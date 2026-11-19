"""Every Joinery tool a prompt, guide or refusal names is one the agent can call, by
the name that agent calls it: Claude Code, Codex, Gemini CLI and opencode each
prefix the joinery-registry server's tools their own way, and a bare name fails
("No such tool available")."""

from __future__ import annotations

import asyncio
import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from bridge import instructions
from bridge.codex_session import CodexSession
from bridge.instructions import RolePrompt
from bridge.protocol import Prompt
from bridge.provider_process import INSTRUCTIONS, prompt_text, turn_text
from joinery import hook
from joinery.agents import all_agents
from joinery.roles import REGISTRY_TOOLS, ROLE_ENV, offered_tools
from joinery.tool_names import (
    callable_name,
    callable_names,
    for_agent,
    registry_prefix,
)
from servers.joinery_registry.proxy import make_server

AGENTS = tuple(agent.id for agent in all_agents())

#: The role each role prompt is for: what it names must be offered to that role.
ROLE_PROMPTS = {
    "REVIEWER_PROMPT": "reviewer",
    "DEBUGGER_PROMPT": "debugger",
    "DESIGN_PROMPT": "designer",
    "PLAYTEST_PROMPT": "playtester",
    "SOUND_DESIGNER_PROMPT": "sound-designer",
    "CHECK_PROMPT": "accessibility",
    "DEBUG_FIX_PROMPT": "worker",
    "TASK_PROMPT": "worker",
    "STEP_PROMPT": "worker",
    "VARIANT_PROMPT": "worker",
}


def texts() -> dict[str, str]:
    """Every fixed text Joinery gives an agent: the instructions, each guide and each
    role prompt's rules and turn."""
    found = {"INSTRUCTIONS": INSTRUCTIONS}
    for name, value in vars(instructions).items():
        if isinstance(value, str) and name.isupper():
            found[name] = value
        elif isinstance(value, RolePrompt):
            found[name] = value.rules + "\n" + value.turn
    return found


def named(text: str, agent: str) -> list[str]:
    """The registry tools ``text`` names in ``agent``'s form."""
    prefix = re.escape(registry_prefix(agent))
    return re.findall(rf"(?<![\w-]){prefix}([A-Za-z0-9_]+)", text)


def test_each_agent_calls_the_registry_by_its_own_prefix() -> None:
    assert callable_name("claude", "todo_step") == "mcp__joinery-registry__todo_step"
    # Codex keeps letters, digits and _ in a server's name (its tool declarations
    # read "mcp__joinery_registry__call_tool").
    assert callable_name("codex", "call_tool") == "mcp__joinery_registry__call_tool"
    assert callable_name("gemini", "read_tool") == "mcp_joinery-registry_read_tool"
    assert callable_name("opencode", "precheck") == "joinery-registry_precheck"
    assert registry_prefix("unknown") == ""


def test_the_hook_reads_each_agents_form_back_as_the_registry_tool() -> None:
    for agent in AGENTS:
        for tool in ("report_diagnosis", "todo_step", "call_tool"):
            raw = hook.registry_probe(agent, Path("."))
            key = "tool" if "tool" in raw else "tool_name"
            raw[key] = callable_name(agent, tool)
            event = hook.normalise_event(raw)
            assert (event.server.replace("_", "-"), event.tool) == (
                "joinery-registry",
                tool,
            ), agent


def test_the_registry_offers_every_tool_joinery_names(tmp_path: Path) -> None:
    async def listed() -> set[str]:
        async with Client(make_server(tmp_path / "gateway.sock")) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    assert set(REGISTRY_TOOLS) <= asyncio.run(listed())


@pytest.mark.parametrize("agent", AGENTS)
def test_every_tool_named_in_the_instructions_exists_for_the_agent(agent: str) -> None:
    for name, text in texts().items():
        rendered = for_agent(text, agent) if name == "INSTRUCTIONS" else text
        rendered = callable_names(rendered, agent)
        tools = named(rendered, agent)
        missing = sorted(set(tools) - set(REGISTRY_TOOLS))
        assert not missing, (name, missing)
        role = ROLE_PROMPTS.get(name)
        if role is not None:
            # "Don't call start_task" names a tool the role is rightly not offered.
            told = re.sub(r"Don't call [^.:;]*", "", rendered)
            unoffered = sorted(set(named(told, agent)) - offered_tools(role))
            assert not unoffered, (name, role, unoffered)
        # No registry tool is left by its bare name, which the agent can't call.
        bare = re.sub(
            rf"(?<![\w-]){re.escape(registry_prefix(agent))}\w+", "", rendered
        )
        left = [t for t in REGISTRY_TOOLS if "_" in t and re.search(rf"\b{t}\b", bare)]
        assert not left, (name, left)


@pytest.mark.parametrize("agent", AGENTS)
def test_what_each_session_sends_names_tools_in_its_agents_form(agent: str) -> None:
    prompt = Prompt(
        text="Then call todo_step(2, 'done') and submit_for_review.",
        project_dir="",
        rules=instructions.DEBUGGER_PROMPT.rules,
    )
    sent = prompt_text(prompt, agent=agent)
    for tool in ("todo_step", "submit_for_review", "report_diagnosis", "call_tool"):
        assert callable_name(agent, tool) in sent
    assert "call todo_step(" not in sent
    turn = turn_text(prompt, agent=agent)
    assert callable_name(agent, "report_diagnosis") in turn


def test_codex_is_told_its_own_tool_names() -> None:
    session = CodexSession.__new__(CodexSession)
    session.project = Path(".")
    session.inspection_only = False
    told = session._thread_params()["developerInstructions"]
    assert "mcp__joinery_registry__submit_for_review" in told
    assert not re.search(r"(?<![\w-])submit_for_review", told)


def test_names_inside_paths_and_longer_words_are_left_alone() -> None:
    text = (
        "res://tools/call_tool.gd, my_call_tool_helper, call_tools, "
        "mcp__joinery-registry__todo_step and todo_step."
    )
    assert callable_names(text, "claude") == (
        "res://tools/call_tool.gd, my_call_tool_helper, call_tools, "
        "mcp__joinery-registry__todo_step and mcp__joinery-registry__todo_step."
    )


def test_a_refusal_names_the_report_tool_as_the_agent_calls_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    for name in ("CLAUDE_PROJECT_DIR", "CODEX_PROJECT_DIR", "JOINERY_POLICY_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(ROLE_ENV, "debugger")
    raw: dict[str, Any] = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "a.gd"},
        "cwd": str(tmp_path),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(raw)))
    hook.main("claude")
    said = capsys.readouterr()
    assert "mcp__joinery-registry__report_diagnosis" in said.out + said.err
