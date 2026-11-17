"""Godot's checks of a stage run on a copy inside their worker, not over a bind mount.

On Docker Desktop (macOS, Windows) a project mounted into a container is read
through the desktop's file sharing, several times slower than the container's
own disk. Godot reads every script and import record it loads, so a no-op
import of a mid-sized game took 86-139 s over the mount and 38-52 s on a copy,
and a SceneTree test 15-19 s against 4-6 s.

A :class:`Mirror` is one worker holding a copy of the stage at ``/work``: what
the stage has is sent once (only what differs from the copy), each command
runs in it with ``docker exec`` (Godot's import, the GUT tests, the SceneTree
tests side by side), and what the import writes is copied back into the stage,
so the stage ends as a bind-mounted import would have left it. The commands
and the files they read are the same: only where the bytes come from changes.
On Linux a bind mount is the host's own disk, so no mirror is made there.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path, PurePosixPath

from joinery import worker_platform
from joinery.authoring import WorkerTimeout, supervise
from joinery.runner import Result
from joinery.worker_identity import docker_owner_args

#: Where the copy lives in the worker: where the bind mount was, so every
#: command (``--path /work``) runs unchanged.
ROOT = "/work"
#: Folders no Godot run reads, never sent.
EXCLUDED = frozenset({".git", "__pycache__", ".pytest_cache", ".venv"})
#: ``JOINERY_CHECK_MIRROR=0`` keeps the bind mount; ``1`` mirrors on Linux too.
SETTING = "JOINERY_CHECK_MIRROR"
#: Seconds the worker may take to start, and to list or move files.
START_SECONDS = 120
COPY_SECONDS = 600
#: A command's process group, so a stopped run leaves nothing running.
JOB = (
    "mkdir -p /tmp/joinery-jobs && exec setsid -w sh -c "
    '\'echo $$ > "/tmp/joinery-jobs/$0"; exec "$@"\' "$0" "$@"'
)
#: Each file of the copy as path, size and modification time.
LIST = (
    f"cd {ROOT} && find . -mindepth 1 \\( -type f -o -type l \\) "
    "-printf '%P\\0%s\\0%T@\\0'"
)

#: What ``docker exec`` says when the worker itself is gone.
LOST = ("Error response from daemon", "No such container", "is not running")

Marks = dict[str, tuple[int, int]]


class MirrorLost(OSError):
    """The mirror's worker stopped: the run never happened (run it on the mount)."""


def wanted() -> bool:
    """Whether checks run on a copy: off on Linux (its bind mounts are the host's
    disk), unless ``JOINERY_CHECK_MIRROR`` says otherwise."""
    setting = os.environ.get(SETTING, "").strip()
    if setting in {"0", "1"}:
        return setting == "1"
    return not sys.platform.startswith("linux")


def host_marks(root: Path) -> Marks:
    """Size and whole-second modification time of each file under ``root``."""
    found: Marks = {}
    for directory, folders, names in os.walk(root):
        folders[:] = [f for f in folders if f not in EXCLUDED]
        base = Path(directory)
        for name in [*names, *(f for f in folders if (base / f).is_symlink())]:
            path = base / name
            try:
                stat = path.lstat()
            except OSError:
                continue
            found[path.relative_to(root).as_posix()] = (
                stat.st_size,
                int(stat.st_mtime),
            )
    return found


def parse_marks(listing: bytes) -> Marks:
    """``LIST``'s output as marks."""
    fields = listing.split(b"\0")
    found: Marks = {}
    for at in range(0, len(fields) - 2, 3):
        name = fields[at].decode(errors="surrogateescape")
        try:
            found[name] = (int(fields[at + 1]), int(float(fields[at + 2])))
        except ValueError:
            continue
    return found


def differ(host: Marks, copy: Marks) -> tuple[list[str], list[str]]:
    """What to send (new or changed on the host) and what to remove from the
    copy (gone from the host)."""
    send = sorted(path for path, mark in host.items() if copy.get(path) != mark)
    gone = sorted(path for path in copy if path not in host)
    return send, gone


def _safe(relative: str) -> bool:
    pure = PurePosixPath(relative)
    return bool(relative) and not pure.is_absolute() and ".." not in pure.parts


def _send(name: str, root: Path, paths: list[str], seconds: float) -> None:
    """Stream ``paths`` (relative to ``root``) into the worker's copy."""
    process = subprocess.Popen(
        ["docker", "exec", "-i", name, "tar", "-C", ROOT, "-xf", "-"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stderr is not None
    try:
        with tarfile.open(
            fileobj=process.stdin, mode="w|", format=tarfile.PAX_FORMAT
        ) as archive:
            for relative in paths:
                with contextlib.suppress(FileNotFoundError):
                    archive.add(root / relative, arcname=relative, recursive=False)
        process.stdin.close()
        process.wait(timeout=seconds)
        error = process.stderr.read()
    except BaseException:
        process.kill()
        process.wait()
        raise
    if process.returncode != 0:
        raise OSError("copying the stage into the worker failed: " + error.decode())


def _fetch(name: str, root: Path, paths: list[str], seconds: float) -> None:
    """Copy ``paths`` from the worker's copy into ``root``, times kept."""
    process = subprocess.Popen(
        ["docker", "exec", "-i", name, "tar", "-C", ROOT, "--null", "-T", "-",
         "-cf", "-"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )  # fmt: skip
    assert process.stdin is not None and process.stdout is not None
    try:
        process.stdin.write(b"".join(p.encode() + b"\0" for p in paths))
        process.stdin.close()
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            # The data filter refuses absolute paths, ".." and links out of root.
            archive.extractall(root, filter="data")
        process.wait(timeout=seconds)
    except BaseException:
        process.kill()
        process.wait()
        raise


class Mirror:
    """One worker holding a copy of ``root`` (a stage), for a check's Godot runs."""

    def __init__(self, root: Path, image: str, parallel: int = 1) -> None:
        self.root = root
        self.image = image
        self.parallel = max(1, parallel)
        self.name = "joinery-check-" + uuid.uuid4().hex
        self.started = False

    async def _docker(
        self, *argv: str, seconds: float = START_SECONDS, stdin: bytes | None = None
    ) -> bytes:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(stdin), seconds)
        except TimeoutError:
            raise WorkerTimeout(seconds) from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode != 0:
            raise OSError(f"docker {argv[0]} failed: " + err.decode(errors="replace"))
        return out

    async def start(self) -> None:
        """Start the worker (idle until a command runs) and copy the stage in."""
        runs_as = await asyncio.to_thread(worker_platform.platform_for, self.image)
        scope = os.environ.get("JOINERY_WORKER_SCOPE", self.name.rsplit("-", 1)[1])
        self.started = True
        await self._docker(
            "run", "-d", "--rm", *docker_owner_args(),
            "--platform", runs_as, "--network", "none", "--name", self.name,
            "--label", "joinery.scope=" + scope,
            # Room for the tests that run side by side, each once its own worker.
            "--pids-limit", str(512 * self.parallel),
            "--memory", f"{4 * self.parallel}g",
            "-w", ROOT, self.image, "sleep", "infinity",
        )  # fmt: skip
        await self.sync()

    async def marks(self) -> Marks:
        """The copy's files as marks."""
        return parse_marks(
            await self._docker(
                "exec", self.name, "sh", "-c", LIST, seconds=COPY_SECONDS
            )
        )

    async def sync(self) -> None:
        """Bring the copy up to date with the stage: send what differs, remove
        what the stage no longer has."""
        host, copy = await asyncio.gather(
            asyncio.to_thread(host_marks, self.root), self.marks()
        )
        send, gone = differ(host, copy)
        if gone:
            await self._docker(
                "exec", "-i", "-w", ROOT, self.name, "xargs", "-0", "rm", "-f", "--",
                stdin=b"".join(p.encode() + b"\0" for p in gone),
                seconds=COPY_SECONDS,
            )  # fmt: skip
        if send:
            await asyncio.to_thread(_send, self.name, self.root, send, COPY_SECONDS)

    async def bring_back(self, before: Marks) -> None:
        """Copy what changed in the copy since ``before`` into the stage, and
        remove there what the copy no longer has: the stage then holds what a
        run on a bind mount would have left."""
        after = await self.marks()
        changed = [p for p, mark in after.items() if before.get(p) != mark and _safe(p)]
        gone = [p for p in before if p not in after and _safe(p)]
        if changed:
            await asyncio.to_thread(_fetch, self.name, self.root, changed, COPY_SECONDS)
        for relative in gone:
            path = self.root / relative
            if path.resolve().is_relative_to(self.root.resolve()):
                with contextlib.suppress(OSError):
                    path.unlink()

    async def run(
        self,
        argv: list[str],
        seconds: float = 600,
        *,
        stall: float = 0.0,
        own_home: bool = False,
        keep: bool = False,
    ) -> Result:
        """Run ``argv`` in the copy, as ``run_offline`` runs it on the stage.

        ``own_home`` gives the run a user data folder and temp folder of its own,
        as a worker of its own had (tests that run side by side never share
        ``user://``). ``keep`` brings what the run wrote back into the stage."""
        before = await self.marks() if keep else {}
        job = uuid.uuid4().hex
        env: list[str] = []
        if own_home:
            home = f"/tmp/joinery-run-{job}"
            env = [
                "-e", f"XDG_DATA_HOME={home}/data",
                "-e", f"XDG_CACHE_HOME={home}/cache",
                "-e", f"TMPDIR={home}/tmp",
            ]  # fmt: skip
            argv = ["sh", "-c", 'mkdir -p "$0/data" "$0/cache" "$0/tmp" && exec "$@"',
                    home, *argv]  # fmt: skip
        start = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", *env, "-w", ROOT, self.name,
            "sh", "-c", JOB, job, *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        try:
            out, err = await supervise(process, seconds, stall)
        finally:
            if process.returncode is None:
                # Stopping the docker client leaves the run going: end its group.
                await self._stop(job)
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        if process.returncode != 0 and any(sign in err for sign in LOST):
            raise MirrorLost(err.strip()[-500:])
        result = Result(process.returncode == 0, out, err, time.monotonic() - start)
        if keep:
            await self.bring_back(before)
        return result

    async def _stop(self, job: str) -> None:
        with contextlib.suppress(OSError, WorkerTimeout):
            await self._docker(
                "exec", self.name, "sh", "-c",
                'p=$(cat "/tmp/joinery-jobs/$0") && kill -9 "-$p"', job,
                seconds=15,
            )  # fmt: skip

    async def close(self) -> None:
        if not self.started:
            return
        self.started = False
        with contextlib.suppress(OSError, WorkerTimeout):
            await self._docker("rm", "--force", self.name, seconds=30)


async def open_mirror(root: Path, image: str, parallel: int = 1) -> Mirror | None:
    """A started mirror of ``root``, or None when checks use the bind mount (on
    Linux, when turned off, or when the worker or the copy failed: the check then
    runs as before, never fails for it)."""
    if not wanted():
        return None
    mirror = Mirror(root, image, parallel)
    try:
        await mirror.start()
    except (OSError, WorkerTimeout, subprocess.SubprocessError, tarfile.TarError):
        await mirror.close()
        return None
    except BaseException:
        await mirror.close()
        raise
    return mirror
