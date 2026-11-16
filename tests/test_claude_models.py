"""Claude Code offers Opus, Sonnet and Haiku, and a chosen one reaches its --model."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from bridge.claude_session import (
    ClaudeSession,
    claude_configured_model,
    claude_model_choices,
)
from bridge.models import Selection, route
from bridge.protocol import Prompt
from joinery.wiring import decoded


def test_the_configured_alias_is_the_default() -> None:
    choices = claude_model_choices("claude-sonnet-4-5", ("low", "high"))
    assert [c.id for c in choices] == ["opus", "sonnet", "haiku"]
    assert [c.id for c in choices if c.is_default] == ["sonnet"]
    assert all(c.efforts == ("low", "high") for c in choices)


def test_nothing_configured_keeps_claude_codes_own_default() -> None:
    choices = claude_model_choices("", ())
    assert choices[0].id == "" and choices[0].is_default
    assert [c.id for c in choices[1:]] == ["opus", "sonnet", "haiku"]


def test_another_configured_model_comes_first() -> None:
    choices = claude_model_choices("my-proxy-model", ())
    assert choices[0].id == "my-proxy-model" and choices[0].is_default


def test_modes_route_to_haiku_and_opus() -> None:
    choices = claude_model_choices("sonnet", ())
    assert route(Selection(), "hello", choices)[0].id == "haiku"
    assert route(Selection(), "debug this script", choices)[0].id == "opus"
    assert route(Selection(), "make this better", choices)[0].id == "sonnet"


def test_configured_model_reads_env_then_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    assert claude_configured_model() == ""
    (tmp_path / "settings.json").write_text(json.dumps({"model": "opus"}))
    assert claude_configured_model() == "opus"
    monkeypatch.setenv("ANTHROPIC_MODEL", "haiku")
    assert claude_configured_model() == "haiku"


class Started(Exception):
    """Stops the session at its process launch, with the arguments it had."""


def launch_args(
    monkeypatch: pytest.MonkeyPatch, prompt: Prompt, sources: bool = False
) -> list[str]:
    session = ClaudeSession()
    session.setting_sources = sources
    session.executable = "claude"
    session.gateway_socket = Path("gateway.sock")
    seen: list[str] = []

    async def spawn(*argv: Any, **_kw: Any) -> Any:
        seen.extend(str(a) for a in argv)
        raise Started

    monkeypatch.setattr("bridge.claude_session.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("bridge.claude_session.readonly_command", lambda argv, _p: argv)

    async def go() -> None:
        async for _ in session.send(prompt):
            pass

    with pytest.raises(Started):
        asyncio.run(go())
    return seen


def test_a_chosen_model_is_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    args = launch_args(monkeypatch, Prompt(text="hi", project_dir="/p", model="opus"))
    assert args[args.index("--model") + 1] == "opus"
    assert "--model" not in launch_args(
        monkeypatch, Prompt(text="hi", project_dir="/p", model="")
    )


def test_a_session_skips_the_users_own_settings_but_signs_in_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "model": "opus",
                "env": {"ANTHROPIC_BASE_URL": "https://proxy.example"},
                "apiKeyHelper": "~/bin/key",
                "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]},
                "enabledPlugins": {"caveman": True},
            }
        )
    )
    prompt = Prompt(text="hi", project_dir="/p")
    args = launch_args(monkeypatch, prompt, sources=True)
    assert args[args.index("--setting-sources") + 1] == "project,local"
    settings = json.loads(args[args.index("--settings") + 1])
    assert settings["env"] == {"ANTHROPIC_BASE_URL": "https://proxy.example"}
    assert settings["apiKeyHelper"] == "~/bin/key" and settings["model"] == "opus"
    assert "enabledPlugins" not in settings
    # Only Joinery's gate among the hooks: the user's own don't reach the session.
    # On Windows the gate is an encoded PowerShell command; decoded, it reads the same.
    assert "joinery" in decoded(json.dumps(settings["hooks"]))
    assert '"Bash"' not in json.dumps(settings["hooks"])
    # An older CLI without the flag runs as before.
    old = launch_args(monkeypatch, prompt)
    assert "--setting-sources" not in old
    assert "env" not in json.loads(old[old.index("--settings") + 1])
