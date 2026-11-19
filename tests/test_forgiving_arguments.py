"""Small argument slips agents made in real sessions are taken, not refused: an empty
content beside a patch, notes past the card's length, a seed on a Blender call, the
underscore pivot spelling, call_tool arguments sent as a JSON string and an
inspect_document without its app. What guards the project stays: a real content
beside a patch, a pivot that isn't one, arguments that aren't an object, and the
gateway's own judging of what the parsed arguments name."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.types import TextContent

from joinery import done_when
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, call_arguments, rpc
from joinery.mcp_runtime import input_problem
from joinery.peer_review import NOTE_CHARS, notes_from
from joinery.tools.blender_model_ops import pivot_name, set_pivot_code
from joinery.turn import STEP_NOTE_CHARS, TurnController
from servers import authoring
from servers.joinery_registry.proxy import make_server
from tests.test_crew import project, wire
from tests.test_small_context import project as small_project

__all__ = ["project"]


def run(step: Any) -> Any:
    return asyncio.run(step())


# --- write_file: content "" beside a patch -------------------------------------------


def test_an_empty_content_beside_a_patch_is_the_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = small_project(tmp_path, monkeypatch)
    (root / "player.gd").write_text("extends Node\nvar speed := 4.0\n")
    gateway = ProjectGateway(root)
    gateway.staging()
    patch = [{"find": "4.0", "replace": "6.0"}]
    arguments = gateway._patched(
        {"filePath": "res://player.gd", "content": "", "patch": patch}
    )
    assert arguments == {
        "filePath": "res://player.gd",
        "content": "extends Node\nvar speed := 6.0\n",
    }
    # A content that says something beside a patch is still two answers.
    for content in ("x", " ", "extends Node\n"):
        with pytest.raises(ValueError, match="not both"):
            gateway._patched(
                {"filePath": "player.gd", "content": content, "patch": patch}
            )


# --- call_tool arguments as a JSON string ---------------------------------------------


def test_call_arguments_take_an_object_or_a_json_string_of_one() -> None:
    assert call_arguments({"a": 1}) == {"a": 1}
    assert call_arguments('{"filePath": "a.gd"}') == {"filePath": "a.gd"}
    assert call_arguments("") == {}
    for bad in ("[1, 2]", '"a.gd"', "1", None, [1]):
        with pytest.raises(TypeError, match="must be an object"):
            call_arguments(bad)
    with pytest.raises(ValueError, match="JSON object"):
        call_arguments("{filePath: a.gd}")


def test_a_json_string_reaches_the_gateway_as_the_object_it_holds(
    project: Path,
) -> None:
    async def go() -> None:
        gateway = wire(ProjectGateway(project))
        seen: list[dict[str, Any]] = []
        real = gateway.call

        async def call(server: str, tool: str, arguments: Any, **kw: Any) -> Any:
            seen.append(arguments)
            return await real(server, tool, arguments, **kw)

        gateway.call = call  # type: ignore[method-assign]
        lead = GatewayRPC(gateway, None)
        await lead.start()
        target = str(project / "notes/new.txt")
        try:
            await rpc(
                lead.path,
                "call",
                {
                    "server": "joinery-sound",
                    "tool": "write",
                    "arguments": json.dumps({"path": target, "text": "hi\n"}),
                },
            )
            assert seen == [{"path": target, "text": "hi\n"}]
            # Parsed, it is judged like any other call's arguments: another
            # change's staged folder is still refused.
            folder = str(project / ".joinery/transactions/x/stage/a.txt")
            with pytest.raises(RuntimeError, match="staged"):
                await rpc(
                    lead.path,
                    "call",
                    {
                        "server": "joinery-sound",
                        "tool": "write",
                        "arguments": json.dumps({"path": folder, "text": "x"}),
                    },
                )
            with pytest.raises(RuntimeError, match="must be an object"):
                await rpc(
                    lead.path,
                    "call",
                    {"server": "joinery-sound", "tool": "write", "arguments": "[1]"},
                )
        finally:
            await lead.close()
            await gateway.close()

    run(go)


def test_the_registry_tools_accept_a_json_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[str, dict[str, Any]]] = []

    async def fake(_socket: Path, method: str, params: dict[str, Any]) -> Any:
        asked.append((method, params))
        return {"content": [], "isError": False}

    monkeypatch.setattr("servers.joinery_registry.proxy.rpc", fake)

    async def go() -> None:
        async with Client(make_server(tmp_path / "gateway.sock")) as client:
            for tool in ("call_tool", "read_tool"):
                result = await client.call_tool(
                    tool,
                    {"server": "godot", "tool": "read_file", "arguments": '{"a": 1}'},
                )
                assert not result.is_error

    run(go)
    # The string reaches the gateway, which parses it (call_arguments) or finds it
    # parsed already.
    assert [call_arguments(params["arguments"]) for _, params in asked] == [
        {"a": 1},
        {"a": 1},
    ]


# --- Notes past the card's length ------------------------------------------------------


def test_a_long_step_note_is_cut_short_and_said_so(project: Path) -> None:
    events: list[dict[str, Any]] = []
    controller = TurnController(wire(ProjectGateway(project)), events.append, "claude")
    controller.begin("Make a mower")
    controller._unattended = lambda: True  # type: ignore[method-assign]
    controller.propose_todo({"steps": ["Mower body", "Paint it"]})
    long = "The shed isn't built yet, so " + "the mower waits by the gate " * 12
    answer = controller.todo_step({"step": 2, "state": "skipped", "note": long})
    assert answer["status"] == "ok" and "cut short" in answer["trimmed"]
    note = events[-1]["note"]
    assert len(note) == STEP_NOTE_CHARS and note.endswith("…")
    short = controller.todo_step({"step": 1, "state": "done", "note": "Made"})
    assert "trimmed" not in short
    with pytest.raises(TypeError, match="short line"):
        controller.todo_step({"step": 1, "state": "done", "note": ["a"]})


def test_a_long_step_or_done_when_is_cut_short_and_said_so() -> None:
    trimmed: list[str] = []
    todo = done_when.proposal(
        {
            "steps": [
                {"title": "Mower " * 150, "detail": "60 cm " * 200},
                "Paint it " * 100,
            ],
            "done_when": [{"text": "The mower looks right " * 20, "kind": "look"}],
        },
        trimmed=trimmed,
    )
    assert len(todo.done_when[0].text) == 160
    assert todo.done_when[0].text.endswith("…")
    assert all(len(t) <= done_when.DETAIL_CHARS for t in todo.steps)
    assert any("done_when text" in t for t in trimmed)
    assert any("step title" in t for t in trimmed)
    # Without a list to say it in, the old refusal stands; an empty step never passes.
    with pytest.raises(ValueError, match="under 160"):
        done_when.proposal(
            {"steps": ["a"], "done_when": [{"text": "x" * 200, "kind": "look"}]}
        )
    with pytest.raises(ValueError, match="short line"):
        done_when.proposal({"steps": [""]}, trimmed=[])


def test_propose_todo_tells_the_agent_what_was_cut(project: Path) -> None:
    controller = TurnController(wire(ProjectGateway(project)), lambda _e: None, "x")
    controller.begin("Make a mower")
    answer = controller.propose_todo(
        {
            "steps": ["Mower body", "Paint it"],
            "done_when": [{"text": "It looks right " * 20, "kind": "look"}],
        }
    )
    assert answer["trimmed"] == [
        "A done_when text ran past 160 characters and was cut short."
    ]


def test_a_long_review_note_is_cut_short_and_said_so(project: Path) -> None:
    notes = notes_from({"notes": [{"severity": "warn", "text": "word " * 200}]})
    assert len(notes[0].text) == NOTE_CHARS and notes[0].text.endswith("…")
    for bad in ({"severity": "warn", "text": " "}, {"severity": "loud", "text": "a"}):
        with pytest.raises(ValueError):
            notes_from({"notes": [bad]})

    async def go() -> None:
        gateway = wire(ProjectGateway(project))
        reviewer = GatewayRPC(gateway, None, role="reviewer")
        await reviewer.start()
        try:
            data = await rpc(
                reviewer.path,
                "report_review",
                {"notes": [{"severity": "info", "text": "long " * 200}]},
            )
            assert data["recorded"] == 1 and "cut short" in data["trimmed"]
        finally:
            await reviewer.close()
            await gateway.close()

    run(go)


def test_the_registry_schemas_take_long_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(_socket: Path, _method: str, _params: dict[str, Any]) -> Any:
        return {"status": "ok"}

    monkeypatch.setattr("servers.joinery_registry.proxy.rpc", fake)

    async def go() -> None:
        async with Client(make_server(tmp_path / "gateway.sock")) as client:
            for tool, arguments in (
                ("todo_step", {"step": 1, "state": "done", "note": "n" * 300}),
                (
                    "propose_todo",
                    {"steps": [{"title": "t" * 200, "detail": "d" * 900}]},
                ),
                ("report_review", {"notes": [{"severity": "info", "text": "x" * 900}]}),
            ):
                result = await client.call_tool(tool, arguments)
                assert not result.is_error, (tool, result.content)

    run(go)


# --- Blender: seed, pivot spelling, inspect_document's app ----------------------------


@pytest.fixture
def tools_project(tmp_path: Path) -> Path:
    (tmp_path / "joinery.toml").write_text(
        '[tools]\nenabled=["blender","krita","supercollider","surge-xt"]\n'
    )
    return tmp_path


def request(project: Path, family: str, tool: str, arguments: dict[str, Any]) -> Any:
    async def go() -> Any:
        async with Client(authoring.make_server(project, family)) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(go())


def test_a_seed_on_a_blender_call_is_ignored_and_said_so(
    tools_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation = AsyncMock(return_value={"status": "staged"})
    monkeypatch.setattr(authoring, "edit_document", operation)
    arguments = {
        "tool": "blender",
        "source": "art/sources/chair.blend",
        "code": "pass",
        "exports": ["art/models/chair.glb"],
        "seed": 7,
    }
    result = request(
        tools_project, "joinery-blender-ops", "operate_document", arguments
    )
    assert not result.is_error
    (text,) = result.content
    assert isinstance(text, TextContent)
    data = json.loads(text.text)
    assert data["status"] == "staged" and "seed" in data["ignored"][0]
    assert "seed" not in operation.call_args.kwargs
    # SuperCollider still takes it.
    operation.reset_mock()
    patch = {**arguments, "tool": "supercollider", "source": "audio/sources/a.scd"}
    result = request(tools_project, "joinery-sound", "operate_document", patch)
    assert not result.is_error and operation.call_args.kwargs["seed"] == 7


@pytest.mark.parametrize(
    "spelling,name",
    [
        ("bottom-centre", "bottom-centre"),
        ("bottom_centre", "bottom-centre"),
        ("bottom_center", "bottom-centre"),
        ("Bottom Center", "bottom-centre"),
        ("centre", "centre"),
        ("center", "centre"),
    ],
)
def test_pivot_spellings(spelling: str, name: str) -> None:
    assert pivot_name(spelling) == name
    assert f"_joinery_set_pivot({name!r})" in set_pivot_code(spelling)


@pytest.mark.parametrize("bad", ["top", "bottom", "", "bottom-centre'); import os #"])
def test_a_pivot_that_isnt_one_is_still_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="bottom-centre"):
        set_pivot_code(bad)


def test_inspect_document_finds_the_app_from_the_source(
    tools_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspect = AsyncMock(return_value={"objects": []})
    monkeypatch.setattr(authoring, "inspect_native", inspect)
    source = "res://art/sources/hedge.blend"
    result = request(
        tools_project, "joinery-blender-ops", "inspect_document", {"source": source}
    )
    assert not result.is_error
    assert inspect.call_args.args[1:] == ("blender", source)
    # A file type this server has no single app for still needs the app named.
    result = request(
        tools_project, "joinery-blender-ops", "inspect_document", {"source": "a.kra"}
    )
    assert result.is_error and "Name the tool" in result.content[0].text


def test_a_missing_argument_is_named() -> None:
    schema = {
        "type": "object",
        "properties": {"tool": {"type": "string"}, "source": {"type": "string"}},
        "required": ["tool", "source"],
    }
    error = next(Draft202012Validator(schema).iter_errors({"source": "a.blend"}))
    assert input_problem(error, schema) == "missing tool (it takes source, tool)."
    error = next(Draft202012Validator(schema).iter_errors({"tool": 1, "source": "a"}))
    assert input_problem(error, schema) == "type constraint failed."
