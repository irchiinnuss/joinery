"""The submit's SceneTree tests: judged against the project before the change,
cached by the content of what each test loads, and run several at once."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from joinery import scene_tests, submission
from joinery.runner import Result
from joinery.transactions import FileTransaction

TEST = "extends SceneTree\n\nfunc _initialize() -> void:\n\tquit(0)\n"


def _project(root: Path) -> None:
    (root / "project.godot").write_text(
        'config_version=5\n[autoload]\nGame="*res://core/game.gd"\n'
    )
    (root / "core").mkdir()
    (root / "core/game.gd").write_text("extends Node\n")
    (root / "game").mkdir()
    (root / "game/door.gd").write_text("class_name Door\nextends Node3D\n")
    (root / "game/door.tscn").write_text(
        '[gd_scene format=3 uid="uid://doorscene"]\n'
        '[ext_resource type="Script" path="res://game/door.gd" id="1"]\n'
    )
    (root / "game/lever.gd").write_text("extends Node\n")
    (root / "tests").mkdir()
    (root / "tests/test_door.gd").write_text(
        TEST + 'const SCENE := preload("uid://doorscene")\n'
    )
    (root / "tests/test_lever.gd").write_text(
        TEST + 'const LEVER := "res://game/lever.gd"\n'
    )
    (root / "tests/test_class.gd").write_text(TEST + "var door: Door\n")
    (root / "notes.md").write_text("design notes\n")


def test_dependencies_follow_paths_uids_classes_and_autoloads(tmp_path: Path) -> None:
    _project(tmp_path)
    tree = scene_tests.Tree(tmp_path)
    door = tree.dependencies("tests/test_door.gd")
    assert {"project.godot", "core/game.gd", "game/door.tscn", "game/door.gd"} <= set(door)
    assert "game/lever.gd" not in door and "notes.md" not in door
    assert "game/lever.gd" in tree.dependencies("tests/test_lever.gd")
    assert "game/door.gd" in tree.dependencies("tests/test_class.gd")
    # A change outside a test's files leaves its key alone.
    before = tree.key("tests/test_door.gd", [])
    (tmp_path / "game/lever.gd").write_text("extends Node\n# changed\n")
    (tmp_path / "notes.md").write_text("more notes\n")
    assert scene_tests.Tree(tmp_path).key("tests/test_door.gd", []) == before
    (tmp_path / "game/door.gd").write_text("class_name Door\nextends Node3D\nvar open := true\n")
    assert scene_tests.Tree(tmp_path).key("tests/test_door.gd", []) != before


class Fake:
    """run_offline for the tests: which runs failed, where, and how many at once."""

    def __init__(self, failing: dict[str, set[str]]) -> None:
        #: "stage" or "baseline" -> scripts that fail there.
        self.failing = failing
        self.runs: list[tuple[str, str]] = []
        self.imports: list[str] = []
        self.active = 0
        self.most = 0

    async def __call__(self, argv: list[str], root: Path, *_a: Any, **_k: Any) -> Result:
        where = "baseline" if root.name.startswith("baseline-") else "stage"
        if "--import" in argv:
            self.imports.append(where)
            return Result(True, "", "", 0.0)
        script = argv[argv.index("-s") + 1].removeprefix("res://")
        self.runs.append((where, script))
        self.active += 1
        self.most = max(self.most, self.active)
        # Stay "running" until a second run overlaps (or 2 s pass), so a slow
        # machine still shows whether runs go side by side.
        for _ in range(100):
            if self.most > 1:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.02)
        self.active -= 1
        if script in self.failing.get(where, set()):
            return Result(False, f"FAIL: {script}", "", 0.0)
        return Result(True, "", "", 0.0)


def _transaction(tmp_path: Path) -> FileTransaction:
    _project(tmp_path)
    tx = FileTransaction(tmp_path)
    tx.begin("Lever")
    return tx


def _run(tx: FileTransaction, info: list[str]) -> list[Any]:
    return asyncio.run(
        submission.run_scene_tests(
            tx.stage, submission.scene_tree_tests(tx.stage), tx=tx, info=info
        )
    )


def test_only_a_test_the_change_breaks_holds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _transaction(tmp_path)
    (tx.stage / "game/lever.gd").write_text("extends Node\nvar pulled := true\n")
    (tx.stage / "tests/test_new.gd").write_text(TEST)
    fake = Fake(
        {
            # The template's door test failed before the change too; the lever
            # test passed before; the new test fails.
            "stage": {"tests/test_door.gd", "tests/test_lever.gd", "tests/test_new.gd"},
            "baseline": {"tests/test_door.gd"},
        }
    )
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    info: list[str] = []
    problems = _run(tx, info)
    assert sorted(p.paths[0] for p in problems) == ["tests/test_lever.gd", "tests/test_new.gd"]
    assert info == [
        "Already failing before this change, so not held for it: res://tests/test_door.gd."
    ]
    # The door test can't be affected by a change to the lever: its failure
    # before is known from the same run, so only the lever test runs on a copy
    # of the project as it was, imported first, and the copy is removed.
    assert fake.imports == ["baseline"]
    assert [r for r in fake.runs if r[0] == "baseline"] == [("baseline", "tests/test_lever.gd")]
    assert not list(tx.directory.glob("baseline-*"))
    assert fake.most > 1  # several tests at once


def test_results_are_cached_by_what_each_test_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _transaction(tmp_path)
    fake = Fake({"stage": {"tests/test_door.gd"}, "baseline": {"tests/test_door.gd"}})
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    info: list[str] = []
    assert _run(tx, info) == [] and info
    first = len(fake.runs)
    assert first == 3
    # Nothing changed: no test runs again, and the baseline is cached too.
    info = []
    assert _run(tx, info) == [] and info
    assert len(fake.runs) == first and fake.imports == []
    # A change to the lever runs only the lever test.
    (tx.stage / "game/lever.gd").write_text("extends Node\nvar pulled := true\n")
    assert _run(tx, []) == []
    assert fake.runs[first:] == [("stage", "tests/test_lever.gd")]


def test_without_a_transaction_every_failure_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path)
    fake = Fake({"stage": {"tests/test_door.gd"}})
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    problems = asyncio.run(
        submission.run_scene_tests(tmp_path, ["tests/test_door.gd"], info=[])
    )
    assert [p.paths for p in problems] == [["tests/test_door.gd"]]


def test_a_worker_that_did_not_start_is_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    down = Result(False, "", "Cannot connect to the Docker daemon at unix://", 0.0)
    answers = [down, down, Result(True, "imported", "", 0.0)]
    slept: list[float] = []

    async def offline(*_a: Any, **_k: Any) -> Result:
        return answers.pop(0)

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    monkeypatch.setattr("joinery.submission.asyncio.sleep", sleep)
    got = asyncio.run(submission.worker_run(["godot"], tmp_path, 10))
    assert got.ok and slept == [2.0, 4.0]
    # A check that ran and failed is the check's answer: never tried again.
    answers[:] = [Result(False, "FAIL: door", "", 0.0)]
    assert not asyncio.run(submission.worker_run(["godot"], tmp_path, 10)).ok
    assert slept == [2.0, 4.0]


def test_comments_and_path_checks_are_not_dependencies(tmp_path: Path) -> None:
    _project(tmp_path)
    (tmp_path / "core/game.gd").write_text(
        "extends Node\n"
        "## `res://game/lever.gd` is where the lever lives.\n"
        'func local(p: String) -> bool:\n\treturn p.begins_with("res://")\n'
    )
    door = scene_tests.Tree(tmp_path).dependencies("tests/test_door.gd")
    assert "game/lever.gd" not in door and "notes.md" not in door
