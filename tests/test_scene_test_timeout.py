"""A SceneTree test that runs past its time limit fails that test alone: it is
tried once more, judged against the project before the change like any other
result, and never ends the other checks with a blank reason."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from joinery import scene_tests, submission
from joinery.authoring import WorkerTimeout, run_offline
from joinery.runner import Result
from joinery.transactions import FileTransaction
from joinery.turn import error_text
from tests.test_scene_tests_baseline import _project

TEST_HANGS = "extends SceneTree\n\nfunc _initialize() -> void:\n\tpass\n"


class Hangs:
    """run_offline for the tests: scripts that never finish, where."""

    def __init__(self, hang: dict[str, set[str]], once: bool = False) -> None:
        self.hang = hang
        self.once = once
        self.runs: list[tuple[str, str]] = []
        self.timeouts: list[int] = []

    async def __call__(
        self, argv: list[str], root: Path, seconds: int, **_k: Any
    ) -> Result:
        where = "baseline" if root.name.startswith("baseline-") else "stage"
        if "--import" in argv:
            return Result(True, "", "", 0.0)
        script = argv[argv.index("-s") + 1].removeprefix("res://")
        self.runs.append((where, script))
        self.timeouts.append(seconds)
        if script in self.hang.get(where, set()):
            if self.once:
                self.hang[where].discard(script)
            raise TimeoutError
        return Result(True, "PASS", "", 0.0)


def _transaction(tmp_path: Path) -> FileTransaction:
    _project(tmp_path)
    (tmp_path / "joinery.toml").write_text("[checks]\nscene_test_seconds = 45\n")
    tx = FileTransaction(tmp_path)
    tx.begin("Lever")
    return tx


def _run(tx: FileTransaction, info: list[str]) -> list[Any]:
    return asyncio.run(
        submission.run_scene_tests(
            tx.stage, submission.scene_tree_tests(tx.stage), tx=tx, info=info
        )
    )


def test_a_hung_new_test_fails_alone_and_the_rest_still_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _transaction(tmp_path)
    (tx.stage / "tests/test_hangs.gd").write_text(
        "extends SceneTree\n\nfunc _initialize() -> void:\n\tpass\n"
    )
    fake = Hangs({"stage": {"tests/test_hangs.gd"}})
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    problems = _run(tx, [])
    assert [p.paths for p in problems] == [["tests/test_hangs.gd"]]
    text = problems[0].message
    assert "res://tests/test_hangs.gd timed out after 45 s" in text
    assert "quit()" in text and "scene_test_seconds" in text
    assert problems[0].items[0].rule == "tests.timed_out"
    # Tried once more before judging; the others ran once each.
    assert fake.runs.count(("stage", "tests/test_hangs.gd")) == 2
    assert fake.runs.count(("stage", "tests/test_door.gd")) == 1
    assert set(fake.timeouts) == {45}
    # A time-out is not cached: the next check runs the test again.
    _run(tx, [])
    assert fake.runs.count(("stage", "tests/test_hangs.gd")) == 4


def test_a_test_that_timed_out_before_the_change_too_is_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _transaction(tmp_path)
    (tx.stage / "game/lever.gd").write_text("extends Node\nvar pulled := true\n")
    fake = Hangs(
        {"stage": {"tests/test_lever.gd"}, "baseline": {"tests/test_lever.gd"}}
    )
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    info: list[str] = []
    assert _run(tx, info) == []
    assert info and "res://tests/test_lever.gd" in info[0]
    assert fake.runs.count(("baseline", "tests/test_lever.gd")) == 2


def test_a_test_that_passed_before_and_now_hangs_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _transaction(tmp_path)
    (tx.stage / "game/lever.gd").write_text("extends Node\nvar pulled := true\n")
    fake = Hangs({"stage": {"tests/test_lever.gd"}})
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    problems = _run(tx, [])
    assert [p.paths for p in problems] == [["tests/test_lever.gd"]]


def test_a_test_that_finishes_on_the_second_try_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _transaction(tmp_path)
    fake = Hangs({"stage": {"tests/test_door.gd"}}, once=True)
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    assert _run(tx, []) == []
    assert fake.runs.count(("stage", "tests/test_door.gd")) == 2


def test_the_limit_is_set_by_the_project_or_scales_under_emulation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("joinery.submission.platform.machine", lambda: "x86_64")
    assert submission.scene_test_seconds(tmp_path) == submission.SCENE_TEST_SECONDS
    monkeypatch.setattr("joinery.submission.platform.machine", lambda: "arm64")
    assert submission.scene_test_seconds(tmp_path) == (
        submission.SCENE_TEST_SECONDS * submission.EMULATED_FACTOR
    )
    (tmp_path / "joinery.toml").write_text("[checks]\nscene_test_seconds = 600\n")
    assert submission.scene_test_seconds(tmp_path) == 600
    # Nonsense falls back to the default.
    (tmp_path / "joinery.toml").write_text("[checks]\nscene_test_seconds = 0\n")
    assert submission.scene_test_seconds(tmp_path) > 0


def test_a_full_stage_import_gets_more_time_under_emulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The stage imports every asset again; three 1024 px PBR maps took the
    # emulated worker past the old 180 s.
    monkeypatch.setattr("joinery.submission.platform.machine", lambda: "x86_64")
    assert submission.import_seconds() == submission.IMPORT_SECONDS
    monkeypatch.setattr("joinery.submission.platform.machine", lambda: "arm64")
    assert submission.import_seconds() == submission.IMPORT_SECONDS * 2 > 180


def test_a_worker_error_elsewhere_cancels_the_other_runs(tmp_path: Path) -> None:
    started: list[str] = []
    cancelled: list[str] = []

    async def run(_root: Path, script: str) -> Result:
        started.append(script)
        if script == "bad":
            raise RuntimeError("Docker isn't running")
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(script)
            raise
        return Result(True, "", "", 0.0)

    with pytest.raises(RuntimeError):
        asyncio.run(
            scene_tests._run_all(tmp_path, ["slow", "bad"], run, lambda got: got.ok, 10)
        )
    assert cancelled == ["slow"]


def test_error_text_is_never_blank() -> None:
    assert error_text(TimeoutError()) == "TimeoutError"
    assert error_text(RuntimeError("Docker isn't running")) == (
        "RuntimeError: Docker isn't running"
    )


def test_a_test_a_script_error_stopped_is_not_run_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As in a live run: the test's _run hit a script error, so quit() never came.
    tx = _transaction(tmp_path)
    (tx.stage / "tests/test_gun.gd").write_text(TEST_HANGS)
    runs: list[str] = []

    async def offline(argv: list[str], root: Path, seconds: int, **_k: Any) -> Result:
        if "--import" in argv:
            return Result(True, "", "", 0.0)
        script = argv[argv.index("-s") + 1].removeprefix("res://")
        runs.append(script)
        if script == "tests/test_gun.gd":
            raise WorkerTimeout(
                seconds,
                "SCRIPT ERROR: Attempt to call function 'null::_from_3 (Callable)' "
                "on a null instance.\n   at: _run (res://tests/test_gun.gd:118)\n",
            )
        return Result(True, "PASS", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    (problem,) = _run(tx, [])
    assert runs.count("tests/test_gun.gd") == 1
    assert "a script error stopped it before it called quit()" in problem.message
    assert "test_gun.gd:118" in problem.message


def test_the_worker_is_killed_at_the_limit_and_its_output_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched: list[asyncio.subprocess.Process] = []
    real = asyncio.create_subprocess_exec

    async def launch(*argv: str, **options: Any) -> asyncio.subprocess.Process:
        if argv[:2] == ("docker", "rm"):
            return await real(sys.executable, "-c", "pass", **options)
        process = await real(
            sys.executable,
            "-c",
            "import sys, time; print('SCRIPT ERROR: boom', flush=True); time.sleep(30)",
            **options,
        )
        launched.append(process)
        return process

    monkeypatch.setattr("joinery.authoring.asyncio.create_subprocess_exec", launch)
    with pytest.raises(WorkerTimeout) as stopped:
        asyncio.run(run_offline(["godot"], tmp_path, 1, warm=False))
    assert "SCRIPT ERROR: boom" in stopped.value.output
    assert str(stopped.value)  # never a blank reason
    assert launched[0].returncode is not None  # the hung process was killed
