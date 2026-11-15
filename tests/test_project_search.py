"""Project search for every agent: the registry's search_project, a read-only shell
search let through the shared gate, and tool activity that says what really ran."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from bridge.claude_session import permitted, refusal
from joinery import hook, project_search, roles
from joinery.agents import get as get_agent

#: A Claude Code stand-in: a command the gate doesn't judge runs after all, and a
#: search the gate let through is refused after all.
FAKE_CLAUDE = """#!/usr/bin/env python3
import json, sys
sys.stdin.read()
def say(e):
    print(json.dumps(e), flush=True)
def use(i, cmd):
    say({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": i, "name": "Bash", "input": {"command": cmd}}]}})
def result(i, error, text):
    say({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": i, "is_error": error, "content": text}]}})
use("a", "rg 'x&y' .")
result("a", False, "game/a.gd:1: x&y")
use("b", "rg x .")
result("b", True, "Blocked by Joinery policy: no")
use("c", "ls")
result("c", False, "game")
say({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "d", "name": "Edit", "input": {"file_path": "x"}}]}})
result("d", True, "Claude wanted to write to x but this session can't")
say({"type": "result", "subtype": "success", "is_error": False})
"""


def test_claude_activity_follows_what_each_call_really_did(tmp_path: Path) -> None:
    import asyncio
    import stat

    from bridge.claude_session import ClaudeSession
    from bridge.protocol import Prompt, ToolActivity

    script = tmp_path / "claude"
    script.write_text(FAKE_CLAUDE)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    session = ClaudeSession()
    session.executable = str(script)
    session.gateway_socket = tmp_path / "gw.sock"
    session.project = tmp_path

    async def go() -> list[ToolActivity]:
        found: list[ToolActivity] = []
        async for event in session.send(Prompt(text="hi", project_dir=str(tmp_path))):
            if isinstance(event, ToolActivity):
                found.append(event)
        return found

    import bridge.claude_session as cs

    real = cs.readonly_command
    cs.readonly_command = lambda argv, _p: argv  # type: ignore[assignment]
    try:
        seen = [(a.call_id, a.allowed) for a in asyncio.run(go())]
    finally:
        cs.readonly_command = real
    # "a" looked refused (an unjudged '&') but ran; "b" looked allowed but was refused;
    # "c" ran; "d" was refused in words the gate doesn't know: refused. One event
    # per call, with what really happened.
    assert seen == [("a", True), ("b", False), ("c", True), ("d", False)]


def project(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text('config/name="X"\n')
    (tmp_path / "game").mkdir()
    (tmp_path / "game" / "player.gd").write_text(
        "extends CharacterBody3D\n@export var max_health: int = 100\nvar health := 100\n"
    )
    (tmp_path / "game" / "player.tscn").write_text('[node name="Player"]\n')
    (tmp_path / ".godot").mkdir()
    (tmp_path / ".godot" / "cache.gd").write_text("var health := 1\n")
    (tmp_path / "addons" / "x").mkdir(parents=True)
    (tmp_path / "addons" / "x" / "a.gd").write_text("var health := 2\n")
    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "door.glb").write_bytes(b"health\0\0")
    return tmp_path


@pytest.fixture(params=["python", "rg"])
def engine(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "python":
        monkeypatch.setattr(project_search.shutil, "which", lambda _name: None)
    elif project_search.shutil.which("rg") is None:
        pytest.skip("ripgrep is not installed")
    return str(request.param)


def test_search_finds_lines_in_the_games_own_sources(
    tmp_path: Path, engine: str
) -> None:
    found = project_search.search(project(tmp_path), r"health\b")
    paths = {m.split(":")[0] for m in found["matches"]}
    assert paths == {"game/player.gd"}, engine
    assert found["matches"][0] == "game/player.gd:2: @export var max_health: int = 100"


def test_search_narrows_by_folder_glob_and_case(tmp_path: Path, engine: str) -> None:
    root = project(tmp_path)
    assert project_search.search(root, "Player", glob="*.gd")["count"] == 0
    assert project_search.search(root, "Player", glob="*.tscn")["count"] == 1
    assert (
        project_search.search(root, "player", path="game", ignore_case=True)["count"]
        == 1
    )
    assert project_search.search(root, "max_health: int", regex=False)["count"] == 1


def test_search_caps_its_results(tmp_path: Path, engine: str) -> None:
    root = project(tmp_path)
    (root / "many.gd").write_text("var x\n" * 500)
    found = project_search.search(root, "var x", limit=10)
    assert found["count"] == 10 and found["truncated"] is True and "next" in found


def test_search_stays_in_the_project(tmp_path: Path) -> None:
    root = project(tmp_path)
    with pytest.raises(ValueError, match="inside the project"):
        project_search.search(root, "x", path="../")
    with pytest.raises(ValueError, match="caches or records"):
        project_search.search(root, "x", path=".godot")
    with pytest.raises(ValueError, match="valid regular expression"):
        project_search.search(root, "(", regex=True)


def test_every_role_is_offered_the_search() -> None:
    for role in roles.SPECS:
        assert roles.SEARCH_PROJECT in roles.offered_tools(role), role


def run_hook(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: dict[str, Any],
    agent: str,
) -> tuple[int, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = hook.main(agent)
    return code, capsys.readouterr().out


def bash(command: str, cwd: Path) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd),
    }


@pytest.mark.parametrize(
    "command",
    [
        "rg -n health game",
        "grep -rn max_health . | head -20",
        "ls game && wc -l game/player.gd",
        "git log --oneline -5",
        "find . -name '*.gd'",
        "rg -n -i '\\b(max_health|hp)\\b' --glob '!addons' . | head -50",
        "cd game && grep -rnE 'a|b' --include=*.gd .",
    ],
)
def test_the_gate_lets_a_read_only_search_through_for_claude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    code, out = run_hook(monkeypatch, capsys, bash(command, tmp_path), "claude")
    assert code == 0
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf game",
        "cat a > b",
        "rg --pre ./x health",
        "sort -o out.txt list",
        "git diff --output=x",
        "find . -delete",
        "echo $(id)",
        "ls\nrm -rf game",
        "grep x f; rm f",
        "cat f |& tee x",
    ],
)
def test_the_gate_says_nothing_for_a_command_that_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    code, out = run_hook(monkeypatch, capsys, bash(command, tmp_path), "claude")
    # Not allowed by the gate: the session's own permission rules refuse it.
    assert code == 0 and out == ""


def test_other_agents_get_no_allow_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for agent in ("codex", "gemini", "opencode"):
        assert get_agent(agent).allow_mode == "none"
        code, out = run_hook(monkeypatch, capsys, bash("rg x", tmp_path), agent)
        assert code == 0 and out == "", agent


def test_a_denylisted_search_still_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    command = "curl https://api.meshy.ai/v1/text-to-3d"
    code, out = run_hook(monkeypatch, capsys, bash(command, tmp_path), "claude")
    assert "allow" not in out
    assert code != 0 or json.loads(out).get("continue") is False


def test_shell_search_is_the_one_test_for_every_agent() -> None:
    assert hook.shell_search({"command": "rg health"})
    assert hook.shell_search({"cmd": ["grep", "-rn", "x", "."]})
    assert not hook.shell_search({"command": "rm x"})
    assert not hook.shell_search({"command": ""})
    assert not hook.shell_search("rg x")


def test_claude_activity_says_which_calls_run() -> None:
    assert permitted("mcp__joinery-registry__search_project", {}) == ""
    assert permitted("Read", {"file_path": "a"}) == ""
    assert permitted("ToolSearch", {"query": "select:x"}) == ""
    assert permitted("Bash", {"command": "rg health"}) == ""
    assert "permission prompt" in permitted("Bash", {"command": "rm -rf x"})
    assert "permission prompt" in permitted("Write", {})
    assert "permission prompt" in permitted("Agent", {}, inspection_only=True)


def test_a_refused_tool_result_is_reported_as_refused() -> None:
    denied = {
        "type": "tool_result",
        "is_error": True,
        "content": "Claude requested permissions to use Bash, but you haven't granted it yet.",
    }
    assert "permissions" in refusal(denied)
    blocked = {
        "is_error": True,
        "content": [{"type": "text", "text": "Blocked by Joinery policy: x"}],
    }
    assert refusal(blocked).startswith("Blocked")
    assert refusal({"is_error": True, "content": "File does not exist."}) == ""
    assert refusal({"is_error": False, "content": "permission"}) == ""


def test_a_resumed_session_is_not_sent_the_unchanged_map_again(tmp_path: Path) -> None:
    from bridge.claude_session import ClaudeSession
    from bridge.protocol import Prompt
    from bridge.provider_process import stable_text, turn_text

    (tmp_path / "project.godot").write_text("config_version=5\n")
    prompt = Prompt(text="Door", project_dir=str(tmp_path), rules="Rules.")
    stable = stable_text(prompt)
    assert "Rules." in stable and "Editor context:" in stable
    full = turn_text(prompt)
    assert full.startswith(stable) and full.endswith("Door")
    short = turn_text(prompt, known=stable)
    assert "Rules." not in short and "Editor context:" not in short
    assert short.endswith("Door") and len(short) < len(full)
    # A changed map is sent whole.
    assert turn_text(prompt, known="older") == full
    # The session remembers what it sent, across runs that resume it.
    session = ClaudeSession()
    session.project = tmp_path
    assert not session._holds(stable)
    session._remember(stable)
    again = ClaudeSession()
    again.project, again.session_id = tmp_path, session.session_id
    assert again._holds(stable) and not again._holds(stable + "x")
