"""A hung Docker gives a clear error, never an hour of waiting."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from joinery import docker_cli, runner, warm_workers
from joinery.authoring import WorkerTimeout

RESTART = "Docker isn't responding — restart Docker Desktop"


@pytest.fixture
def hung_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `docker` on PATH that never answers, as a stuck Docker Desktop."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\nexec sleep 30\n")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(docker_cli, "PROBE", 0.3)
    return docker


def test_a_quick_call_that_gets_no_answer_says_to_restart_docker(
    hung_docker: Path,
) -> None:
    with pytest.raises(docker_cli.DockerNotResponding) as error:
        docker_cli.run(["docker", "image", "inspect", "x"], timeout=0.3)
    assert str(error.value).startswith(RESTART)
    assert "docker image inspect" in str(error.value)
    assert docker_cli.responding(0.3) is False
    assert asyncio.run(docker_cli.responding_async(0.3)) is False


def test_an_answering_docker_is_responding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text("#!/bin/sh\necho 27.0\n")
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert docker_cli.responding(5) is True
    assert asyncio.run(docker_cli.responding_async(5)) is True


def test_the_sandbox_runner_names_a_hung_docker(
    hung_docker: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = runner._timed(["docker", "run", "img"], timeout=1)
    assert not got.ok and got.stderr == docker_cli.NOT_RESPONDING
    # "Is the worker image there?" no longer reads a hung Docker as "no".
    monkeypatch.setattr(docker_cli, "QUICK", 1)
    with pytest.raises(docker_cli.DockerNotResponding):
        runner.SandboxRunner().available()


def test_a_slow_tool_is_still_a_timeout_not_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow(*_a: Any, **_k: Any) -> Any:
        raise subprocess.TimeoutExpired("docker", 1)

    monkeypatch.setattr(subprocess, "run", slow)
    monkeypatch.setattr(docker_cli, "responding", lambda *_a: True)
    got = runner._timed(["docker", "run", "img"], timeout=1)
    assert got.stderr == "timed out after 1s"


def test_a_warm_worker_command_names_a_hung_docker(hung_docker: Path) -> None:
    with pytest.raises(docker_cli.DockerNotResponding):
        asyncio.run(warm_workers._command(["docker", "exec", "w", "true"], 1))


def test_a_slow_warm_command_is_still_a_worker_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def answers(*_a: Any) -> bool:
        return True

    monkeypatch.setattr(docker_cli, "responding_async", answers)
    with pytest.raises(WorkerTimeout):
        asyncio.run(warm_workers._command(["sleep", "5"], 1))
