"""The scene tests keep within the check's own time, so the check answers before
the agent's tool gives up: a test still running then is stopped and named, one
not started is named too, and neither counts as a pass, a failure or a cached
result."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from joinery import scene_tests, submission
from joinery.runner import Result
from joinery.transactions import FileTransaction
from tests.test_scene_tests_baseline import TEST, _project


class Slow:
    """run_offline for the tests: ``slow`` scripts take far longer than the budget."""

    def __init__(self, slow: set[str], fail: set[str] | None = None) -> None:
        self.slow = slow
        self.fail = fail or set()
        self.runs: list[str] = []
        self.cancelled: list[str] = []

    async def __call__(
        self, argv: list[str], root: Path, seconds: int, **_k: Any
    ) -> Result:
        if "--import" in argv:
            return Result(True, "", "", 0.0)
        script = argv[argv.index("-s") + 1].removeprefix("res://")
        self.runs.append(script)
        if script in self.slow:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self.cancelled.append(script)
                raise
        if script in self.fail:
            return Result(False, f"FAIL: {script}", "", 0.0)
        return Result(True, "ok", "", 0.0)


def _transaction(tmp_path: Path) -> FileTransaction:
    _project(tmp_path)
    tx = FileTransaction(tmp_path)
    tx.begin("Lever")
    return tx


def _run(tx: FileTransaction, budget: float) -> list[Any]:
    return asyncio.run(
        submission.run_scene_tests(
            tx.stage,
            submission.scene_tree_tests(tx.stage),
            tx=tx,
            deadline=time.monotonic() + budget,
            budget=budget,
        )
    )


def test_a_test_still_running_at_the_deadline_is_stopped_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _transaction(tmp_path)
    fake = Slow({"tests/test_lever.gd"})
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    monkeypatch.setattr("joinery.scene_tests.MIN_LEFT", 0.05)
    began = time.monotonic()
    problems = _run(tx, 0.5)
    assert time.monotonic() - began < 5, "the check answered at its deadline"
    (problem,) = problems
    assert problem.check == "checks" and not problem.fixable
    assert problem.paths == ["tests/test_lever.gd"]
    assert "res://tests/test_lever.gd was still running after" in problem.message
    assert "ran out of time (0 s)" not in problem.message
    assert "Don't run the same check again" in problem.message
    assert fake.cancelled == ["tests/test_lever.gd"], "its worker was stopped"
    # Never cached: the next check runs it again, and only it.
    fake.slow.clear()
    fake.runs.clear()
    assert _run(tx, 30) == []
    assert fake.runs == ["tests/test_lever.gd"]


def test_tests_not_started_by_the_deadline_are_named_and_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _transaction(tmp_path)
    fake = Slow({"tests/test_class.gd"})
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    monkeypatch.setattr("joinery.scene_tests.PARALLEL", 1)
    monkeypatch.setattr("joinery.scene_tests.MIN_LEFT", 0.05)
    (problem,) = _run(tx, 0.4)
    # test_class runs first and takes the whole budget; the other two never start.
    assert fake.runs == ["tests/test_class.gd"]
    assert sorted(problem.paths) == [
        "tests/test_class.gd",
        "tests/test_door.gd",
        "tests/test_lever.gd",
    ]
    assert "res://tests/test_door.gd did not start" in problem.message


def test_a_real_failure_beside_an_unfinished_test_still_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _transaction(tmp_path)
    (tx.stage / "tests/test_new.gd").write_text(TEST)
    fake = Slow({"tests/test_lever.gd"}, fail={"tests/test_new.gd"})
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    monkeypatch.setattr("joinery.scene_tests.PARALLEL", 4)
    monkeypatch.setattr("joinery.scene_tests.MIN_LEFT", 0.05)
    problems = _run(tx, 0.5)
    by = {p.check: p for p in problems}
    assert by["checks"].paths == ["tests/test_lever.gd"]
    assert by["tests"].paths == ["tests/test_new.gd"]


def test_no_baseline_runs_once_the_time_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Judging a failure against the project before the change needs time too:
    with none left, no copy is made."""
    made: list[str] = []

    async def baseline() -> Path | None:
        made.append("copy")
        return None

    async def run(_root: Path, script: str) -> Result:
        await asyncio.sleep(0.3)
        return Result(False, "FAIL", "", 0.0)

    monkeypatch.setattr("joinery.scene_tests.MIN_LEFT", 0.05)
    _project(tmp_path)
    tx = FileTransaction(tmp_path)
    tx.begin("Lever")
    (tx.stage / "game/lever.gd").write_text("extends Node\nvar x := 1\n")
    verdict = asyncio.run(
        scene_tests.judge(
            project=None,
            stage=tx.stage,
            original=tx.original,
            scripts=["tests/test_lever.gd"],
            args=lambda *_: [],
            run=run,
            passed=lambda got: got.ok,
            baseline=baseline,
            deadline=time.monotonic() + 0.32,
            budget=0.32,
        )
    )
    assert verdict.broken and not made
