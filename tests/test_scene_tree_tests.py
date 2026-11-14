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
        if argv[-1] == "res://tests/unit/test_lever.gd":
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
    assert [r[-1] for r in runs[1:]] == [
        "res://tests/test_door.gd",
        "res://tests/unit/test_lever.gd",
    ]
    assert all(r[-2] == "-s" for r in runs[1:])
    (problem,) = problems
    assert problem.check == "tests" and problem.paths == ["tests/unit/test_lever.gd"]
    assert "the lever didn't move" in problem.message
