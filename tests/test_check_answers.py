"""A check that runs long or fails still answers the agent in words it can act on:
never a blank error, a time-out that names what ran, and no second run of the
same checks on an unchanged change."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from joinery import gateway_rpc
from joinery.errors import said
from joinery.gateway_rpc import GatewayRPC, rpc
from servers.errors import tool_errors
from tests.test_submit_for_review import Session

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


async def _ask(tmp_path: Path, handler: Handler, method: str) -> str:
    """Call ``method`` on a local gateway that answers with ``handler``."""
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    endpoint = tmp_path / "g.endpoint.json"
    endpoint.write_text(
        json.dumps({"version": 1, "host": "127.0.0.1", "port": port, "token": "t" * 32})
    )
    try:
        with pytest.raises(RuntimeError) as caught:
            await rpc(endpoint, method, {})
        return str(caught.value)
    finally:
        server.close()


def _raises(error: BaseException) -> str:
    @tool_errors
    async def precheck() -> None:
        raise error

    with pytest.raises(ToolError) as caught:
        asyncio.run(precheck())
    return str(caught.value)


@pytest.mark.parametrize(
    "error",
    [TimeoutError(), OSError(), RuntimeError(""), KeyError(""), TypeError()],
)
def test_no_tool_error_is_ever_blank(error: BaseException) -> None:
    text = _raises(error)
    assert text.strip() and text.strip() != "precheck"
    assert "precheck" in text


def test_a_timeout_says_it_timed_out() -> None:
    assert "timed out" in _raises(TimeoutError())
    assert "timed out" in said(TimeoutError(), "the check")
    assert said(ValueError("bad path"), "x") == "bad path"


def test_an_unanswered_check_says_what_ran_and_not_to_repeat_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gateway is still checking when the tool stops waiting: the agent hears
    that, how long it waited, and not to run the same check again."""

    async def slow(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()
        await asyncio.sleep(5)
        writer.close()

    monkeypatch.setattr(gateway_rpc, "RPC_SECONDS", 0.2)

    text = asyncio.run(_ask(tmp_path, slow, "precheck"))
    assert "precheck gave no answer within 0.2 s" in text
    assert "Godot's import" in text and "tests" in text
    assert "Don't call it again unchanged" in text


def test_a_gateway_that_closes_without_answering_says_so(tmp_path: Path) -> None:
    async def gone(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()
        writer.close()

    assert "closed without an answer" in asyncio.run(_ask(tmp_path, gone, "submit"))


def test_the_gateway_never_answers_with_a_blank_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = Session(tmp_path, monkeypatch)
    session.turn.begin("Make an ashtray")
    session.stage({"a.gd": "extends Node\n"})

    async def times_out() -> dict[str, Any]:
        raise TimeoutError

    monkeypatch.setattr(session.turn, "precheck", times_out)

    async def go() -> str:
        server = GatewayRPC(session.gateway)
        await server.start()
        try:
            with pytest.raises(RuntimeError) as caught:
                await rpc(server.path, "precheck", {})
            return str(caught.value)
        finally:
            await server.close()
            await session.gateway.close()

    text = asyncio.run(go())
    assert text.strip() and "timed out" in text and "precheck" in text
