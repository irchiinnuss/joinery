"""SceneTree tests run with the project's tests: one convention for done-when tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from joinery import submission
from joinery.runner import Result
from joinery.transactions import FileTransaction


def _stage(tmp_path: Path) -> FileTransaction:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    tx = FileTransaction(tmp_path)
    tx.begin("Door")
    (tx.stage / "tests/unit").mkdir(parents=True)
    (tx.stage / "tests/test_door.gd").write_text(
        "extends SceneTree\n\nfunc _initialize() -> void:\n\tquit(0)\n"
    )
    (tx.stage / "tests/unit/test_lever.gd").write_text(
        'class_name LeverTest extends "SceneTree"  # a harness\n'
    )
    (tx.stage / "tests/helpers.gd").write_text("extends Node\n")
    (tx.stage / "tests/test_gut.gd").write_text("extends GutTest\n")
    (tx.stage / "scripts").mkdir()
    (tx.stage / "scripts/tool.gd").write_text("extends SceneTree\n")
    return tx


def test_scene_tree_tests_are_found_under_the_test_folder(tmp_path: Path) -> None:
    tx = _stage(tmp_path)
    assert submission.scene_tree_tests(tx.stage) == [
        "tests/test_door.gd",
        "tests/unit/test_lever.gd",
    ]
    # Not a GUT project, but its SceneTree tests still run.
    assert submission.gut_dirs(tx.stage) == []


@pytest.mark.parametrize(
    ("result", "passed"),
    [
        (Result(True, "door opened\n", "", 0.1), True),
        (Result(False, "", "", 0.1), False),
        (Result(True, "FAIL: the door stayed shut\n", "", 0.1), False),
        (Result(True, "", "SCRIPT ERROR: Invalid call\n", 0.1), False),
        # An engine line that is not the test's own failure doesn't fail it.
        (Result(True, "", "ERROR: OpenXR: no runtime\n", 0.1), True),
    ],
)
def test_a_scene_tree_test_passes_by_exit_code_and_output(
    result: Result, passed: bool
) -> None:
    assert submission.scene_test_passed(result) is passed


def test_each_scene_tree_test_runs_after_the_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _stage(tmp_path)
    runs: list[list[str]] = []

    async def offline(argv: list[str], _root: Path, *_a: Any, **_k: Any) -> Result:
        runs.append(argv)
        if "res://tests/unit/test_lever.gd" in argv:
            return Result(False, "FAIL: the lever didn't move", "", 0.1)
        return Result(True, "", "", 0.1)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    problems = asyncio.run(
        submission.checks(
            tx,
            [],
            rules=False,
            godot=False,
            scene_tests=submission.scene_tree_tests(tx.stage),
        )
    )
    assert "--import" in runs[0]
    scripts = [r[r.index("-s") + 1] for r in runs[1:]]
    assert scripts == ["res://tests/test_door.gd", "res://tests/unit/test_lever.gd"]
    (problem,) = problems
    assert problem.check == "tests" and problem.paths == ["tests/unit/test_lever.gd"]
    assert "the lever didn't move" in problem.message


HARNESS_TEST = """extends SceneTree
## The door opens.
##
## Run: godot --headless --fixed-fps 60 --script res://tests/test_door.gd
##        -- --autostart --settings-path=<file> [--door-phase=verify]

const TEST_NAME := "door"
"""


def test_a_scene_tree_test_runs_as_its_run_line_says(tmp_path: Path) -> None:
    # Run bare, the harness tests time out or fail for want of a fixed frame
    # rate, --autostart and a settings file of their own.
    tx = _stage(tmp_path)
    (tx.stage / "tests/test_door.gd").write_text(HARNESS_TEST)
    argv = submission.scene_test_argv(tx.stage, "tests/test_door.gd", "/tmp/s.cfg")
    assert argv == [
        "godot", "--headless", "--path", "/work", "--fixed-fps", "60",
        "-s", "res://tests/test_door.gd", "--", "--autostart", "--settings-path=/tmp/s.cfg",
    ]  # fmt: skip
    # No Run line: the same harness convention.
    lever = submission.scene_test_argv(
        tx.stage, "tests/unit/test_lever.gd", "/tmp/l.cfg"
    )
    assert lever[-3:] == ["--", "--autostart", "--settings-path=/tmp/l.cfg"]
    assert "--fixed-fps" in lever
    # A VR project runs its tests on the desktop, as the blueprints' own runs do.
    (tx.stage / "project.godot").write_text("[xr]\n\nopenxr/enabled=true\n")
    vr = submission.scene_test_argv(tx.stage, "tests/test_door.gd", "/tmp/s.cfg")
    assert vr[2:4] == ["--xr-mode", "off"]


def test_tests_that_cannot_run_alone_are_left_out_and_the_games_come_first(
    tmp_path: Path,
) -> None:
    tx = _stage(tmp_path)
    features = tx.stage / "tests/features/online"
    features.mkdir(parents=True)
    (features / "pair_test.gd").write_text(
        "extends SceneTree\n## Host:   --host --port=N\n## Client: --join=127.0.0.1:N\n"
    )
    (features / "lobby_test.gd").write_text(
        "extends SceneTree\n## Run: godot --script x.gd -- --port=<n>\n"
    )
    (features / "a_test.gd").write_text("extends SceneTree\n")
    (tx.stage / "tests/test_zeta.gd").write_text("extends SceneTree\n")
    assert submission.scene_tree_tests(tx.stage) == [
        "tests/test_door.gd",
        "tests/test_zeta.gd",
        "tests/unit/test_lever.gd",
        "tests/features/online/a_test.gd",
    ]


#: What Godot printed for a live run's test that names BehaviourAgent.
COMPILED_EARLY = (
    "SCRIPT ERROR: Compile Error: Identifier not found: Replays\n"
    "          at: GDScript::reload (res://features/behaviour/behaviour_agent.gd:113)\n"
    "SCRIPT ERROR: Compile Error: Failed to compile depended scripts.\n"
)


def test_a_test_that_cannot_see_an_autoload_is_told_how_to_reach_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _stage(tmp_path)
    (tx.stage / "project.godot").write_text(
        'config_version=5\n\n[autoload]\n\nReplays="*res://core/replays/replays.gd"\n'
        'Game="*res://core/game.gd"\n\n[display]\n\nwindow/size/mode=0\n'
    )
    assert submission.autoloads(tx.stage) == ["Replays", "Game"]
    hint = submission.autoload_hint(tx.stage, COMPILED_EARLY)
    assert "Replays is an autoload" in hint
    assert 'root.get_node(^"/root/Replays")' in hint and "play.replays" in hint
    # Another unknown name is the test's own mistake: no hint.
    assert submission.autoload_hint(tx.stage, "Identifier not found: Lever") == ""

    async def offline(argv: list[str], _root: Path, *_a: Any, **_k: Any) -> Result:
        if "--import" in argv:
            return Result(True, "", "", 0.0)
        return Result(False, COMPILED_EARLY, "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    problems = asyncio.run(
        submission.run_scene_tests(tx.stage, ["tests/test_door.gd"], tx=None)
    )
    assert "Replays is an autoload" in problems[0].message
