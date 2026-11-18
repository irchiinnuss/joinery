"""Godot's checks run on a copy of the stage inside their worker (check_mirror).

Over Docker Desktop's file sharing Godot read a stage several times slower than
from the worker's own disk, so the checks copy the stage in once and run there.
The same commands run on the same files: the checks are not weakened, and what
the import writes still reaches the stage.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from joinery import check_mirror, submission
from joinery.authoring import WorkerTimeout
from joinery.gateway import ProjectGateway
from joinery.runner import Result
from joinery.transactions import FileTransaction

WORKER = os.environ.get("JOINERY_WORKER_TESTS") == "1" and shutil.which("docker")
needs_worker = pytest.mark.skipif(not WORKER, reason="needs JOINERY_WORKER_TESTS=1")


# --- What is sent, and what is removed -----------------------------------------------


def test_the_stage_is_listed_without_what_no_godot_run_reads(tmp_path: Path) -> None:
    (tmp_path / ".godot/imported").mkdir(parents=True)
    (tmp_path / ".godot/imported/tv.scn").write_bytes(b"12345")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text("ref")
    (tmp_path / "game").mkdir()
    (tmp_path / "game/player.gd").write_bytes(b"extends Node\n")
    os.utime(tmp_path / "game/player.gd", (1000.7, 1000.7))
    marks = check_mirror.host_marks(tmp_path)
    assert marks == {
        ".godot/imported/tv.scn": (
            5,
            int((tmp_path / ".godot/imported/tv.scn").stat().st_mtime),
        ),
        "game/player.gd": (13, 1000),
    }


def test_the_copy_listing_is_read_and_compared() -> None:
    listing = b"game/player.gd\x0013\x001000.9000000000\x00art/tv.glb\x005\x0012.5\x00"
    copy = check_mirror.parse_marks(listing)
    assert copy == {"game/player.gd": (13, 1000), "art/tv.glb": (5, 12)}
    host = {"game/player.gd": (14, 1001), "game/new.gd": (1, 5)}
    send, gone = check_mirror.differ(host, copy)
    assert send == ["game/new.gd", "game/player.gd"]
    assert gone == ["art/tv.glb"]
    assert check_mirror.differ(copy, copy) == ([], [])


def test_linux_keeps_the_bind_mount_unless_told(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(check_mirror.SETTING, raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    assert not check_mirror.wanted()
    monkeypatch.setattr("sys.platform", "darwin")
    assert check_mirror.wanted()
    monkeypatch.setenv(check_mirror.SETTING, "0")
    assert not check_mirror.wanted()
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv(check_mirror.SETTING, "1")
    assert check_mirror.wanted()


# --- Which runs go to the copy -------------------------------------------------------


class FakeMirror(check_mirror.Mirror):
    """A mirror that records its runs instead of starting a worker."""

    def __init__(
        self,
        root: Path,
        lost: bool = False,
        image: str = "joinery-godot-worker",
        parallel: int = 1,
    ) -> None:
        super().__init__(root, image, parallel)
        self.started = True
        self.lost = lost
        self.runs: list[tuple[list[str], dict[str, Any]]] = []
        self.closed = False

    async def run(
        self, argv: list[str], seconds: float = 600, **options: Any
    ) -> Result:
        if self.lost:
            raise check_mirror.MirrorLost("No such container")
        self.runs.append((argv, options))
        return Result(True, "", "", 0.0)

    async def sync(self) -> None:
        return None

    async def close(self) -> None:
        self.started = False
        self.closed = True


def test_runs_on_the_stage_go_to_the_copy_and_others_to_the_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mounted: list[Path] = []

    async def offline(_argv: list[str], root: Path, *_a: Any, **_k: Any) -> Result:
        mounted.append(root)
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    mirror = FakeMirror(tmp_path / "stage")
    run = submission.on_copy(mirror, keep=True)
    assert run is not None and submission.on_copy(None) is None

    async def go() -> None:
        await run(["godot", "--import"], tmp_path / "stage", 60, stall=3, warm=False)
        # The project as it was (a baseline copy) is not the mirror's.
        await run(["godot", "-s", "t.gd"], tmp_path / "baseline", 60)

    asyncio.run(go())
    assert mirror.runs == [
        (["godot", "--import"], {"stall": 3.0, "keep": True, "own_home": False})
    ]
    assert mounted == [tmp_path / "baseline"]


def test_a_lost_worker_runs_on_the_mount_instead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mounted: list[Path] = []

    async def offline(_argv: list[str], root: Path, *_a: Any, **_k: Any) -> Result:
        mounted.append(root)
        return Result(True, "imported", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    mirror = FakeMirror(tmp_path, lost=True)
    run = submission.on_copy(mirror)
    assert run is not None

    async def go(runner: Any) -> Result:
        found: Result = await runner(["godot", "--import"], tmp_path, 60)
        return found

    got: Result = asyncio.run(go(run))
    assert got.output == "imported" and mounted == [tmp_path] and mirror.closed


def test_the_checks_import_and_test_on_one_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "tests").mkdir()
    for name in ("test_a.gd", "test_b.gd"):
        (tmp_path / "tests" / name).write_text(
            "extends SceneTree\n\nfunc _init() -> void:\n\tquit()\n"
        )
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))

    async def offline(*_a: Any, **_k: Any) -> Result:
        raise AssertionError("every run of the stage goes to the copy")

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    opened: list[FakeMirror] = []

    async def open_mirror(root: Path, image: str, parallel: int = 1) -> FakeMirror:
        opened.append(FakeMirror(root))
        return opened[-1]

    monkeypatch.setattr("joinery.check_mirror.open_mirror", open_mirror)
    gateway = ProjectGateway(tmp_path)
    tx = FileTransaction(tmp_path)
    tx.begin("Lever")
    gateway.transaction = tx
    (tx.stage / "lever.gd").write_text("extends Node\n")

    async def go() -> submission.Validation:
        try:
            return await submission.validate(gateway, tx, set())
        finally:
            await gateway.close()

    found = asyncio.run(go())
    assert found.problems == []
    (mirror,) = opened
    assert mirror.root == tx.stage and mirror.closed
    importing, *tests = mirror.runs
    assert "--import" in importing[0] and importing[1]["keep"]
    assert sorted(argv[argv.index("-s") + 1] for argv, _o in tests) == [
        "res://tests/test_a.gd",
        "res://tests/test_b.gd",
    ]
    assert all(options["own_home"] and not options["keep"] for _a, options in tests)


# --- A real worker -------------------------------------------------------------------


@needs_worker
def test_a_real_copy_runs_brings_back_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(check_mirror.SETTING, "1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("hi\n")
    (tmp_path / "sub/b.txt").write_text("x\n")

    async def go() -> None:
        mirror = await check_mirror.open_mirror(tmp_path, "joinery-godot-worker")
        assert mirror is not None
        try:
            assert set(await mirror.marks()) == {"a.txt", "sub/b.txt"}
            got = await mirror.run(
                ["sh", "-c", "cat a.txt; echo new > sub/c.txt; rm sub/b.txt"],
                keep=True,
            )
            assert got.ok and got.stdout == "hi\n"
            assert (tmp_path / "sub/c.txt").read_text() == "new\n"
            assert not (tmp_path / "sub/b.txt").exists()
            with pytest.raises(WorkerTimeout):
                await mirror.run(["sh", "-c", "sleep 100 & sleep 100"], 2)
            count = (
                "for f in /proc/[0-9]*/cmdline; do tr '\\0' ' ' < $f; echo; done"
                " | grep -c '^sleep 100 $'"
            )
            left = await mirror.run(["sh", "-c", count])
            assert left.stdout.strip() == "0", "a stopped run leaves nothing running"
            alone = await mirror.run(["sh", "-c", "echo $XDG_DATA_HOME"], own_home=True)
            assert alone.stdout.startswith("/tmp/joinery-run-")
            (tmp_path / "a.txt").write_text("changed\n")
            (tmp_path / "sub/c.txt").unlink()
            await mirror.sync()
            assert set(await mirror.marks()) == {"a.txt"}
            assert (await mirror.run(["cat", "a.txt"])).stdout == "changed\n"
        finally:
            await mirror.close()

    asyncio.run(go())


# --- One copy for a change -----------------------------------------------------------


def test_a_change_keeps_one_copy_and_sends_only_what_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[FakeMirror] = []
    synced: list[str] = []

    async def open_mirror(root: Path, image: str, parallel: int = 1) -> FakeMirror:
        opened.append(FakeMirror(root, image=image, parallel=parallel))
        return opened[-1]

    async def sync(self: FakeMirror) -> None:
        synced.append(self.name)

    monkeypatch.setattr("joinery.check_mirror.open_mirror", open_mirror)
    monkeypatch.setattr(FakeMirror, "sync", sync)
    held = check_mirror.Held()

    async def go() -> None:
        await held.get("tx1", tmp_path, "godot", 4)
        await held.get("tx1", tmp_path, "godot", 4)
        # Another change (or another image) gets a copy of its own.
        await held.get("tx2", tmp_path, "godot", 4)
        await held.close()

    asyncio.run(go())
    first, other = opened
    assert synced == [first.name], "the second check only sent what changed"
    assert first.closed and other.closed


def test_the_stage_import_runs_on_the_change_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    opened: list[FakeMirror] = []

    async def open_mirror(root: Path, image: str, parallel: int = 1) -> FakeMirror:
        opened.append(FakeMirror(root, image=image, parallel=parallel))
        return opened[-1]

    async def offline(*_a: Any, **_k: Any) -> Result:
        raise AssertionError("the import runs on the copy")

    monkeypatch.setattr("joinery.check_mirror.open_mirror", open_mirror)
    monkeypatch.setattr("joinery.gateway.run_offline", offline)
    gateway = ProjectGateway(tmp_path)
    tx = FileTransaction(tmp_path)
    tx.begin("Lever")
    gateway.transaction = tx

    async def go() -> None:
        try:
            await gateway.import_stage(tx)
            await gateway.import_stage(tx)
        finally:
            gateway.transaction = None
            await gateway.close()

    asyncio.run(go())
    (mirror,) = opened
    assert [options["keep"] for _argv, options in mirror.runs] == [True, True]
    assert mirror.closed, "the copy goes with the gateway"


def test_a_large_stage_stays_on_the_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(check_mirror.SETTING, "1")
    monkeypatch.setattr("joinery.check_mirror.MAX_BYTES", 10)
    (tmp_path / "big.png").write_bytes(b"x" * 11)
    started: list[str] = []

    async def docker(self: check_mirror.Mirror, *argv: str, **_k: Any) -> bytes:
        started.append(argv[0])
        return b""

    monkeypatch.setattr(check_mirror.Mirror, "_docker", docker)
    assert asyncio.run(check_mirror.open_mirror(tmp_path, "godot")) is None
    assert started == [], "no worker started for it"
