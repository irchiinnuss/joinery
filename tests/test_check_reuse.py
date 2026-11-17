"""A change checked again (the next precheck, the submit after it) re-runs only what
its edits since the last check can affect: the art bible measures only changed
files, and a stage whose only edits since its last clean import are scripts is
compiled rather than imported again."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from joinery import submission
from joinery.bible_rules import check_rules
from joinery.gateway import ProjectGateway
from joinery.measure_cache import MeasureCache
from joinery.runner import Result
from joinery.transactions import FileTransaction
from tests.test_bible_rules import png, v2_stage
from tests.test_crew import project

__all__ = ["project"]  # the fixture, used by name


class Worker:
    """The worker's per-file loops: one block per file, from the file's bytes."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, argv: list[str], root: Path) -> Result:
        self.calls.append(argv[4:])
        lines: list[str] = []
        for name in argv[4:]:
            data = (root / name).read_bytes()
            lines.append(f"JOINERY_FILE:{name}")
            if name.endswith(".png"):
                # Dark samples when the file says so, mid grey otherwise.
                lines.append(("000000ff" if b"dark" in data else "786e64ff") * 4)
            elif name.endswith(".wav"):
                lines += [
                    "  Summary:",
                    "    I:         -20.0 LUFS",
                    "    Peak:  -3.0 dBFS",
                ]
        return Result(True, "\n".join(lines) + "\n", "", 0.0)


def _albedo(root: Path, name: str, dark: bool = False) -> None:
    (root / name).write_bytes(png(4, 4) + (b"dark" if dark else b""))


def test_the_bible_measures_only_files_changed_since_the_last_check(
    tmp_path: Path,
) -> None:
    (tmp_path / "stage").mkdir()
    root = v2_stage(tmp_path / "stage", {})
    (root / "art/textures").mkdir(parents=True)
    paths = ["art/textures/wall_albedo.png", "art/textures/door_albedo.png"]
    for name in paths:
        _albedo(root, name)
    worker = Worker()
    cache_file = tmp_path / "measures.json"

    def round_() -> list[str]:
        kept = MeasureCache(cache_file, "creative")
        found = asyncio.run(check_rules(root, paths, kept.wrap(worker)))
        kept.save()
        return found

    assert round_() == []
    assert worker.calls == [sorted(paths)]
    assert round_() == []  # the submit after a clean precheck: nothing measured
    assert worker.calls == [sorted(paths)]
    _albedo(root, paths[0], dark=True)
    found = round_()
    assert worker.calls[-1] == [paths[0]]
    # The same findings as measuring everything afresh.
    assert found == asyncio.run(check_rules(root, paths, Worker()))
    assert found and found[0].startswith(paths[0])


def test_a_failed_run_or_an_empty_block_is_measured_again(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"a")
    calls: list[list[str]] = []

    async def failing(argv: list[str], _root: Path) -> Result:
        calls.append(argv[4:])
        return Result(False, "JOINERY_FILE:a.png\n786e64ff\n", "", 0.0)

    async def empty(argv: list[str], _root: Path) -> Result:
        calls.append(argv[4:])
        return Result(True, "JOINERY_FILE:a.png\n\n", "", 0.0)

    kept = MeasureCache(None, "creative")
    argv = ["sh", "-c", "loop", "sh", "a.png"]
    for runner in (failing, failing, empty, empty):
        asyncio.run(kept.wrap(runner)(argv, tmp_path))
    assert len(calls) == 4
    assert kept.entries == {}


def test_a_blend_is_measured_again_when_any_blend_changed(tmp_path: Path) -> None:
    (tmp_path / "a.blend").write_bytes(b"a")
    (tmp_path / "b.blend").write_bytes(b"b")
    calls: list[list[str]] = []

    async def blender(argv: list[str], _root: Path) -> Result:
        calls.append(argv[4:])
        said = [f"JOINERY_FILE:{f}\nJOINERY_MEASURE:{{}}" for f in argv[4:]]
        return Result(True, "\n".join(said), "", 0.0)

    kept = MeasureCache(None, "creative")
    measure = kept.wrap(blender)
    argv = ["sh", "-c", "loop", "script", "a.blend"]
    asyncio.run(measure(argv, tmp_path))
    asyncio.run(measure(argv, tmp_path))
    assert calls == [["a.blend"]]
    # a.blend may link b.blend's data: its measurement is not reused.
    (tmp_path / "b.blend").write_bytes(b"b2")
    asyncio.run(measure(argv, tmp_path))
    assert calls == [["a.blend"], ["a.blend"]]


def test_a_command_that_is_not_a_per_file_loop_always_runs(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    async def mix(argv: list[str], _root: Path) -> Result:
        calls.append(argv)
        return Result(True, "JOINERY_FILE:mix\nI: -14 LUFS\n", "", 0.0)

    measure = MeasureCache(None, "creative").wrap(mix)
    asyncio.run(measure(["sh", "-c", "mix script"], tmp_path))
    asyncio.run(measure(["sh", "-c", "mix script"], tmp_path))
    assert len(calls) == 2


def _game(root: Path) -> None:
    (root / "project.godot").write_text(
        'config_version=5\n\n[autoload]\n\nScore="*res://game/score.gd"\n'
    )
    (root / "game").mkdir(exist_ok=True)
    (root / "game/score.gd").write_text("extends Node\nvar points := 0\n")
    (root / "game/door.gd").write_text(
        "class_name Door\nextends Node3D\nfunc open() -> void:\n\tScore.points += 1\n"
    )


def test_only_scripts_changed_since_the_import_are_compiled_not_imported(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _game(project)
    runs: list[list[str]] = []

    async def offline(argv: list[str], root: Path, *_a: Any, **_k: Any) -> Result:
        runs.append(argv)
        if argv[:2] == ["sh", "-c"]:
            said = [f"JOINERY_FILE:{f}\nGodot Engine v4.7" for f in argv[4:]]
            if (
                "game/door.gd" in argv
                and "broken" in (root / "game/door.gd").read_text()
            ):
                said.append("SCRIPT ERROR: Parse Error: Expected end of statement.")
            # Naming an autoload is fine outside a SceneTree test.
            said.append("SCRIPT ERROR: Compile Error: Identifier not found: Score")
            return Result(True, "\n".join(said), "", 0.0)
        return Result(True, "", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    monkeypatch.setattr("joinery.gateway.run_offline", offline)
    monkeypatch.setattr("joinery.submission.check_image", lambda _s: "godot-worker")

    async def go() -> None:
        gateway = ProjectGateway(project)
        tx = FileTransaction(project)
        tx.begin("Door")
        gateway.transaction = tx
        try:
            door = tx.stage / "game/door.gd"
            door.write_text(door.read_text() + "\nvar shut := true\n")
            first = await submission.validate(gateway, tx, set())
            assert first.problems == []
            assert sum("--import" in argv for argv in runs) == 1
            assert gateway.scripts_since_import(tx) is None  # just imported
            # The agent fixes one line of a script: compiled, not imported.
            door.write_text(door.read_text() + "var locked := false\n")
            assert gateway.scripts_since_import(tx) == ["game/door.gd"]
            again = await submission.validate(gateway, tx, set())
            assert again.problems == []
            assert "Godot import" in again.ran
            assert sum("--import" in argv for argv in runs) == 1
            assert runs[-1][4:] == ["game/door.gd"]
            # A broken script is the import's problem, as before.
            door.write_text(door.read_text() + "var broken := \n")
            broken = await submission.validate(gateway, tx, set())
            assert [p.check for p in broken.problems] == ["godot_import"]
            assert "Expected end of statement" in broken.problems[0].message
            # A changed class header, a new file or an asset: Godot imports again.
            door.write_text("class_name Gate\nextends Node3D\n")
            assert gateway.scripts_since_import(tx) is None
            door.write_text(door.read_text().replace("Gate", "Door"))
            (tx.stage / "game/lamp.gd").write_text("extends Node\n")
            assert gateway.scripts_since_import(tx) is None
        finally:
            await gateway.close()

    asyncio.run(go())
