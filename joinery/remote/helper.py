"""The always-on helper: Joinery Remote with no editor open (ADR 0093).

``joinery remote serve --helper`` (the login item, ``joinery remote install-helper``) is
the same server, given a :class:`HeadlessLauncher`. When a phone opens a project no editor
has open, it starts the headless chat host ``joinery run`` uses (``joinery chat
--keep-reviews``) with the agent of the project's last chat. That host takes the project's
lease like any other, answers whatever only an editor can serve with "No editor is open",
and keeps every change waiting for review. It stops after ``idle_minutes`` with nothing
running, nothing waiting and no phone watching; an editor opening the project takes it over
(``joinery.remote.handoff``). The helper holds no lease of its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bridge.remote_channel import HELPER_ENV
from joinery import headless
from joinery.platform_support import private_file
from joinery.remote.hosts import Host, discover
from joinery.remote.paths import HOME_ENV, known, project_id, remote_home

IDLE_MINUTES = 15.0
#: How long a new host may take to connect its agent and open its channel.
START_SECONDS = 90.0
LOG = "helper.log"


@dataclass
class Child:
    project: Path
    process: asyncio.subprocess.Process
    started: float = field(default_factory=time.time)
    reader: asyncio.Task[None] | None = None
    error: str = ""


Command = Callable[[Path, str], list[str]]


def chat_command(project: Path, agent: str) -> list[str]:
    return headless.chat_command(project, headless.Pin(agent), keep_reviews=True)


class HeadlessLauncher:
    """Starts, follows and stops the headless hosts the helper owns."""

    def __init__(
        self,
        home: Path | None = None,
        *,
        idle_minutes: float = IDLE_MINUTES,
        command: Command = chat_command,
        start_seconds: float = START_SECONDS,
    ) -> None:
        self.home = home
        self.idle_minutes = idle_minutes
        self.command = command
        self.start_seconds = start_seconds
        self.children: dict[str, Child] = {}
        self._starting: dict[str, asyncio.Task[Host | None]] = {}

    # --- HostLauncher -----------------------------------------------------------------

    async def ensure_host(self, project: Path) -> Host | None:
        identifier = project_id(project)
        running = self._running(identifier)
        if running is not None:
            return running
        if identifier not in self._starting or self._starting[identifier].done():
            self._starting[identifier] = asyncio.create_task(self._start(project))
        return await asyncio.shield(self._starting[identifier])

    def _running(self, identifier: str) -> Host | None:
        return next((h for h in discover(self.home) if h.id == identifier), None)

    async def _start(self, project: Path) -> Host | None:
        if not (project / "project.godot").is_file():
            raise ValueError("That project isn't on this Mac any more.")
        identifier = project_id(project)
        agent = headless.default_agent(project) or str(
            known(self.home).get(identifier, {}).get("agent", "")
        )
        if not agent:
            raise ValueError("Open this project in Joinery once, then try again.")
        env = {**os.environ, HELPER_ENV: "1"}
        if self.home is not None:
            env[HOME_ENV] = str(self.home)
        log = (self.home or remote_home()) / LOG
        with log.open("ab") as stream:
            private_file(log)
            process = await asyncio.create_subprocess_exec(
                *self.command(project, agent),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=stream,
                env=env,
                cwd=project,
                limit=4_000_000,
            )
        child = Child(project, process)
        child.reader = asyncio.create_task(self._answer(child))
        self.children[identifier] = child
        deadline = time.monotonic() + self.start_seconds
        while time.monotonic() < deadline:
            host = self._running(identifier)
            if host is not None:
                return host
            if process.returncode is not None:
                break
            await asyncio.sleep(0.25)
        await self._stop(identifier)
        raise ValueError(child.error or "Joinery couldn't start this project's chat.")

    # --- The host's pipes -------------------------------------------------------------

    async def _answer(self, child: Child) -> None:
        """Read the host's events: what only an editor serves is answered "No editor is
        open" (as ``joinery run`` does); a connection error is kept to say why."""
        assert child.process.stdout is not None
        while line := await child.process.stdout.readline():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("kind") == "connection" and event.get("state") == "error":
                child.error = str(event.get("message", "")) or child.error
            answer = headless._no_editor(event)
            if answer is not None:
                await self._send(child, answer)

    async def _send(self, child: Child, line: dict[str, Any]) -> None:
        stdin = child.process.stdin
        if stdin is None or stdin.is_closing():
            return
        with contextlib.suppress(OSError, RuntimeError):
            stdin.write((json.dumps(line) + "\n").encode())
            await stdin.drain()

    # --- Idle and closing -------------------------------------------------------------

    async def tick(self, idle: Callable[[str], float]) -> None:
        """Stop the hosts idle for ``idle_minutes`` (``idle`` says for how many seconds
        a host has had nothing running, nothing waiting and no phone watching)."""
        for identifier, child in list(self.children.items()):
            if child.process.returncode is not None:
                self.children.pop(identifier, None)
            elif idle(identifier) >= self.idle_minutes * 60:
                await self._stop(identifier)

    async def _stop(self, identifier: str) -> None:
        child = self.children.pop(identifier, None)
        if child is None:
            return
        # As the editor does when it closes: changes waiting for review stay.
        await self._send(child, {"kind": "shutdown", "keep_reviews": True})
        if child.process.stdin is not None:
            with contextlib.suppress(OSError, RuntimeError):
                child.process.stdin.close()
        try:
            await asyncio.wait_for(child.process.wait(), 30)
        except TimeoutError:
            child.process.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(child.process.wait(), 10)
        if child.reader is not None:
            child.reader.cancel()

    async def close(self) -> None:
        for identifier in list(self.children):
            await self._stop(identifier)
