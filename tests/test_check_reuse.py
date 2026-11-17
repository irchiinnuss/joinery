"""A change checked again (the next precheck, the submit after it) re-runs only what
its edits since the last check can affect: the art bible measures only changed
files."""

from __future__ import annotations

import asyncio
from pathlib import Path

from joinery.bible_rules import check_rules
from joinery.measure_cache import MeasureCache
from joinery.runner import Result
from tests.test_bible_rules import png, v2_stage


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
