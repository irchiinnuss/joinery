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
from joinery.authoring import WorkerStalled, WorkerTimeout, run_offline
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
    # A time-out is cached by what the test loads: the next check of the same
    # files (the submit after a precheck) does not wait it out again.
    again = _run(tx, [])
    assert [p.paths for p in again] == [["tests/test_hangs.gd"]]
    assert again[0].items[0].rule == "tests.timed_out"
    assert fake.runs.count(("stage", "tests/test_hangs.gd")) == 2
    # A change to its files runs it again.
    (tx.stage / "tests/test_hangs.gd").write_text(TEST_HANGS + "# edited\n")
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


def test_a_stage_import_gets_more_time_under_emulation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Three 1024 px PBR maps took the emulated worker past the old 180 s.
    monkeypatch.setattr("joinery.submission.platform.machine", lambda: "x86_64")
    assert submission.import_seconds(tmp_path) == submission.IMPORT_SECONDS
    monkeypatch.setattr("joinery.submission.platform.machine", lambda: "arm64")
    assert submission.import_seconds(tmp_path) == submission.IMPORT_SECONDS * 2 > 180


def test_a_worker_error_elsewhere_cancels_the_other_runs(tmp_path: Path) -> None:
    started: list[str] = []
    cancelled: list[str] = []
    slow_running = asyncio.Event()

    async def run(_root: Path, script: str) -> Result:
        started.append(script)
        if script == "bad":
            # Fail only once the other run is under way, whatever order they start in.
            await asyncio.wait_for(slow_running.wait(), 5)
            raise RuntimeError("Docker isn't running")
        slow_running.set()
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

    async def answering(*_args: Any, **_kwargs: Any) -> bool:
        return True  # Docker itself answers: the tool is the slow one

    monkeypatch.setattr("joinery.docker_cli.responding_async", answering)
    with pytest.raises(WorkerTimeout) as stopped:
        asyncio.run(run_offline(["godot"], tmp_path, 1, warm=False))
    assert "SCRIPT ERROR: boom" in stopped.value.output
    assert str(stopped.value)  # never a blank reason
    assert launched[0].returncode is not None  # the hung process was killed


def test_a_hung_baseline_is_cached_so_the_submit_does_not_run_it_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As in a live run: a test hangs before and after the change; the precheck
    # and the submit both checked it, each waiting out both sides.
    tx = _transaction(tmp_path)
    (tx.stage / "game/lever.gd").write_text("extends Node\nvar pulled := true\n")
    fake = Hangs(
        {"stage": {"tests/test_lever.gd"}, "baseline": {"tests/test_lever.gd"}}
    )
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    info: list[str] = []
    assert _run(tx, info) == []  # the precheck
    assert fake.runs.count(("baseline", "tests/test_lever.gd")) == 2
    assert fake.runs.count(("stage", "tests/test_lever.gd")) == 2
    info = []
    assert _run(tx, info) == []  # the submit, nothing changed since
    assert info and "res://tests/test_lever.gd" in info[0]
    assert fake.runs.count(("baseline", "tests/test_lever.gd")) == 2
    assert fake.runs.count(("stage", "tests/test_lever.gd")) == 2
    # The change moves on: only the change side runs again; the baseline is known.
    (tx.stage / "game/lever.gd").write_text("extends Node\nvar pulled := false\n")
    assert _run(tx, []) == []
    assert fake.runs.count(("baseline", "tests/test_lever.gd")) == 2
    assert fake.runs.count(("stage", "tests/test_lever.gd")) == 4


def test_a_raised_limit_runs_a_cached_time_out_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _transaction(tmp_path)
    fake = Hangs({"stage": {"tests/test_door.gd"}})
    monkeypatch.setattr("joinery.submission.run_offline", fake)
    # Unchanged by the change, so it hung before it too: a note, not a hold.
    _run(tx, [])
    assert fake.runs.count(("stage", "tests/test_door.gd")) == 2
    _run(tx, [])
    assert fake.runs.count(("stage", "tests/test_door.gd")) == 2
    (tmp_path / "joinery.toml").write_text("[checks]\nscene_test_seconds = 90\n")
    _run(tx, [])
    assert fake.runs.count(("stage", "tests/test_door.gd")) == 4
    assert fake.timeouts[-1] == 90


def _spawn_python(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> list[asyncio.subprocess.Process]:
    """Make run_offline start ``python -c code`` in place of the worker."""
    launched: list[asyncio.subprocess.Process] = []
    real = asyncio.create_subprocess_exec

    async def launch(*argv: str, **options: Any) -> asyncio.subprocess.Process:
        if argv[:2] == ("docker", "rm"):
            return await real(sys.executable, "-c", "pass", **options)
        process = await real(sys.executable, "-c", code, **options)
        launched.append(process)
        return process

    monkeypatch.setattr("joinery.authoring.asyncio.create_subprocess_exec", launch)
    return launched


#: A test whose physics callback errors every frame after its _run stopped.
REPEATS_ERROR = """
import time
print('Godot Engine v4.7', flush=True)
for _ in range(400):
    print('SCRIPT ERROR: Invalid call. Nonexistent function find_target in base Nil.')
    print('   at: _update_sight (res://game/enemies/slag_drudge.gd:142)', flush=True)
    time.sleep(0.05)
"""


def test_a_run_that_repeats_one_error_is_stopped_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched = _spawn_python(monkeypatch, REPEATS_ERROR)

    async def go() -> float:
        loop = asyncio.get_running_loop()
        start = loop.time()
        with pytest.raises(WorkerStalled) as stopped:
            await run_offline(["godot"], tmp_path, 60, warm=False, stall=1.0)
        assert "slag_drudge.gd:142" in stopped.value.output
        assert "nothing new for 1 s" in str(stopped.value)
        return loop.time() - start

    assert asyncio.run(go()) < 10  # not the 60 s limit
    assert launched[0].returncode is not None


def test_a_run_that_keeps_making_progress_after_an_error_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spawn_python(
        monkeypatch,
        "import time\n"
        "print('SCRIPT ERROR: one-off', flush=True)\n"
        "for i in range(8):\n"
        "    print(f'step {i}', flush=True)\n"
        "    time.sleep(0.4)\n"
        "print('FAIL: the door stayed shut', flush=True)\n",
    )
    got = asyncio.run(run_offline(["godot"], tmp_path, 60, warm=False, stall=1.0))
    assert "FAIL: the door stayed shut" in got.output


def test_a_stalled_test_is_stopped_once_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _transaction(tmp_path)
    (tx.stage / "tests/test_gun.gd").write_text(TEST_HANGS)
    runs: list[tuple[str, float]] = []

    async def offline(argv: list[str], root: Path, seconds: int, **k: Any) -> Result:
        if "--import" in argv:
            return Result(True, "", "", 0.0)
        script = argv[argv.index("-s") + 1].removeprefix("res://")
        runs.append((script, float(k.get("stall", 0))))
        if script == "tests/test_gun.gd":
            raise WorkerStalled(
                k["stall"],
                "SCRIPT ERROR: boom\n   at: _run (res://tests/test_gun.gd:9)\n",
            )
        return Result(True, "PASS", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    (problem,) = _run(tx, [])
    gun = [stall for script, stall in runs if script == "tests/test_gun.gd"]
    assert len(gun) == 1 and gun[0] > 0  # stopped early, never tried again
    assert "printed nothing new" in problem.message
    assert "test_gun.gd:9" in problem.message
    assert problem.items[0].rule == "tests.timed_out"
    # Cached: the submit after the precheck reports it without a run.
    (again,) = _run(tx, [])
    assert "printed nothing new" in again.message
    assert len([s for s, _ in runs if s == "tests/test_gun.gd"]) == 1


DOCKER_DOWN = Result(False, "", "Cannot connect to the Docker daemon", 0.0)


def test_a_docker_hiccup_is_asked_again_and_never_cached_as_a_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _transaction(tmp_path)
    (tx.stage / "tests/test_gun.gd").write_text(TEST_HANGS)
    monkeypatch.setattr("joinery.submission.WORKER_RETRY_DELAYS", (0.0, 0.0, 0.0))
    runs: list[str] = []
    down = {"left": 2, "always": 0}

    async def offline(argv: list[str], root: Path, seconds: int, **k: Any) -> Result:
        if "--import" in argv:
            return Result(True, "", "", 0.0)
        script = argv[argv.index("-s") + 1].removeprefix("res://")
        runs.append(script)
        if script == "tests/test_gun.gd":
            if down["always"]:
                return DOCKER_DOWN
            if down["left"]:
                down["left"] -= 1
                return DOCKER_DOWN
            raise WorkerStalled(k["stall"], "SCRIPT ERROR: boom\n")
        return Result(True, "PASS", "", 0.0)

    monkeypatch.setattr("joinery.submission.run_offline", offline)
    # Two refusals, then the run: the stalled result stands and is cached.
    (problem,) = _run(tx, [])
    assert "printed nothing new" in problem.message
    assert runs.count("tests/test_gun.gd") == 3
    # Docker stays down for a test whose files changed: the check stops with
    # Docker's own reason and caches nothing for it.
    (tx.stage / "tests/test_gun.gd").write_text(TEST_HANGS + "# edited\n")
    down["always"] = 1
    with pytest.raises(RuntimeError, match="Docker isn't running"):
        _run(tx, [])
    down["always"] = 0
    runs.clear()
    _run(tx, [])
    assert runs.count("tests/test_gun.gd") == 1  # run, not answered from a cache


def test_the_scene_tests_say_how_many_are_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tx = _transaction(tmp_path)
    monkeypatch.setattr("joinery.submission.run_offline", Hangs({}))
    said: list[str] = []
    scripts = submission.scene_tree_tests(tx.stage)
    asyncio.run(submission.run_scene_tests(tx.stage, scripts, tx=tx, note=said.append))
    assert said[-1] == f"{len(scripts)} of {len(scripts)}"
    assert said[0] == f"1 of {len(scripts)}"
