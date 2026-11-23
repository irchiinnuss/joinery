"""call_tool and read_tool take the slips agents made in real sessions: ``name`` for
``tool``, a registry read with no server, and tool_schema called through them. The
hook and the gateway read the call in the same spelling, so the role and read
checks still judge what actually runs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from joinery import hook
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.policy import Severity
from joinery.read_access import call_target, missing_target
from servers.joinery_registry.proxy import _target


def registry(tool: str, values: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_name": f"mcp__joinery-registry__{tool}",
        "mcp_server": {"name": "joinery-registry", "source": "dynamic"},
        "tool_input": values,
    }


def test_name_is_read_as_tool() -> None:
    assert call_target({"server": "godot", "name": "read_file", "arguments": {}}) == {
        "server": "godot",
        "tool": "read_file",
        "arguments": {},
    }


def test_a_registry_read_with_no_server_is_the_registrys() -> None:
    assert call_target({"tool": "game_brief"})["server"] == "joinery-registry"
    assert call_target({"tool": "write_file"})["server"] == ""


def test_tool_schema_through_call_tool_keeps_the_server_it_named() -> None:
    target = call_target(
        {"server": "joinery-sound", "tool": "tool_schema", "arguments": {"tool": "x"}}
    )
    assert target["server"] == "joinery-registry"
    assert target["arguments"] == {"tool": "x", "server": "joinery-sound"}
    inner = '{"server": "godot", "tool": "write_file"}'
    target = call_target(
        {"server": "joinery-registry", "tool": "tool_schema", "arguments": inner}
    )
    assert target["arguments"] == inner


def test_a_call_naming_nothing_says_what_to_pass() -> None:
    assert "Name the tool" in missing_target(call_target({"server": "godot"}))
    assert "Name the server of write_file" in missing_target(
        call_target({"tool": "write_file"})
    )
    with pytest.raises(ValueError, match="Name the tool"):
        _target("godot", "", "", None)
    assert _target("godot", "", "read_file", None) == {
        "server": "godot",
        "tool": "read_file",
        "arguments": {},
    }


def test_the_hook_judges_read_tool_by_its_name_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    read = registry(
        "read_tool", {"server": "godot", "name": "read_file", "arguments": {}}
    )
    assert hook.violation(read) is None
    schema = registry(
        "read_tool",
        {"server": "joinery-sound", "tool": "tool_schema", "arguments": {"tool": "x"}},
    )
    assert hook.violation(schema) is None
    write = registry(
        "read_tool", {"server": "godot", "name": "write_file", "arguments": {}}
    )
    verdict = hook.violation(write)
    assert verdict is not None and "godot.write_file is not a read" in verdict.reason


def test_a_read_only_role_is_still_refused_a_write_named_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("JOINERY_AGENT_ROLE", "reviewer")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    call = registry(
        "call_tool",
        {"server": "godot", "name": "write_file", "arguments": {"content": "x"}},
    )
    verdict = hook.violation(call)
    assert verdict is not None and verdict.severity is Severity.DENY
    read = registry(
        "call_tool", {"server": "godot", "name": "read_file", "arguments": {}}
    )
    assert hook.violation(read) is None


def test_the_gateway_answers_tool_schema_called_through_call_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    from joinery.gateway import ProjectGateway

    schema = {"type": "object", "properties": {"text": {"type": "string"}}}
    tool = SimpleNamespace(
        model_dump=lambda **_k: {"name": "write", "inputSchema": schema}
    )

    async def go() -> None:
        gateway = ProjectGateway(tmp_path)

        async def connection(_server: str) -> Any:
            return SimpleNamespace(tools={"write": tool})

        gateway.connection = connection  # type: ignore[method-assign]
        lead = GatewayRPC(gateway)
        await lead.start()
        try:
            for method in ("call", "read"):
                found = await rpc(
                    lead.path,
                    method,
                    {
                        "server": "joinery-registry",
                        "tool": "tool_schema",
                        "arguments": {"server": "joinery-sound", "tool": "write"},
                    },
                )
                assert found["inputSchema"] == schema
            with pytest.raises(RuntimeError, match="server and name"):
                await rpc(
                    lead.path,
                    "call",
                    {
                        "server": "joinery-registry",
                        "tool": "tool_schema",
                        "arguments": {},
                    },
                )
            with pytest.raises(RuntimeError, match="no tool nope"):
                await rpc(lead.path, "schema", {"server": "x", "tool": "nope"})
            with pytest.raises(RuntimeError, match="tool of its own"):
                await rpc(lead.path, "schema", {"server": "x", "tool": "propose_todo"})
        finally:
            await lead.close()

    asyncio.run(go())
