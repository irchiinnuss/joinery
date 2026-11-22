"""Shared test setup."""

from __future__ import annotations

import pytest

from joinery.agents import AGENTS, JOINERY_AGENT_ENV


@pytest.fixture(autouse=True)
def _no_remote_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chat hosts under test never start a Joinery Remote server of their own."""
    monkeypatch.setenv("JOINERY_REMOTE_NO_SERVER", "1")
    # Nor ask the machine's own Tailscale: a test that wants it installs a fake.
    monkeypatch.setenv("JOINERY_TAILSCALE", "none")


@pytest.fixture(autouse=True)
def _as_the_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests run as the user, even inside an agent's shell: a test that wants an
    agent sets its variables itself (joinery.agents.running_agent)."""
    for name in (*JOINERY_AGENT_ENV, *(n for a in AGENTS for n in a.shell_env)):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_local_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never ask the local Docker which worker images it has: a worker
    runs as the platform it would be built as (joinery.worker_platform)."""
    monkeypatch.setattr("joinery.worker_platform.present", lambda _image: None)


@pytest.fixture(autouse=True)
def _checks_on_the_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    """Checks run their stand-in workers on the stage itself, never on a copy in
    a real container (joinery.check_mirror): a test of the copy turns it on."""
    monkeypatch.setenv("JOINERY_CHECK_MIRROR", "0")


@pytest.fixture(autouse=True)
def _machine_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chat host never asks the real disk or Docker before a turn, and a
    worker's time-out is never this machine's free space
    (joinery.machine_health): a test of the check turns it on."""
    monkeypatch.setenv("JOINERY_MACHINE_CHECK", "off")
    monkeypatch.setattr("joinery.machine_health.MIN_FREE", 0)
