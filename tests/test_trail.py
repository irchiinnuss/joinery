"""The work trail: every agent's tool calls as the same short lines, as they happen."""

from __future__ import annotations

import asyncio
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from bridge import trail
from bridge.acp_session import acp_trail
from bridge.codex_session import codex_trail
from bridge.protocol import Prompt, Trail
from bridge.turn_runner import picture_path
from joinery.headless import describe as headless_line


@pytest.mark.parametrize(
    ("name", "arguments", "line"),
    [
        ("Read", {"file_path": "/p/game/level.gd"}, "Reading level.gd"),
        ("read_file", {"absolute_path": "/p/game/level.gd"}, "Reading level.gd"),
        ("Grep", {"pattern": "Spawn"}, "Searching for Spawn"),
        ("mcp__joinery__search_project", {"pattern": "Spawn"}, "Searching for Spawn"),
        ("Glob", {"pattern": "**/*.tscn"}, "Looking through **/*.tscn"),
        ("Edit", {"file_path": "game/player.gd"}, "Editing player.gd"),
        ("Write", {"file_path": "game/new.gd", "content": "x"}, "Writing new.gd"),
        ("Bash", {"command": "rg 'Spawn' game"}, "Searching for Spawn"),
        ("Bash", {"command": "uv run pytest -q"}, "Running the tests"),
        (
            "shell",
            {"command": ["bash", "-lc", "cat game/level.gd"]},
            "Reading level.gd",
        ),
        ("WebSearch", {"query": "godot tween"}, "Searching the web for godot tween"),
        ("TodoWrite", {}, "Updating the plan"),
        ("mcp__joinery__view_editor", {}, "Looking at your view"),
        (
            "mcp__joinery__edit_scene",
            {"edit": {"summary": "Put the mug on the table"}},
            "Editing the scene",
        ),
        ("mcp__joinery__submit_for_review", {}, "Handing the change over"),
        ("mcp__joinery__preview_asset", {"path": "art/mug.glb"}, "Looking at mug.glb"),
        ("mystery_tool", {}, "Mystery tool"),
    ],
)
def test_each_call_reads_as_a_short_line(name: str, arguments: Any, line: str) -> None:
    assert trail.describe(name, arguments) == line


def test_a_tool_in_an_app_says_what_it_makes_and_where() -> None:
    call = {
        "server": "blender",
        "tool": "blender_batch",
        "arguments": {
            "source": "art/props/mug.blend",
            "steps": [
                {"op": "transform_parts", "arguments": {}},
                {"op": "apply_scale", "arguments": {}},
            ],
        },
    }
    assert (
        trail.describe("mcp__joinery__call_tool", call)
        == "Modelling the mug in Blender: transform parts, apply scale"
    )
    single = {
        "server": "blender",
        "tool": "reduce_triangles",
        "arguments": {"source": "art/props/mug.blend", "ratio": 0.5},
    }
    assert (
        trail.describe("joinery.call_tool", single)
        == "Modelling the mug in Blender: reduce triangles, 0.5 ratio"
    )
    paint = {"server": "krita", "tool": "paint_map", "arguments": {"source": "x.blend"}}
    assert trail.describe("call_tool", paint).startswith("Painting the x in Krita")
    read = {"server": "godot", "tool": "read_file", "arguments": {"filePath": "a/b.gd"}}
    assert trail.describe("call_tool", read) == "Reading b.gd"


def test_an_edit_says_its_file_and_lines_added_and_removed() -> None:
    edit = {
        "file_path": "game/player.gd",
        "old_string": "a\nb",
        "new_string": "a\nc\nd",
    }
    assert trail.edit_size("Edit", edit) == ("game/player.gd", 2, 1)
    write = {"file_path": "game/new.gd", "content": "a\nb\nc\n"}
    assert trail.edit_size("Write", write) == ("game/new.gd", 3, 0)
    godot = {
        "server": "godot",
        "tool": "write_file",
        "arguments": {"filePath": "res://x.gd", "patch": [{"old": "a", "new": "b\nc"}]},
    }
    assert trail.edit_size("mcp__joinery__call_tool", godot) == ("res://x.gd", 2, 1)
    patch = "*** Begin Patch\n*** Update File: game/a.gd\n@@\n-x\n+y\n+z\n*** End Patch"
    assert trail.edit_size("apply_patch", {"input": patch}) == ("game/a.gd", 2, 1)
    assert trail.edit_size("Read", {"file_path": "x"}) == ("", 0, 0)


def test_thoughts_are_one_short_sentence() -> None:
    assert trail.thought("**First** I'll read the level. Then the player.") == (
        "First I'll read the level."
    )
    assert len(trail.thought("word " * 100)) <= trail.THOUGHT_WIDTH
    assert trail.thinking("   ") is None


def test_a_call_runs_then_ends_under_one_id_with_a_picture_it_made() -> None:
    calls = trail.Calls()
    started = calls.start("c1", "Edit", {"file_path": "a.gd", "new_string": "x"})
    assert (started.id, started.state, started.file, started.added) == (
        "c1",
        "running",
        "a.gd",
        1,
    )
    ended = calls.end("c1", True, [{"type": "text", "text": "Saved res://art/mug.png"}])
    assert ended is not None
    assert (ended.state, ended.text, ended.picture) == (
        "done",
        "Editing a.gd",
        "res://art/mug.png",
    )
    assert calls.end("c1") is None
    calls.start("", "Read", {"file_path": "b.gd"})
    assert [e.state for e in calls.end_all()] == ["done"]


def test_codex_items_join_the_trail() -> None:
    calls = trail.Calls()
    command = codex_trail(
        calls, {"id": "1", "type": "commandExecution", "command": "ls"}
    )
    assert command is not None and command.text == "Looking through files"
    change = {
        "id": "2",
        "type": "fileChange",
        "changes": [{"path": "/p/game/level.gd", "diff": "@@\n-a\n+b\n+c"}],
    }
    edited = codex_trail(calls, change)
    assert edited is not None
    assert (edited.text, edited.added, edited.removed) == ("Editing level.gd", 2, 1)
    assert codex_trail(calls, {"id": "3", "type": "agentMessage"}) is None


def test_acp_tool_calls_join_the_trail() -> None:
    calls = trail.Calls()
    read = acp_trail(
        calls,
        {
            "toolCallId": "r",
            "title": "ReadFile",
            "kind": "read",
            "locations": [{"path": "/p/game/level.gd"}],
        },
    )
    assert read.text == "Reading level.gd"
    edit = acp_trail(
        calls,
        {
            "toolCallId": "e",
            "title": "Edit",
            "kind": "edit",
            "content": [
                {"type": "diff", "path": "/p/a.gd", "oldText": "a", "newText": "b\nc"}
            ],
        },
    )
    assert (edit.text, edit.file, edit.added, edit.removed) == (
        "Editing a.gd",
        "a.gd",
        2,
        1,
    )
    joinery = acp_trail(
        calls,
        {
            "toolCallId": "j",
            "title": "call_tool (joinery MCP Server)",
            "rawInput": {"server": "godot", "tool": "read_file", "arguments": {}},
        },
    )
    assert joinery.text == "Read file in Godot"
    other = acp_trail(calls, {"toolCallId": "o", "title": "Thinking about it"})
    assert other.text == "Thinking about it"


def test_a_picture_is_found_in_the_stage_first(tmp_path: Path) -> None:
    class Stage:
        stage = tmp_path / "stage"

    class Gateway:
        project = tmp_path / "project"
        transaction = Stage()

    (tmp_path / "stage/art").mkdir(parents=True)
    (tmp_path / "project/art").mkdir(parents=True)
    (tmp_path / "stage/art/mug.png").write_bytes(b"png")
    (tmp_path / "project/art/old.png").write_bytes(b"png")
    assert picture_path(Gateway(), "res://art/mug.png") == str(
        tmp_path / "stage/art/mug.png"
    )
    assert picture_path(Gateway(), "res://art/old.png") == str(
        tmp_path / "project/art/old.png"
    )
    assert picture_path(Gateway(), "res://art/gone.png") == ""


def test_joinery_run_prints_the_same_trail() -> None:
    running = Trail(id="1", text="Editing level.gd", added=12, removed=3).model_dump()
    assert headless_line(running) == "  › Editing level.gd  +12 −3"
    thought = Trail(text="I'll widen the door first.", note="thought").model_dump()
    assert headless_line(thought) == "  … I'll widen the door first."
    failed = Trail(id="1", text="Running the tests", state="failed").model_dump()
    assert headless_line(failed) == "  ✗ Running the tests"
    done = Trail(id="1", text="Reading level.gd", state="done").model_dump()
    assert headless_line(done) == ""


#: A Claude Code stand-in that thinks, reads a file and edits one.
FAKE_CLAUDE = """#!/usr/bin/env python3
import json, sys
sys.stdin.read()
def say(e):
    print(json.dumps(e), flush=True)
say({"type": "assistant", "message": {"content": [
    {"type": "thinking", "thinking": "The spawn point lives in level.gd. I'll look."},
    {"type": "tool_use", "id": "a", "name": "Read",
     "input": {"file_path": "/p/game/level.gd"}}]}})
say({"type": "user", "message": {"content": [
    {"type": "tool_result", "tool_use_id": "a", "is_error": False, "content": "x"}]}})
say({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "b", "name": "Edit", "input": {
        "file_path": "/p/game/level.gd", "old_string": "a", "new_string": "b\\nc"}}]}})
say({"type": "result", "subtype": "success", "is_error": False})
"""


@pytest.mark.skipif(sys.platform == "win32", reason="the stub is a shell script")
def test_claude_code_shows_its_work_as_it_goes(tmp_path: Path) -> None:
    import bridge.claude_session as cs
    from bridge.claude_session import ClaudeSession

    script = tmp_path / "claude"
    script.write_text(FAKE_CLAUDE)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    session = ClaudeSession()
    session.executable = str(script)
    session.gateway_socket = tmp_path / "gw.sock"
    session.project = tmp_path

    async def go() -> list[Trail]:
        found: list[Trail] = []
        async for event in session.send(Prompt(text="hi", project_dir=str(tmp_path))):
            if isinstance(event, Trail):
                found.append(event)
        return found

    real = cs.readonly_command
    cs.readonly_command = lambda argv, _p: argv  # type: ignore[assignment]
    try:
        seen = [(e.note, e.state, e.text) for e in asyncio.run(go())]
    finally:
        cs.readonly_command = real
    assert seen == [
        ("thought", "done", "The spawn point lives in level.gd."),
        ("tool", "running", "Reading level.gd"),
        ("tool", "done", "Reading level.gd"),
        ("tool", "running", "Editing level.gd"),
        # The stream ended before its result: done with it.
        ("tool", "done", "Editing level.gd"),
    ]
