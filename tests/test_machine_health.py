"""A full disk and a stopped Docker are found and said plainly (joinery.machine_health)."""

from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridge import stdio
from bridge.claude_session import claude_exit_text
from bridge.protocol import Done, Message
from bridge.turn_runner import _with_cause
from joinery import gateway as gateway_module
from joinery import machine_health as mh
from joinery import submission
from joinery.authoring import WorkerTimeout
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC
from joinery.runner import Result
from tests.test_agent_names import serve
from tests.test_chats import FakeBridge

FULL = "The disk is full (2 GB free). Free space, then Retry."


@pytest.fixture
def two_gb_free(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mh, "MIN_FREE", 3_000_000_000)
    monkeypatch.setattr(mh, "free_bytes", lambda _path: 2_100_000_000)
    monkeypatch.setattr(mh, "docker_data", list)


def test_the_words_for_a_full_disk_and_a_stopped_docker() -> None:
    assert mh.disk_full(2_100_000_000) == FULL
    assert mh.disk_full(400_000_000) == (
        "The disk is full (0.4 GB free). Free space, then Retry."
    )
    assert mh.disk_full(None) == "The disk is full. Free space, then Retry."
    assert mh.DOCKER_DOWN == "Docker isn't running. Start Docker Desktop, then Retry."


def test_the_fullest_of_the_project_and_dockers_disk_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mh, "MIN_FREE", 3_000_000_000)
    free = {tmp_path / "game": 50_000_000_000, tmp_path / "docker": 2_100_000_000}
    assert mh.disk_problem(free, free.get) == FULL
    free[tmp_path / "docker"] = 10_000_000_000
    assert mh.disk_problem(free, free.get) == ""
    # A place whose space can't be read says nothing.
    assert mh.disk_problem([tmp_path / "gone"], lambda _p: None) == ""
    # The real disk this test runs on has its free space read.
    assert mh.free_bytes(tmp_path / "not/made/yet") is not None


def test_a_tools_error_is_mapped_when_it_is_the_machines(two_gb_free: None) -> None:
    for text in (
        "Error: write /var/lib/docker/tmp/x: no space left on device",
        "OSError: [Errno 28] No space left on device: 'stage/a.png'",
        "npm ERR! code ENOSPC",
    ):
        assert mh.explain(text, [Path("/project")]) == FULL
    for text in (
        (
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the"
            " docker daemon running?"
        ),
        "error during connect: Get http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine",
        "Docker isn't responding — restart Docker Desktop, then try again.",
    ):
        assert mh.explain(text) == mh.DOCKER_DOWN
    # The game's own words, or any other failure: not the machine's.
    assert mh.explain("Save failed: disk full") == ""
    assert mh.explain('Identifier "speed" not declared') == ""


def _fake_docker(root: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    """A ``docker`` that runs ``script``, alone on PATH (a new folder each time:
    macOS may refuse an executable rewritten in place)."""
    folder = root / f"bin{len(list(root.glob('bin*')))}"
    folder.mkdir()
    docker = folder / "docker"
    docker.write_text("#!/bin/sh\n" + script + "\n")
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(folder))


@pytest.mark.skipif(os.name == "nt", reason="a shell script stands in for docker")
def test_docker_is_asked_for_its_version_and_a_stopped_or_hung_one_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    # Not installed: the app checks say so, not this.
    assert asyncio.run(mh.docker_problem()) == ""
    _fake_docker(tmp_path, monkeypatch, "echo 27.3.1")
    assert asyncio.run(mh.docker_problem()) == ""
    _fake_docker(
        tmp_path,
        monkeypatch,
        "echo 'Cannot connect to the Docker daemon at unix:///x.sock. Is the docker"
        " daemon running?' >&2; exit 1",
    )
    assert asyncio.run(mh.docker_problem()) == mh.DOCKER_DOWN
    _fake_docker(tmp_path, monkeypatch, "exec /bin/sleep 5")
    assert asyncio.run(mh.docker_problem(timeout=0.2)) == mh.DOCKER_DOWN
    # Some other failure of the CLI: not called "not running".
    _fake_docker(tmp_path, monkeypatch, "echo 'unknown flag' >&2; exit 1")
    assert asyncio.run(mh.docker_problem()) == ""


def test_the_hosts_check_is_disk_first_then_docker_and_can_be_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_gb_free: None
) -> None:
    check = mh.checker(tmp_path)
    assert asyncio.run(check()) == ""  # Off in tests (conftest).
    monkeypatch.setenv(mh.SWITCH, "on")
    assert asyncio.run(check()) == FULL
    monkeypatch.setattr(mh, "free_bytes", lambda _path: 90_000_000_000)

    async def stopped(timeout: float = 0) -> str:
        return mh.DOCKER_DOWN

    monkeypatch.setattr(mh, "docker_problem", stopped)
    assert asyncio.run(check()) == mh.DOCKER_DOWN


def test_a_worker_with_no_space_left_or_no_docker_fails_at_once(
    tmp_path: Path, two_gb_free: None
) -> None:
    async def no_space(*_a: Any, **_k: Any) -> Result:
        return Result(False, "", "ERROR: write error: No space left on device", 0.0)

    with pytest.raises(mh.MachineProblem, match="The disk is full"):
        asyncio.run(submission.worker_run(["godot"], tmp_path, 10, runner=no_space))

    async def imports_forever(*_a: Any, **_k: Any) -> Result:
        raise WorkerTimeout(300, "Importing… 40%")

    # An import that only counted on, on a full disk: the disk, not a slow import.
    with pytest.raises(mh.MachineProblem) as caught:
        asyncio.run(
            submission.worker_run(["godot"], tmp_path, 10, runner=imports_forever)
        )
    assert str(caught.value) == FULL


def test_a_worker_time_out_with_room_on_disk_stays_a_time_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mh, "free_bytes", lambda _path: 90_000_000_000)

    async def slow(*_a: Any, **_k: Any) -> Result:
        raise WorkerTimeout(300, "Importing… 40%")

    with pytest.raises(WorkerTimeout):
        asyncio.run(submission.worker_run(["godot"], tmp_path, 10, runner=slow))


def test_a_long_step_ends_when_the_disk_fills_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text("")
    gateway = ProjectGateway(tmp_path)
    said: list[str] = []
    gateway.progress = said.append
    monkeypatch.setattr(gateway_module, "PROGRESS_EVERY", 0.01)
    checks = ["", FULL]

    async def health() -> str:
        return checks.pop(0) if checks else FULL

    gateway.health = health

    async def importing() -> None:
        with gateway.ticking("Importing into Godot"):
            await asyncio.sleep(30)

    with pytest.raises(mh.MachineProblem, match="The disk is full"):
        asyncio.run(asyncio.wait_for(importing(), 5))
    # One tick counted on; the next said why the step ended.
    assert said[0].startswith("Importing into Godot, ")
    assert said[-1] == "Importing into Godot: " + FULL
    assert gateway.machine_problem == FULL


def test_a_failed_tool_call_is_said_plainly_and_kept_for_the_turn(
    two_gb_free: None,
) -> None:
    gateway = SimpleNamespace(project=Path("/project"), machine_problem="")
    rpc = SimpleNamespace(gateway=gateway)
    error = OSError(28, "No space left on device")
    text = GatewayRPC._said(rpc, error, "write_file")  # type: ignore[arg-type]
    assert text.startswith(FULL) and "stop and tell the user" in text
    assert gateway.machine_problem == FULL
    stopped = mh.MachineProblem(mh.DOCKER_DOWN)
    assert GatewayRPC._said(rpc, stopped, "submit").startswith(mh.DOCKER_DOWN)  # type: ignore[arg-type]
    assert gateway.machine_problem == mh.DOCKER_DOWN
    # Anything else is the call's own error, as before.
    other = GatewayRPC._said(rpc, ValueError("No such node"), "edit")  # type: ignore[arg-type]
    assert other == "No such node"


def test_the_turns_failure_carries_the_cause(two_gb_free: None) -> None:
    stopped = Message(text="Claude Code stopped with an error.", level="warn")
    assert _with_cause(stopped, mh.DOCKER_DOWN).text == (
        mh.DOCKER_DOWN + " (Claude Code stopped with an error.)"
    )
    # Nothing found this turn, or a warning of another kind: as it was.
    assert _with_cause(stopped, "") is stopped
    other = Message(text="The reviewer has notes.", level="warn")
    assert _with_cause(other, mh.DOCKER_DOWN) is other
    # Claude Code's own stderr says why.
    assert claude_exit_text("Error: ENOSPC: no space left on device, write") == (
        mh.disk_full(None)
    )


class _NeverAsked(FakeBridge):
    asked = 0

    async def send(self, prompt: Any) -> AsyncIterator[Any]:
        _NeverAsked.asked += 1
        yield Done(ok=True)


def test_a_turn_on_a_full_disk_stops_before_the_agent_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def full() -> str:
        return FULL

    monkeypatch.setattr(stdio.machine_health, "checker", lambda _project: full)
    events = serve(tmp_path, monkeypatch, _NeverAsked, 1)
    assert _NeverAsked.asked == 0
    assert {"kind": "progress", "text": FULL} in events
    warned = [
        e for e in events if e.get("kind") == "message" and e.get("level") == "warn"
    ]
    assert [e["text"] for e in warned] == [FULL]
    assert {"kind": "done", "ok": False}.items() <= next(
        e for e in events if e.get("kind") == "done"
    ).items()
    assert next(e for e in events if e.get("kind") == "timing")["outcome"] == "failed"
