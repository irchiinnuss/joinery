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
