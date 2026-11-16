"""Run a creative tool, in the sandbox or on the host.

Recipes replay by driving real tools, and those tools live in the sandbox image
while Joinery's own Python lives outside it. So the host orchestrates and the
container executes: `joinery rebuild` resolves a recipe here and runs each tool
step through a `Runner`.

Two runners, same interface:

* `SandboxRunner` runs the tool in the image with `--network none`. This is the
  default and the one that actually guarantees Rule Zero, because a worker with
  no network cannot reach a generative service.
* `HostRunner` runs the tool from PATH, for live mode where the user wants to
  watch the work happen, and for a machine with the tools installed natively.

Nothing here knows what a recipe means; it only knows how to run a command
against a project directory.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from joinery import docker_cli
from joinery.worker_identity import docker_owner_args
from joinery.worker_platform import platform_for

SANDBOX_IMAGE = "joinery-creative-worker"
#: Where the project is mounted inside the container.
WORKDIR = "/work"
DEFAULT_TIMEOUT = 900


@dataclass(frozen=True)
class Result:
    """What a tool run produced."""

    ok: bool
    stdout: str
    stderr: str
    #: Wall-clock seconds, so recipes can report honest latency.
    seconds: float

    @property
    def output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


class Runner(Protocol):
    @property
    def name(self) -> str:
        """Short label for reports, e.g. "sandbox"."""
        ...

    def available(self) -> bool:
        """Whether this runner can run anything at all."""
        ...

    def run(
        self, argv: list[str], project: Path, timeout: int = DEFAULT_TIMEOUT
    ) -> Result:
        """Run a tool command with `project` as the working directory."""
        ...


def _timed(cmd: list[str], timeout: int, cwd: Path | None = None) -> Result:
    start = time.monotonic()
    try:
        done = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        why = f"timed out after {timeout}s"
        if cmd[:1] == ["docker"] and not docker_cli.responding():
            # A hung Docker, not a slow tool: say what to do about it.
            why = docker_cli.NOT_RESPONDING
        return Result(False, "", why, time.monotonic() - start)
    except OSError as exc:
        return Result(False, "", str(exc), time.monotonic() - start)
    return Result(
        done.returncode == 0, done.stdout, done.stderr, time.monotonic() - start
    )


@dataclass(frozen=True)
class SandboxRunner:
    """Run the tool inside the sandbox image, offline."""

    name: str = "sandbox"
    image: str = SANDBOX_IMAGE

    def available(self) -> bool:
        if shutil.which("docker") is None:
            return False
        got = _timed(
            ["docker", "image", "inspect", self.image], timeout=docker_cli.QUICK
        )
        if got.stderr == docker_cli.NOT_RESPONDING:
            raise docker_cli.DockerNotResponding("docker image inspect")
        return got.ok

    def run(
        self, argv: list[str], project: Path, timeout: int = DEFAULT_TIMEOUT
    ) -> Result:
        # --network none is the point of the sandbox, not a detail.
        cmd = [
            "docker",
            "run",
            "--rm",
            *docker_owner_args(),
            "--platform",
            platform_for(self.image),
            "--network",
            "none",
            "--label",
            "joinery.scope=" + os.environ.get("JOINERY_WORKER_SCOPE", "recipe-cli"),
            "-v",
            f"{project.resolve()}:{WORKDIR}",
            "-w",
            WORKDIR,
            self.image,
            *argv,
        ]
        return _timed(cmd, timeout)


#: The display and runtime folder the sandbox entrypoint sets up for each command.
_SESSION_ENV = ("DISPLAY=:99", "XDG_RUNTIME_DIR=/tmp/runtime-joiner")


class WorkerPool:
    """Long-lived worker containers, for many tool runs in a row.

    Starting a container (under emulation on an Arm Mac) costs far more than most
    recipe steps. A pool has a few slots, each a host folder that its workers
    mount at ``/work`` (recipe scripts work there), with one worker per image
    started the first time a slot needs it and kept, offline as ever. A step runs
    with ``docker exec`` in the worker of its slot. ``close`` ends them all.
    """

    def __init__(self, root: Path, slots: int = 1, scope: str = "recipe-cli") -> None:
        self.root = root.resolve()
        self.scope = scope
        self.slots = [self.root / f"slot-{n}" for n in range(max(1, slots))]
        for slot in self.slots:
            slot.mkdir(parents=True, exist_ok=True)
        self._free: queue.SimpleQueue[Path] = queue.SimpleQueue()
        for slot in self.slots:
            self._free.put(slot)
        self._containers: dict[
            tuple[Path, str], tuple[str, subprocess.Popen[bytes]]
        ] = {}
        self._lock = threading.Lock()

    @contextmanager
    def slot(self) -> Iterator[Path]:
        """A slot folder for one project at a time. It keeps what the last user left
        (the caller brings it up to date: its workers see the same folders)."""
        slot = self._free.get()
        try:
            yield slot
        finally:
            self._free.put(slot)

    def owns(self, project: Path) -> Path | None:
        """The slot ``project`` is, if it is one of this pool's."""
        where = project.resolve()
        return where if where in self.slots else None

    def container(self, slot: Path, image: str) -> str:
        with self._lock:
            known = self._containers.get((slot, image))
            if known is not None:
                return known[0]
            cidfile = self.root / f"{slot.name}-{len(self._containers)}.cid"
            # Attached, its main process reading stdin: when Joinery stops for any
            # reason the pipe closes, the worker ends and --rm removes it.
            process = subprocess.Popen(
                [
                    "docker",
                    "run",
                    "--interactive",
                    "--rm",
                    "--cidfile",
                    str(cidfile),
                    *docker_owner_args(),
                    "--platform",
                    platform_for(image),
                    "--network",
                    "none",
                    "--label",
                    "joinery.scope=" + self.scope,
                    "-v",
                    f"{slot}:{WORKDIR}",
                    "-w",
                    WORKDIR,
                    image,
                    "sh",
                    "-c",
                    "cat >/dev/null",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline and process.poll() is None:
                container = cidfile.read_text().strip() if cidfile.is_file() else ""
                if container and _timed(["docker", "exec", container, "true"], 60).ok:
                    self._containers[(slot, image)] = (container, process)
                    return container
                time.sleep(0.2)
            process.kill()
            error = (
                process.stderr.read().decode(errors="replace") if process.stderr else ""
            )
            raise RuntimeError(f"could not start a {image} worker: {error[-400:]}")

    def close(self) -> None:
        with self._lock:
            running, self._containers = list(self._containers.values()), {}
        for container, process in running:
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                _timed(["docker", "rm", "--force", container], timeout=120)
                process.kill()


@dataclass(frozen=True)
class SessionRunner:
    """A `SandboxRunner` that runs in its pool's worker when the project is one of
    the pool's slots; for any other project it starts a container as usual."""

    pool: WorkerPool
    name: str = "sandbox"
    image: str = SANDBOX_IMAGE

    def available(self) -> bool:
        return SandboxRunner(image=self.image).available()

    def run(
        self, argv: list[str], project: Path, timeout: int = DEFAULT_TIMEOUT
    ) -> Result:
        slot = self.pool.owns(project)
        if slot is None:
            return SandboxRunner(name=self.name, image=self.image).run(
                argv, project, timeout
            )
        start = time.monotonic()
        try:
            container = self.pool.container(slot, self.image)
        except RuntimeError as exc:
            return Result(False, "", str(exc), time.monotonic() - start)
        env = [part for setting in _SESSION_ENV for part in ("--env", setting)]
        cmd = ["docker", "exec", *docker_owner_args(), *env, "-w", WORKDIR, container]
        return _timed([*cmd, *argv], timeout)


@dataclass(frozen=True)
class HostRunner:
    """Run the tool from PATH, for live mode on the user's desktop."""

    name: str = "host"

    def available(self) -> bool:
        return True

    def run(
        self, argv: list[str], project: Path, timeout: int = DEFAULT_TIMEOUT
    ) -> Result:
        if shutil.which(argv[0]) is None:
            return Result(False, "", f"{argv[0]} not on PATH", 0.0)
        return _timed(argv, timeout, cwd=project)


def pick(prefer: str = "auto") -> Runner:
    """Choose a runner. `auto` takes the sandbox when it is built, else the host."""
    if prefer == "host":
        return HostRunner()
    sandbox = SandboxRunner()
    if prefer == "sandbox":
        return sandbox
    return sandbox
