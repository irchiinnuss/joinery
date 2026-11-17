"""Shared test setup."""

from __future__ import annotations

import pytest

from joinery.agents import AGENTS, JOINERY_AGENT_ENV


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
