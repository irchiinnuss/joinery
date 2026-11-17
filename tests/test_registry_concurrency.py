"""The registry answers independent requests side by side: a quick call never waits
behind a slow one, reads run together, and writes to one change stay in order."""

import asyncio
from pathlib import Path
from typing import Any, Self

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from joinery import hook
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.mcp_runtime import MCPConnection, ServerSpec
from joinery.policy import Severity
from joinery.roles import READ_TOOL, REGISTRY_TOOLS, offered_tools
from joinery.rw_lock import ReadWriteLock
from servers.joinery_registry.proxy import make_server

SLOW = ("joinery-blender-ops", "operate_document")
READ = ("godot", "validate_script")
WRITE = ("godot", "write_file")


def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    (tmp_path / "player.gd").write_text("extends Node\n")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    return tmp_path


def text(value: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=value)])


class FakeTools:
    """Stands in for the downstream apps behind ProjectGateway._call."""

    def __init__(self) -> None:
        self.release_slow = asyncio.Event()
        self.slow_started = asyncio.Event()
        self.running = 0
        self.most = 0
        self.log: list[str] = []
        self.calls: list[tuple[str, str]] = []

    async def __call__(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        view: bool = False,
    ) -> CallToolResult:
        self.calls.append((server, tool))
        self.running += 1
        self.most = max(self.most, self.running)
        try:
            if (server, tool) == SLOW:
                self.slow_started.set()
                await self.release_slow.wait()
            elif (server, tool) == READ:
                self.log.append("read start")
                await asyncio.sleep(0.2)
                self.log.append("read end")
            elif (server, tool) == WRITE:
                # Read, wait, write: two of these at once would lose a line.
                path = Path(arguments["path"])
                before = path.read_text() if path.exists() else ""
                self.log.append("write start")
                await asyncio.sleep(0.1)
                path.write_text(before + arguments["line"] + "\n")
                self.log.append("write end")
            return text(f"{server}.{tool} done")
        finally:
            self.running -= 1


def test_a_slow_tool_call_does_not_delay_quick_registry_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        tools = FakeTools()
        gateway._call = tools  # type: ignore[method-assign]
        server = GatewayRPC(gateway)
        await server.start()
        try:
            slow = asyncio.create_task(
                rpc(
                    server.path,
                    "call",
                    {"server": SLOW[0], "tool": SLOW[1], "arguments": {}},
                )
            )
            await asyncio.wait_for(tools.slow_started.wait(), 5)
            loop = asyncio.get_running_loop()
            started = loop.time()
            found = await asyncio.wait_for(
                rpc(server.path, "inventory", {"query": "script"}), 5
            )
            searched = await asyncio.wait_for(
                rpc(server.path, "search", {"pattern": "extends"}), 5
            )
            assert loop.time() - started < 2
            assert "tools" in found
            assert searched
            assert not slow.done()
            tools.release_slow.set()
            assert (await asyncio.wait_for(slow, 5))["content"][0]["text"] == (
                "joinery-blender-ops.operate_document done"
            )
        finally:
            tools.release_slow.set()
            await server.close()
            await gateway.close()

    asyncio.run(run())


def test_reads_run_side_by_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        tools = FakeTools()
        gateway._call = tools  # type: ignore[method-assign]
        server = GatewayRPC(gateway)
        await server.start()
        try:
            loop = asyncio.get_running_loop()
            started = loop.time()
            answers = await asyncio.gather(
                *(
                    rpc(
                        server.path,
                        "read",
                        {"server": READ[0], "tool": READ[1], "arguments": {}},
                    )
                    for _ in range(4)
                )
            )
            assert len(answers) == 4
            assert tools.most == 4
            # Four 0.2 s reads one after another would take 0.8 s.
            assert loop.time() - started < 0.6
        finally:
            await server.close()
            await gateway.close()

    asyncio.run(run())


def test_two_writes_to_one_change_stay_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        tools = FakeTools()
        gateway._call = tools  # type: ignore[method-assign]
        target = tmp_path / "staged.txt"
        try:
            await asyncio.gather(
                *(
                    gateway.call(
                        WRITE[0], WRITE[1], {"path": str(target), "line": line}
                    )
                    for line in ("one", "two", "three")
                )
            )
            assert sorted(target.read_text().splitlines()) == ["one", "three", "two"]
            assert tools.most == 1
            assert tools.log == ["write start", "write end"] * 3
        finally:
            await gateway.close()

    asyncio.run(run())


def test_a_read_never_runs_beside_a_write_of_its_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        tools = FakeTools()
        gateway._call = tools  # type: ignore[method-assign]
        target = tmp_path / "staged.txt"
        try:
            write = asyncio.create_task(
                gateway.call(WRITE[0], WRITE[1], {"path": str(target), "line": "a"})
            )
            await asyncio.sleep(0.01)
            await gateway.call(READ[0], READ[1], {})
            await write
            assert tools.log == ["write start", "write end", "read start", "read end"]
            # A submit holds the lane alone: a read waits for it, as before.
            async with gateway.call_lock:
                read = asyncio.create_task(gateway.call(READ[0], READ[1], {}))
                await asyncio.sleep(0.05)
                assert not read.done()
            await read
        finally:
            await gateway.close()

    asyncio.run(run())


def test_calls_side_by_side_keep_their_own_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))

        async def noting(
            server: str, tool: str, arguments: dict[str, Any], *, view: bool = False
        ) -> CallToolResult:
            gateway._write_notes.append(f"note for {arguments['n']}")
            await asyncio.sleep(0.05)
            return text(f"result {arguments['n']}")

        gateway._call = noting  # type: ignore[method-assign]
        try:
            results = await asyncio.gather(
                *(gateway.call(READ[0], READ[1], {"n": n}) for n in range(3))
            )
            for n, result in enumerate(results):
                said = [getattr(block, "text", "") for block in result.content]
                assert said == [f"result {n}", f"note for {n}"]
        finally:
            await gateway.close()

    asyncio.run(run())


# --- read_tool: reads only ------------------------------------------------------------


def test_the_gateway_refuses_a_write_through_read_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        gateway = ProjectGateway(project(tmp_path, monkeypatch))
        tools = FakeTools()
        gateway._call = tools  # type: ignore[method-assign]
        server = GatewayRPC(gateway)
        await server.start()
        try:
            with pytest.raises(RuntimeError, match="Call it with call_tool"):
                await rpc(
                    server.path,
                    "read",
                    {
                        "server": WRITE[0],
                        "tool": WRITE[1],
                        "arguments": {"path": "x", "line": "y"},
                    },
                )
            assert tools.calls == []
        finally:
            await server.close()
            await gateway.close()

    asyncio.run(run())


def read_tool_event(agent_id: str, project: Path, tool: str) -> dict[str, Any]:
    """read_tool in ``agent_id``'s hook payload, calling godot ``tool``."""
    raw = hook.registry_probe(agent_id, project)
    arguments = {"server": "godot", "tool": tool, "arguments": {}}
    for key in ("tool_name", "tool"):
        if key in raw:
            raw[key] = str(raw[key]).replace("discover_tools", READ_TOOL)
    raw["tool_input" if "tool_input" in raw else "args"] = arguments
    return raw


@pytest.mark.parametrize("agent_id", ["claude", "codex", "gemini", "opencode"])
@pytest.mark.parametrize("role", ["", "lead", "worker", "reviewer"])
def test_the_hook_judges_read_tool_for_every_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_id: str, role: str
) -> None:
    root = project(tmp_path, monkeypatch)
    monkeypatch.delenv("JOINERY_STAGE", raising=False)
    monkeypatch.delenv("JOINERY_INSPECTION_ONLY", raising=False)
    if role:
        monkeypatch.setenv("JOINERY_AGENT_ROLE", role)
    else:
        monkeypatch.delenv("JOINERY_AGENT_ROLE", raising=False)
    assert hook.violation(read_tool_event(agent_id, root, "validate_script")) is None
    refused = hook.violation(read_tool_event(agent_id, root, "write_file"))
    assert refused is not None and refused.severity == Severity.DENY
    assert "call_tool" in refused.reason


def test_an_inspection_specialist_may_read_through_read_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    monkeypatch.delenv("JOINERY_AGENT_ROLE", raising=False)
    monkeypatch.delenv("JOINERY_STAGE", raising=False)
    monkeypatch.setenv("JOINERY_INSPECTION_ONLY", "1")
    assert hook.violation(read_tool_event("claude", root, "read_file")) is None
    refused = hook.violation(read_tool_event("claude", root, "write_file"))
    assert refused is not None and refused.severity == Severity.DENY


def test_read_tool_is_listed_read_only_to_every_role() -> None:
    assert READ_TOOL in REGISTRY_TOOLS
    for role in ("lead", "worker", "reviewer", "debugger", "designer"):
        assert READ_TOOL in offered_tools(role)

    async def listed() -> dict[str, Any]:
        tools = await make_server(Path("/nonexistent.sock")).list_tools()
        return {tool.name: tool for tool in tools}

    tools = asyncio.run(listed())
    annotations = tools[READ_TOOL].annotations
    assert annotations is not None and annotations.read_only_hint is True
    call = tools["call_tool"].annotations
    assert call is None or not call.read_only_hint


# --- One connection to a downstream server ---------------------------------------------


def connection_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, *tools: str
) -> MCPConnection:
    spec = ServerSpec(name=name, command="true")
    connection = MCPConnection(spec, tmp_path)
    for tool in tools:
        connection.tools[tool] = Tool(name=tool, input_schema={"type": "object"})
    monkeypatch.setattr("joinery.mcp_runtime.mcp_violation", lambda *a, **k: None)
    return connection


async def in_flight(connection: MCPConnection, *tools: str) -> int:
    """Send ``tools`` at once; how many reached the server before any was answered."""
    connection._task = asyncio.create_task(asyncio.sleep(3600))
    calls = [asyncio.create_task(connection.call(tool, {})) for tool in tools]
    await asyncio.sleep(0.05)
    waiting = connection._queue.qsize()
    while any(not call.done() for call in calls):
        request = await asyncio.wait_for(connection._queue.get(), 5)
        assert request is not None
        request.future.set_result(text("ok"))
        await asyncio.sleep(0.01)
    await asyncio.gather(*calls)
    connection._task.cancel()
    return waiting


def test_godot_reads_reach_the_server_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = connection_with(
        tmp_path, monkeypatch, "godot", "validate_script", "read_file", "write_file"
    )
    assert asyncio.run(in_flight(connection, "validate_script", "read_file")) == 2
    # A write still goes alone, so it never runs beside the reads.
    assert asyncio.run(in_flight(connection, "write_file", "validate_script")) == 1


def test_a_server_not_known_to_take_requests_at_once_gets_one_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = connection_with(
        tmp_path, monkeypatch, "joinery-blender-ops", "inspect_document"
    )
    assert asyncio.run(in_flight(connection, *["inspect_document"] * 3)) == 1


# --- The lock itself -------------------------------------------------------------------


def test_a_waiting_writer_goes_first_and_a_cancelled_one_lets_readers_on() -> None:
    async def run() -> None:
        lock = ReadWriteLock()
        await lock.acquire_read()
        writer = asyncio.create_task(lock.acquire_write())
        await asyncio.sleep(0)
        reader = asyncio.create_task(lock.acquire_read())
        await asyncio.sleep(0.01)
        assert not writer.done() and not reader.done()
        writer.cancel()
        await asyncio.wait_for(reader, 1)
        assert lock.readers == 2
        lock.release_read()
        lock.release_read()
        async with lock.write:
            assert lock.write.locked()
            late = asyncio.create_task(lock.acquire_read())
            await asyncio.sleep(0.01)
            assert not late.done()
        await asyncio.wait_for(late, 1)
        lock.release_read()
        assert not lock.write.locked() and not lock.read.locked()

    asyncio.run(run())


class SlowPeer:
    """A protocol peer whose calls take a while; it counts how many overlap."""

    protocol_version = "fixture-protocol"

    def __init__(self, *tools: str) -> None:
        self.tools = [
            Tool(name=name, input_schema={"type": "object"}) for name in tools
        ]
        self.running = 0
        self.most = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def list_tools(self, cursor: str | None = None) -> Any:
        return type("Page", (), {"tools": self.tools, "next_cursor": None})()

    async def call_tool(self, name: str, arguments: Any, **_: Any) -> CallToolResult:
        self.running += 1
        self.most = max(self.most, self.running)
        try:
            if name == "crash":
                await asyncio.sleep(0.05)
                raise OSError("the server went away")
            await asyncio.sleep(0.2)
            return text(name)
        finally:
            self.running -= 1


def served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, peer: SlowPeer
) -> MCPConnection:
    monkeypatch.setattr("joinery.mcp_runtime.Client", lambda *_a, **_k: peer)
    monkeypatch.setattr("joinery.mcp_runtime.mcp_violation", lambda *a, **k: None)
    return MCPConnection(ServerSpec(name="godot", command="fixture"), tmp_path)


def test_one_godot_connection_answers_reads_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    peer = SlowPeer("validate_script", "write_file")
    connection = served(tmp_path, monkeypatch, peer)

    async def run() -> None:
        try:
            loop = asyncio.get_running_loop()
            started = loop.time()
            await asyncio.gather(
                *(connection.call("validate_script", {}) for _ in range(4))
            )
            assert peer.most == 4
            assert loop.time() - started < 0.6
            peer.most = 0
            await asyncio.gather(*(connection.call("write_file", {}) for _ in range(2)))
            assert peer.most == 1
        finally:
            await connection.close()

    asyncio.run(run())


def test_a_failed_request_ends_the_ones_beside_it_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    peer = SlowPeer("validate_script", "crash")
    connection = served(tmp_path, monkeypatch, peer)
    monkeypatch.setattr("joinery.mcp_runtime.read_only", lambda *_: True)

    async def run() -> None:
        try:
            await connection.connect()
            results = await asyncio.wait_for(
                asyncio.gather(
                    connection.call("crash", {}, timeout=30),
                    connection.call("validate_script", {}, timeout=30),
                    return_exceptions=True,
                ),
                # Answered when the crash ends the connection, not at 30 s.
                1,
            )
            assert all(isinstance(result, Exception) for result in results), results
        finally:
            await connection.close()

    asyncio.run(run())
