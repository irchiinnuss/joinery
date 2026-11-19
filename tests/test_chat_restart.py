"""The editor restarting its chat (another agent, Reconnect) keeps changes in review,
and the chat host ends with its editor."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bridge import stdio
from joinery.gateway import ProjectGateway
from joinery.transactions import HELD, FileTransaction
from tests.test_chats import FakeBridge, FakeGateway, FakeRPC


def test_shutdown_keeps_reviews_and_ends_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    events: list[dict[str, Any]] = []
    gateways: list[Any] = []

    def gateway(project: Path) -> FakeGateway:
        made = FakeGateway(project)
        made.keep_reviews = False  # type: ignore[attr-defined]
        gateways.append(made)
        return made

    monkeypatch.setattr(stdio, "emit", events.append)
    monkeypatch.setattr(stdio, "Bridge", FakeBridge)
    monkeypatch.setattr(stdio, "ProjectGateway", gateway)
    monkeypatch.setattr(stdio, "GatewayRPC", FakeRPC)
    monkeypatch.setattr(stdio, "Specialists", lambda *_a: AsyncMock())
    monkeypatch.setattr(stdio, "game_suggestions", lambda _p: {"kind": "suggestions"})

    async def reader(_stream: Any) -> tuple[asyncio.StreamReader, Any]:
        stream = asyncio.StreamReader()
        stream.feed_data(b'{"kind": "shutdown", "keep_reviews": true}\n')
        # Never read: the host has gone by then.
        stream.feed_data(b'{"kind": "prompt", "text": "Door", "chat_id": ""}\n')
        return stream, type("T", (), {"close": lambda self: None})()

    monkeypatch.setattr(stdio, "input_reader", reader)
    assert asyncio.run(stdio.serve(tmp_path, "claude")) == 0
    assert gateways[0].keep_reviews is True
    assert gateways[0].held_by == "chat restart"
    assert not any(e.get("kind") == "done" for e in events)


def test_a_change_kept_by_a_restart_says_who_kept_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))

    async def go() -> str:
        gateway = ProjectGateway(tmp_path)
        tx = FileTransaction(tmp_path)
        tx.begin("Note")
        (tx.stage / "note.txt").write_text("staged")
        tx.review()
        gateway.transaction = tx
        gateway.keep_reviews = True
        gateway.held_by = "chat restart"
        await gateway.close()
        assert FileTransaction.load(tmp_path, tx.id).state == "review"
        return str(json.loads((tx.directory / HELD).read_text())["by"])

    assert asyncio.run(go()) == "chat restart"


def _quiet_host(monkeypatch: pytest.MonkeyPatch, bridge: type = FakeBridge) -> None:
    monkeypatch.setattr(stdio, "emit", lambda _event: None)
    monkeypatch.setattr(stdio, "Bridge", bridge)
    monkeypatch.setattr(stdio, "ProjectGateway", FakeGateway)
    monkeypatch.setattr(stdio, "GatewayRPC", FakeRPC)
    monkeypatch.setattr(stdio, "Specialists", lambda *_a: AsyncMock())
    monkeypatch.setattr(stdio, "game_suggestions", lambda _p: {"kind": "suggestions"})
    monkeypatch.setattr(stdio, "EDITOR_POLL_SECONDS", 0.02)


def test_the_host_ends_with_its_editor_while_the_pipe_stays_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crashed editor's game kept the chat's stdin open, so no EOF ever came."""
    (tmp_path / "project.godot").write_text("config_version=5\n")
    closed: list[str] = []

    class Closing(FakeBridge):
        async def close(self) -> None:
            closed.append("agent")

    class Transport:
        def close(self) -> None:
            closed.append("input")

    _quiet_host(monkeypatch, Closing)

    async def reader(_stream: Any) -> tuple[asyncio.StreamReader, Any]:
        return asyncio.StreamReader(), Transport()  # Open, and never written to.

    monkeypatch.setattr(stdio, "input_reader", reader)
    editor = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    async def go() -> int:
        host = asyncio.create_task(
            stdio.serve(tmp_path, "claude", editor_pid=editor.pid)
        )
        await asyncio.sleep(0.3)
        assert not host.done(), "the host runs while its editor does"
        editor.kill()
        editor.wait()
        return await asyncio.wait_for(host, 10)

    try:
        assert asyncio.run(go()) == 0
    finally:
        if editor.poll() is None:
            editor.kill()
            editor.wait()
    assert "input" in closed


def test_without_an_editor_pid_the_host_waits_for_its_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``joinery run`` and connection checks start the host with no editor to follow."""
    (tmp_path / "project.godot").write_text("config_version=5\n")
    _quiet_host(monkeypatch)
    streams: list[asyncio.StreamReader] = []

    async def reader(_stream: Any) -> tuple[asyncio.StreamReader, Any]:
        streams.append(asyncio.StreamReader())
        return streams[0], type("T", (), {"close": lambda self: None})()

    monkeypatch.setattr(stdio, "input_reader", reader)

    async def go() -> int:
        host = asyncio.create_task(stdio.serve(tmp_path, "claude"))
        await asyncio.sleep(0.3)
        assert not host.done()
        streams[0].feed_eof()
        return await asyncio.wait_for(host, 10)

    assert asyncio.run(go()) == 0


class _NeverConnects(FakeBridge):
    """An agent whose connection never completes (a login prompt nobody answers)."""

    async def connect(self, _project: Path, _profile: str = "") -> None:
        await asyncio.Event().wait()


def test_a_host_still_connecting_ends_on_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    _quiet_host(monkeypatch, _NeverConnects)
    streams: list[asyncio.StreamReader] = []

    async def reader(_stream: Any) -> tuple[asyncio.StreamReader, Any]:
        streams.append(asyncio.StreamReader())
        return streams[0], type("T", (), {"close": lambda self: None})()

    monkeypatch.setattr(stdio, "input_reader", reader)

    async def go() -> int:
        host = asyncio.create_task(stdio.serve(tmp_path, "claude"))
        await asyncio.sleep(0.3)
        assert not host.done(), "the host waits for its agent"
        streams[0].feed_eof()
        return await asyncio.wait_for(host, 10)

    assert asyncio.run(go()) == 0


def test_a_host_still_connecting_ends_with_its_editor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    _quiet_host(monkeypatch, _NeverConnects)

    async def reader(_stream: Any) -> tuple[asyncio.StreamReader, Any]:
        return asyncio.StreamReader(), type("T", (), {"close": lambda self: None})()

    monkeypatch.setattr(stdio, "input_reader", reader)
    editor = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    async def go() -> int:
        host = asyncio.create_task(
            stdio.serve(tmp_path, "claude", editor_pid=editor.pid)
        )
        await asyncio.sleep(0.3)
        assert not host.done()
        editor.kill()
        editor.wait()
        return await asyncio.wait_for(host, 10)

    try:
        assert asyncio.run(go()) == 0
    finally:
        if editor.poll() is None:
            editor.kill()
            editor.wait()


def test_lines_sent_while_connecting_are_kept_for_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Watching for the end while connecting reads the input; what it read still counts."""
    (tmp_path / "project.godot").write_text("config_version=5\n")
    gateways: list[Any] = []

    class Slow(FakeBridge):
        async def connect(self, _project: Path, _profile: str = "") -> None:
            await asyncio.sleep(0.2)

    def gateway(project: Path) -> FakeGateway:
        gateways.append(FakeGateway(project))
        return gateways[-1]

    _quiet_host(monkeypatch, Slow)
    monkeypatch.setattr(stdio, "ProjectGateway", gateway)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(stdio, "emit", events.append)

    streams: list[asyncio.StreamReader] = []

    async def reader(_stream: Any) -> tuple[asyncio.StreamReader, Any]:
        streams.append(asyncio.StreamReader())
        streams[0].feed_data(b'{"kind": "no such thing"}\n')
        return streams[0], type("T", (), {"close": lambda self: None})()

    monkeypatch.setattr(stdio, "input_reader", reader)

    async def go() -> int:
        host = asyncio.create_task(stdio.serve(tmp_path, "claude"))
        await asyncio.sleep(0.6)  # Connected by now.
        streams[0].feed_data(b'{"kind": "shutdown", "keep_reviews": true}\n')
        return await asyncio.wait_for(host, 10)

    assert asyncio.run(go()) == 0
    assert gateways[0].keep_reviews is True
    said = [e.get("text") for e in events if e.get("kind") == "message"]
    assert "Unknown chat message." in said, "the early line was read after connecting"


def test_a_restart_while_connecting_keeps_reviews(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    gateways: list[Any] = []

    def gateway(project: Path) -> FakeGateway:
        gateways.append(FakeGateway(project))
        return gateways[-1]

    _quiet_host(monkeypatch, _NeverConnects)
    monkeypatch.setattr(stdio, "ProjectGateway", gateway)

    async def reader(_stream: Any) -> tuple[asyncio.StreamReader, Any]:
        stream = asyncio.StreamReader()
        stream.feed_data(b'{"kind": "shutdown", "keep_reviews": true}\n')
        return stream, type("T", (), {"close": lambda self: None})()

    monkeypatch.setattr(stdio, "input_reader", reader)
    assert asyncio.run(asyncio.wait_for(stdio.serve(tmp_path, "claude"), 10)) == 0
    assert gateways[0].keep_reviews is True
