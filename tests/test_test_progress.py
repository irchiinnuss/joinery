"""A test run as a person follows it: the test running now in words, each result as
it lands with a failure's one-line reason, and a summary at the end."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from joinery import submission, test_progress
from joinery.headless import printer
from joinery.runner import Result
from joinery.test_progress import TestRun
from joinery.transactions import FileTransaction

TEST = "extends SceneTree\n\nfunc _initialize() -> void:\n\tquit(0)\n"


@pytest.mark.parametrize(
    ("script", "title", "area"),
    [
        ("tests/test_mower_starts_on_lawn.gd", "mower starts on lawn", ""),
        (
            "res://tests/garden/test_hose_reaches_flowerbed.gd",
            "hose reaches flowerbed",
            "garden",
        ),
        ("tests/scene/doorOpensTest.gd", "door opens", "scene"),
        ("tests/level_one/check_spawn.gd", "spawn", "level one"),
    ],
)
def test_a_test_reads_as_what_it_checks(script: str, title: str, area: str) -> None:
    assert test_progress.title(script) == title
    assert test_progress.area(script) == area


def test_a_failure_says_why_in_one_line_without_full_paths() -> None:
    output = (
        "Godot Engine v4.7\n"
        "loading level\n"
        "FAIL: hose is 2.5 m short of /Users/me/game/levels/flowerbed.tscn\n"
        "more output\n"
    )
    assert test_progress.reason(output) == "hose is 2.5 m short of flowerbed.tscn"
    assert test_progress.reason(
        "SCRIPT ERROR: Invalid call. res://game/mower.gd:12"
    ) == ("Invalid call. mower.gd:12")
    assert test_progress.reason("") == "it exited with an error"
    assert len(test_progress.reason("FAIL: " + "x" * 300)) <= test_progress.REASON_WIDTH


def test_a_run_tells_the_test_now_each_result_and_a_summary() -> None:
    events: list[dict[str, Any]] = []
    scripts = ["tests/test_mower_starts.gd", "tests/test_hose_reaches_flowerbed.gd"]
    run = TestRun("Running the scene tests", scripts, events.append)
    run.started(scripts[0])
    assert events[-1]["now"] == "Checking mower starts"
    assert events[-1]["results"] == [] and not events[-1]["done"]
    run.started(scripts[1])
    run.finished(scripts[0], True)
    assert events[-1]["now"] == "Checking hose reaches flowerbed"
    run.finished(scripts[1], False, "FAIL: the hose is too short")
    run.end()
    last = events[-1]
    assert last["done"] and last["now"] == "" and last["total"] == 2
    assert last["results"][1] == {
        "name": "hose reaches flowerbed",
        "area": "",
        "passed": False,
        "reason": "the hose is too short",
    }
    assert last["summary"] == "2 checks: 1 passed, 1 failed — hose reaches flowerbed"
    every = TestRun("x", ["tests/test_a.gd"], events.append)
    every.finished("tests/test_a.gd", True)
    assert every.summary() == "1 check passed"


def test_tests_the_checks_time_cut_short_are_named_not_failed() -> None:
    events: list[dict[str, Any]] = []
    scripts = [
        "tests/test_mower_starts.gd",
        "tests/test_hose_reaches_flowerbed.gd",
        "tests/test_gate_opens.gd",
        "tests/test_steam.gd",
    ]
    run = TestRun("Running the scene tests", scripts, events.append)
    run.finished(scripts[0], True)
    run.finished(scripts[1], False, "FAIL: the hose is too short")
    run.finished(
        scripts[2], False, "was still running after 90 s when the check's time", True
    )
    run.finished(scripts[3], False, "did not start: the check's time ran out", True)
    run.end()
    results = events[-1]["results"]
    assert results[2]["unfinished"] and not results[2]["passed"]
    assert results[2]["reason"] == "stopped, the check's time ran out"
    assert results[3]["reason"] == "not started, the check's time ran out"
    assert "unfinished" not in results[1]
    assert events[-1]["summary"] == (
        "4 checks: 1 passed; 1 failed — hose reaches flowerbed; "
        "2 not run in time — gate opens, steam"
    )
    printed = test_progress.lines(events[-1], set())
    assert "    – gate opens — stopped, the check's time ran out" in printed
    only = TestRun("x", scripts[:1], events.append)
    only.finished(scripts[0], False, "did not start", True)
    assert only.summary() == "1 check: 0 passed, 1 not run in time — mower starts"


def test_joinery_run_prints_each_result_once_and_the_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    show = printer(as_json=False)
    scripts = ["tests/garden/test_hose.gd", "tests/garden/test_mower.gd"]
    run = TestRun("Running the scene tests", scripts, show)
    run.started(scripts[0])
    run.started(scripts[1])
    run.finished(scripts[1], True)
    run.finished(scripts[0], False, "FAIL: too short")
    run.end()
    assert capsys.readouterr().out.splitlines() == [
        "    ✓ garden: mower",
        "    ✗ garden: hose — too short",
        "  2 checks: 1 passed, 1 failed — hose",
    ]


class Fake:
    """run_offline: the scripts that fail on the stage."""

    def __init__(self, failing: set[str]) -> None:
        self.failing = failing

    async def __call__(
        self, argv: list[str], root: Path, *_a: Any, **_k: Any
    ) -> Result:
        if "--import" in argv:
            return Result(True, "", "", 0.0)
        script = argv[argv.index("-s") + 1].removeprefix("res://")
        await asyncio.sleep(0)
        if script in self.failing:
            return Result(False, "FAIL: the lever sticks", "", 0.0)
        return Result(True, "", "", 0.0)


def test_scene_tests_report_as_they_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "tests").mkdir()
    for name in ("test_door_opens", "test_lever_pulls"):
        (tmp_path / f"tests/{name}.gd").write_text(TEST)
    tx = FileTransaction(tmp_path)
    tx.begin("Lever")
    monkeypatch.setattr(
        "joinery.submission.run_offline", Fake({"tests/test_lever_pulls.gd"})
    )
    events: list[dict[str, Any]] = []
    asyncio.run(
        submission.run_scene_tests(
            tx.stage, submission.scene_tree_tests(tx.stage), report=events.append
        )
    )
    assert any(e["now"] == "Checking door opens" for e in events)
    last = events[-1]
    assert last["kind"] == "tests" and last["done"]
    assert last["summary"] == "2 checks: 1 passed, 1 failed — lever pulls"
    assert last["results"][1]["reason"] == "the lever sticks"
    # A second run is cached: its results come at once, with the same summary.
    again: list[dict[str, Any]] = []
    asyncio.run(
        submission.run_scene_tests(
            tx.stage, submission.scene_tree_tests(tx.stage), report=again.append
        )
    )
    assert again[-1]["summary"] == last["summary"]
