"""Godot's import of a stage redoes only what the change made: the stage and the
baseline start with the project's import cache, an import that is current is not
run again, and an import past its limit is a problem of its own beside the
other checks."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from joinery import import_cache, submission
from joinery.authoring import WorkerTimeout
from joinery.gateway import ProjectGateway
from joinery.mcp_runtime import ServerSpec
from joinery.runner import Result
from joinery.transactions import FileTransaction, seed_import_cache
from joinery.turn import Problem
from tests.test_crew import FakeConnection, project
from tests.test_scene_tests_baseline import _project

__all__ = ["project"]  # the fixture, used by name


def _cached_project(root: Path) -> None:
    (root / "project.godot").write_text("config_version=5\n")
    (root / "art").mkdir()
    (root / "art/crate.png").write_bytes(b"png")
    (root / ".godot/imported").mkdir(parents=True)
    (root / ".godot/imported/crate.png-1.ctex").write_bytes(b"ctex")
    (root / "art/crate.png.import").write_text(
        'dest_files=["res://.godot/imported/crate.png-1.ctex"]\n'
    )


def test_a_stage_starts_with_the_project_import_cache(tmp_path: Path) -> None:
    _cached_project(tmp_path)
    tx = FileTransaction(tmp_path)
    tx.begin("Crate")
    assert (tx.stage / ".godot/imported/crate.png-1.ctex").read_bytes() == b"ctex"
    # Never part of the change.
    (tx.stage / ".godot/imported/new.ctex").write_bytes(b"x")
    assert tx.diff() == []
    assert import_cache.stale(tx.stage) == []


def test_seeding_keeps_an_existing_cache_and_needs_one_to_copy(tmp_path: Path) -> None:
    source, target = tmp_path / "a", tmp_path / "b"
    source.mkdir()
    target.mkdir()
    assert not seed_import_cache(source, target)
    (source / ".godot").mkdir()
    (source / ".godot/uid_cache.bin").write_bytes(b"one")
    (target / ".godot").mkdir()
    (target / ".godot/uid_cache.bin").write_bytes(b"two")
    assert not seed_import_cache(source, target)
    assert (target / ".godot/uid_cache.bin").read_bytes() == b"two"


def test_the_import_limit_is_set_by_the_project_or_scales_under_emulation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("joinery.submission.platform.machine", lambda: "x86_64")
    assert submission.import_seconds(tmp_path) == submission.IMPORT_SECONDS
    monkeypatch.setattr("joinery.submission.platform.machine", lambda: "arm64")
    assert submission.import_seconds(tmp_path) == (
        submission.IMPORT_SECONDS * submission.EMULATED_FACTOR
    )
    (tmp_path / "joinery.toml").write_text("[checks]\nimport_seconds = 900\n")
    assert submission.import_seconds(tmp_path) == 900


class Offline:
    """run_offline for the checks: imports that may run past the limit."""

    def __init__(self, *, stage_hangs: bool = False, base_hangs: bool = False):
        self.stage_hangs = stage_hangs
        self.base_hangs = base_hangs
        self.runs: list[tuple[str, str, int]] = []
        self.seeded: list[bool] = []

    async def __call__(
        self, argv: list[str], root: Path, seconds: int, **_k: Any
    ) -> Result:
        where = "baseline" if root.name.startswith("baseline-") else "stage"
        if "--import" in argv:
            self.runs.append((where, "import", seconds))
            self.seeded.append((root / ".godot").is_dir())
            if (where == "stage" and self.stage_hangs) or (
                where == "baseline" and self.base_hangs
            ):
                raise WorkerTimeout(seconds, "importing crate.png\n")
            return Result(True, "", "", 0.0)
        if "-s" in argv:
            script = argv[argv.index("-s") + 1].removeprefix("res://")
            self.runs.append((where, script, seconds))
            if where == "stage" and script == "tests/test_lever.gd":
                return Result(False, "FAIL: lever", "", 0.0)
            return Result(True, "PASS", "", 0.0)
        return Result(True, "", "", 0.0)


def _staged(tmp_path: Path) -> FileTransaction:
    _project(tmp_path)
    (tmp_path / ".godot").mkdir()
    (tmp_path / ".godot/uid_cache.bin").write_bytes(b"uids")
    (tmp_path / "joinery.toml").write_text("[checks]\nimport_seconds = 30\n")
    tx = FileTransaction(tmp_path)
    tx.begin("Lever")
    (tx.stage / "game/lever.gd").write_text("extends Node\nvar pulled := true\n")
    return tx


def _checks(tx: FileTransaction, **options: Any) -> list[Problem]:
    return asyncio.run(
        submission.checks(
            tx,
            [{"path": "game/lever.gd"}],
            rules=True,
            godot=True,
            scene_tests=submission.scene_tree_tests(tx.stage),
            **options,
        )
    )


def test_an_import_past_its_limit_is_a_problem_beside_the_other_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _staged(tmp_path)
    fake = Offline(stage_hangs=True)
    monkeypatch.setattr("joinery.submission.run_offline", fake)

    async def rules(*_a: Any, **_k: Any) -> list[str]:
        return ["art/crate.png: 4096 px is over the 1024 px limit"]

    monkeypatch.setattr("joinery.submission.check_rules", rules)
    problems = _checks(tx)
    assert sorted(p.check for p in problems) == ["art_bible", "godot_import"]
    (imported,) = [p for p in problems if p.check == "godot_import"]
    assert "ran past 30 s" in imported.message
    assert "import_seconds" in imported.message
    assert "importing crate.png" in imported.message
    assert imported.items[0].rule == "godot_import.timed_out"
    assert ("stage", "import", 30) in fake.runs


def test_tests_on_a_stage_imported_as_it_is_do_not_import_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _staged(tmp_path)
    fake = Offline()
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    asyncio.run(
        submission.checks(
            tx,
            [{"path": "game/lever.gd"}],
            rules=False,
            godot=False,
            scene_tests=["tests/test_door.gd"],
            current=True,
        )
    )
    assert ("stage", "import", 30) not in fake.runs
    assert ("stage", "tests/test_door.gd", fake.runs[0][2]) in fake.runs


def test_the_baseline_starts_with_the_import_cache_and_survives_a_slow_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _staged(tmp_path)
    fake = Offline(base_hangs=True)
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    problems = asyncio.run(
        submission.run_scene_tests(tx.stage, ["tests/test_lever.gd"], tx=tx)
    )
    # The baseline import ran out of time, yet its test still ran and answered.
    assert ("baseline", "import", 30) in fake.runs
    assert any(w == "baseline" and s == "tests/test_lever.gd" for w, s, _ in fake.runs)
    assert all(fake.seeded)
    assert [p.paths for p in problems] == [["tests/test_lever.gd"]]


def test_loadable_marks_change_with_an_export_and_not_with_a_source(
    tmp_path: Path,
) -> None:
    _cached_project(tmp_path)
    (tmp_path / "art/sources").mkdir()
    (tmp_path / "art/sources/.gdignore").write_text("")
    before = import_cache.loadable_marks(tmp_path)
    assert "art/crate.png" in before and "art/crate.png.import" in before
    (tmp_path / "art/sources/crate.blend").write_bytes(b"blend")
    assert import_cache.loadable_marks(tmp_path) == before
    time.sleep(0.01)
    (tmp_path / "art/crate.png.import").write_text("dest_files=[]\nloop=true\n")
    assert import_cache.loadable_marks(tmp_path) != before


class PaintOps(FakeConnection):
    """Blender's paint_map: writes the files its call names, as an export would."""

    def __init__(self, spec: ServerSpec, project: Path) -> None:
        super().__init__(spec, project)
        self.tools = {"paint_map": object()}

    async def call(
        self, tool: str, arguments: dict[str, Any], timeout: float = 0
    ) -> CallToolResult:
        for path in arguments.get("writes", []):
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"painted " + str(time.monotonic_ns()).encode())
        return CallToolResult(content=[TextContent(type="text", text="ok")])


def test_a_native_op_imports_only_when_it_changed_what_godot_loads(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    imports: list[dict[str, Any]] = []

    async def offline(argv: list[str], root: Path, *_a: Any, **k: Any) -> Result:
        assert "--import" in argv
        imports.append(k)
        # As Godot would: each texture gets its record and imported copy.
        for png in root.glob("art/*.png"):
            dest = f".godot/imported/{png.name}-1.ctex"
            (root / ".godot/imported").mkdir(parents=True, exist_ok=True)
            (root / dest).write_bytes(b"ctex")
            png.with_name(png.name + ".import").write_text(
                f'dest_files=["res://{dest}"]\n'
            )
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.gateway.run_offline", offline)
    monkeypatch.setattr("joinery.gateway.MCPConnection", PaintOps)
    (project / "art/sources").mkdir(parents=True)
    (project / "art/sources/.gdignore").write_text("")
    (project / ".godot").mkdir()

    async def go() -> None:
        gateway = ProjectGateway(project)

        async def connection(server: str) -> PaintOps:
            spec = ServerSpec(name=server, command="unused", source="first-party")
            return PaintOps(spec, gateway.project)

        monkeypatch.setattr(gateway, "connection", connection)

        async def paint(*paths: str) -> None:
            await gateway.call(
                "joinery-blender-ops",
                "paint_map",
                {"writes": [str(project / p) for p in paths]},
            )

        try:
            await paint("art/sources/crate.blend", "art/crate.png")
            assert len(imports) == 1
            # Its own worker: never the warm one Blender keeps running in.
            assert imports[0].get("warm") is False
            await paint("art/sources/crate.blend")  # only the source: no import
            assert len(imports) == 1
            await paint("art/sources/crate.blend", "art/crate.png")
            assert len(imports) == 2
        finally:
            await gateway.close()

    asyncio.run(go())


def test_the_stage_import_asks_a_busy_docker_again(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("joinery.submission.WORKER_RETRY_DELAYS", (0.0, 0.0, 0.0))
    tries: list[int] = []

    async def offline(argv: list[str], root: Path, *_a: Any, **_k: Any) -> Result:
        tries.append(1)
        if len(tries) < 3:
            return Result(False, "", "error during connect: Docker is busy", 0.0)
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.gateway.run_offline", offline)

    async def go() -> None:
        gateway = ProjectGateway(project)
        tx = FileTransaction(project)
        tx.begin("Crate")
        gateway.transaction = tx
        try:
            await gateway.import_stage(tx)
            assert gateway.import_current(tx)
        finally:
            await gateway.close()

    asyncio.run(go())
    assert len(tries) == 3
