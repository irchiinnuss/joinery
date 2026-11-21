"""The real chat host driven through its Remote channel, for every agent (ADR 0093).

``tests/godot/scripted_host.py`` runs ``bridge.stdio.serve`` with a scripted provider. The
test is the editor on the host's pipes and, through ``joinery.remote.hosts``, the Remote
server: a device's action reaches the same input loop as the editor's lines. Headless,
the host carries it out; with an editor attached, the sidebar is handed what it owns.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from joinery.remote import hosts
from joinery.remote.store import Store

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ["claude", "codex", "gemini", "opencode"]
DEVICE = {"id": "d_1", "name": "Ana's iPhone", "scopes": ["watch", "chat", "decide"]}
PREVIEW = {
    "kind": "file_preview",
    "transaction_id": "tx1",
    "summary": "Taller hedge",
    "diff": "",
    "changes": [],
    "media": [],
}
SCRIPT = {
    "turns": {
        "Make the hedge taller": [{"message": "Making it taller."}, {"emit": PREVIEW}],
        "Use a generator": [{"policy_stop": "a generative service"}],
        "Keep going": [{"hang": True}],
    },
    "decision_ms": 10,
}


class Host:
    """The scripted host: its pipes as the editor sees them."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.events: list[dict[str, Any]] = []
        self.arrived = asyncio.Event()
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self.process.stdout is not None
        while line := await self.process.stdout.readline():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                self.events.append(event)
                self.arrived.set()

    async def wait_for(self, test: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        while True:
            for event in self.events:
                if test(event):
                    return event
            self.arrived.clear()
            await asyncio.wait_for(self.arrived.wait(), 15)

    async def send(self, line: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps(line) + "\n").encode())
        await self.process.stdin.drain()

    async def close(self) -> None:
        if self.process.stdin is not None and not self.process.stdin.is_closing():
            self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 10)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()
        self.reader.cancel()


async def start_host(tmp_path: Path, agent: str, editor: bool) -> tuple[Host, Path]:
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    script = tmp_path / "turns.json"
    script.write_text(json.dumps(SCRIPT))
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    Store(home).set_config(enabled=True)
    args = [
        sys.executable,
        str(ROOT / "tests/godot/scripted_host.py"),
        "--project",
        str(project),
        "--script",
        str(script),
        "--agent",
        agent,
    ]
    if editor:
        args += ["--editor-pid", str(os.getpid())]
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, "JOINERY_REMOTE_HOME": str(home)},
        cwd=ROOT,
    )
    host = Host(process)
    await host.wait_for(lambda e: e.get("kind") == "connection")
    return host, home


async def channel_of(home: Path) -> hosts.Host:
    for _ in range(100):
        found = hosts.discover(home)
        if found:
            return found[0]
        await asyncio.sleep(0.1)
    raise AssertionError("The host never opened its Remote channel.")


class Feed:
    """What a device sees: the host's events through its channel."""

    def __init__(self, host: hosts.Host) -> None:
        self.items: list[dict[str, Any]] = []
        self.arrived = asyncio.Event()
        self.ended = asyncio.Event()
        self.task = asyncio.create_task(self._follow(host))

    async def _follow(self, host: hosts.Host) -> None:
        try:
            async for item in hosts.subscribe(host, 0):
                self.items.append(item)
                self.arrived.set()
        except hosts.HostGone:
            pass
        self.ended.set()

    async def wait_for(self, test: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        while True:
            for item in self.items:
                event = item.get("event")
                if isinstance(event, dict) and test(event):
                    return event
            self.arrived.clear()
            await asyncio.wait_for(self.arrived.wait(), 15)


def act(action: dict[str, Any]) -> dict[str, Any]:
    return {"op": "action", "action": action, "device": DEVICE}


def run(test: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(asyncio.wait_for(test(), 90))


@pytest.mark.parametrize("agent", AGENTS)
def test_headless_host_takes_device_actions(tmp_path: Path, agent: str) -> None:
    async def body() -> None:
        host, home = await start_host(tmp_path, agent, editor=False)
        try:
            channel = await channel_of(home)
            assert channel.agent == agent and not channel.editor
            feed = Feed(channel)
            # The same loop as the editor's prompt: the turn plays, every view sees it.
            sent = await hosts.request(
                channel, act({"kind": "prompt", "text": "Make the hedge taller"})
            )
            assert sent == {"ok": True, "text": ""}
            await feed.wait_for(lambda e: e.get("text") == "Making it taller.")
            preview = await feed.wait_for(lambda e: e.get("kind") == "file_preview")
            assert preview["transaction_id"] == "tx1"
            await host.wait_for(lambda e: e.get("kind") == "file_preview")
            # The host's own answer comes back to the device, not to the sidebar.
            decided = await hosts.request(
                channel,
                act({"kind": "decision", "transaction_id": "tx1", "accept": True}),
            )
            assert decided == {
                "ok": False,
                "text": "No change is waiting for your decision.",
            }
            assert not any(e.get("text") == decided["text"] for e in host.events), (
                "a device's refusal is not the sidebar's warning"
            )
            # Never reaches the loop at all.
            refused = await hosts.request(
                channel, act({"kind": "art_approval", "sha256": "x"})
            )
            assert refused["refused"]
            # A Rule Zero stop is mirrored to the device as it is.
            await hosts.request(
                channel, act({"kind": "prompt", "text": "Use a generator"})
            )
            stop = await feed.wait_for(
                lambda e: str(e.get("text", "")).startswith("Stopped by project policy")
            )
            assert "a generative service" in stop["text"]
            # Stop on a turn that hangs.
            await hosts.request(channel, act({"kind": "prompt", "text": "Keep going"}))
            await asyncio.sleep(0.3)
            stopped = await hosts.request(channel, act({"kind": "cancel"}))
            assert stopped["ok"]
            await feed.wait_for(lambda e: e.get("kind") == "done" and not e.get("ok"))
        finally:
            await host.close()
        # The editor's input ended: the host and its channel are gone.
        assert hosts.discover(home) == []

    run(body)


@pytest.mark.parametrize("agent", AGENTS)
def test_with_an_editor_the_sidebar_does_it(tmp_path: Path, agent: str) -> None:
    async def body() -> None:
        host, home = await start_host(tmp_path, agent, editor=True)
        try:
            channel = await channel_of(home)
            assert channel.editor
            feed = Feed(channel)

            async def sidebar() -> None:
                """The editor: does what it is handed as if clicked, then answers."""
                done: set[str] = set()
                while True:
                    relayed = await host.wait_for(
                        lambda e: (
                            e.get("kind") == "remote_action"
                            and e.get("ref") not in done
                        )
                    )
                    done.add(str(relayed["ref"]))
                    action = relayed["action"]
                    assert relayed["device"]["name"] == "Ana's iPhone"
                    if action["kind"] == "prompt":
                        await host.send({"kind": "prompt", "text": action["text"]})
                    await host.send(
                        {
                            "kind": "remote_ack",
                            "ref": relayed["ref"],
                            "ok": True,
                            "text": "Done.",
                        }
                    )

            editor = asyncio.create_task(sidebar())
            sent = await hosts.request(
                channel, act({"kind": "prompt", "text": "Make the hedge taller"})
            )
            assert sent == {"ok": True, "text": "Done."}
            await feed.wait_for(lambda e: e.get("kind") == "file_preview")
            # A change the sidebar isn't reviewing is the host's to answer, not relayed.
            decided = await hosts.request(
                channel,
                act({"kind": "decision", "transaction_id": "tx1", "accept": True}),
            )
            assert decided == {
                "ok": False,
                "text": "No change is waiting for your decision.",
            }
            # The relay itself is the editor's alone: no device sees it.
            assert not any(
                (i.get("event") or {}).get("kind") == "remote_action"
                for i in feed.items
            )
            # A direct action (another task's Stop) still goes straight to the host.
            direct = await hosts.request(channel, act({"kind": "cancel_tasks"}))
            assert direct["ok"]
            editor.cancel()
        finally:
            await host.close()

    run(body)


def test_turning_remote_off_closes_the_channel(tmp_path: Path) -> None:
    async def body() -> None:
        host, home = await start_host(tmp_path, "codex", editor=False)
        try:
            channel = await channel_of(home)
            feed = Feed(channel)
            await asyncio.sleep(0.2)
            Store(home).set_config(enabled=False)
            await asyncio.wait_for(feed.ended.wait(), 10)
            assert hosts.discover(home) == []
            # The editor's own input still works.
            await host.send({"kind": "prompt", "text": "Make the hedge taller"})
            await host.wait_for(lambda e: e.get("kind") == "file_preview")
        finally:
            await host.close()

    run(body)
