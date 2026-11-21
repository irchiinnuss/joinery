"""A chat host's local-only channel for Joinery Remote (ADR 0093).

The host never listens on the network. Like the gateway's private endpoint
(``joinery.gateway_rpc``) it opens a unix socket in a fresh 0700 directory, or on
Windows a loopback port guarded by a 32-byte token, and advertises it to the per-user
Remote server in ``<remote home>/hosts/<project id>.json`` (0600). Only that server
connects; it authenticates devices and sends each action with the device it came from.

In: an action is checked against the closed allowlist (``joinery.remote.actions``)
again here, then joins the host's own input loop through :attr:`RemoteChannel.inbox`,
the same loop the editor's pipe feeds: a phone's Accept runs the code a click runs.
With an editor attached, actions the sidebar owns (send, Stop, Go, Accept, Discard) go
to the sidebar as ``remote_action`` instead, so it does them exactly as if clicked and
its cards, queue and live scene edits stay right; it answers ``remote_ack``.

Out: every event the host emits is published here too, numbered (``seq``) and kept in
a ring for reconnects, minus the editor's own bookkeeping, with file paths taken out:
media are named by change and index, never by path (:meth:`RemoteChannel.media_path`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from joinery.platform_support import private_directory
from joinery.remote.actions import Refused, admit, route
from joinery.remote.media import checked_media, scrub
from joinery.remote.paths import hosts_dir, project_id, write_private_json

#: Events kept for a device that reconnects; older ones come from the saved chats.
RING = 2000
#: Events a slow subscriber may fall behind before it is dropped (it resumes by seq).
BACKLOG = 1000
#: How long an action waits for the sidebar's answer, and for the host's.
RELAY_SECONDS = 20.0
DIRECT_SECONDS = 120.0
#: Never sent to a device: the editor's own bookkeeping.
HIDDEN = frozenset({"editor_action", "editor_view", "remote_action", "quiet"})
#: Media item keys that name a file on disk, by side.
SIDES = ("before", "after")
FILE_KEYS = ("before", "after", "before_model", "after_model")
NO_EDITOR_ANSWER = "The editor didn't answer. Look at the machine."


@dataclass
class Origin:
    """Where a remote action came from, and how it is answered."""

    device_id: str
    device_name: str
    scopes: tuple[str, ...]
    ref: str = ""
    answer: asyncio.Future[dict[str, Any]] | None = field(default=None, repr=False)

    def done(self, ok: bool, text: str = "", **extra: Any) -> None:
        if self.answer is not None and not self.answer.done():
            self.answer.set_result({"ok": ok, "text": text, **extra})


class RemoteChannel:
    def __init__(
        self,
        project: Path,
        agent_id: str,
        *,
        editor: bool,
        to_editor: Callable[[dict[str, Any]], None],
        replies: dict[str, Callable[[], dict[str, Any]]] | None = None,
        home: Path | None = None,
        transport: str | None = None,
    ) -> None:
        self.project = project
        self.agent_id = agent_id
        #: An editor is attached: the sidebar carries out what it owns.
        self.editor = editor
        self.to_editor = to_editor
        self.replies = replies or {}
        self.home = home
        self.transport = transport or ("tcp" if os.name == "nt" else "unix")
        self.inbox: asyncio.Queue[tuple[dict[str, Any], Origin]] = asyncio.Queue()
        self.seq = 0
        self.ring: deque[tuple[int, dict[str, Any]]] = deque(maxlen=RING)
        #: The newest media list of each change, as the host sent it (with paths).
        self.media: dict[str, list[dict[str, Any]]] = {}
        self._token = secrets.token_urlsafe(32)
        self._relays: dict[str, Origin] = {}
        self._subscribers: set[asyncio.Queue[tuple[int, dict[str, Any]] | None]] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._server: asyncio.Server | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = 0
        self.directory: Path | None = None
        self.advert: Path | None = None
        self.started = time.time()

    # --- Lifetime -----------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._thread = threading.get_ident()
        # Unix socket names have a short OS limit, so the directory sits in /tmp.
        self.directory = Path(
            tempfile.mkdtemp(
                prefix="joinery-remote-", dir=None if os.name == "nt" else "/tmp"
            )
        )
        try:
            private_directory(self.directory)
            endpoint: dict[str, Any]
            if self.transport == "tcp":
                self._server = await asyncio.start_server(
                    self._client, "127.0.0.1", 0, limit=1_000_000
                )
                port = self._server.sockets[0].getsockname()[1]
                endpoint = {"transport": "tcp", "host": "127.0.0.1", "port": port}
            else:
                if sys.platform == "win32":
                    raise ValueError("Native Windows hosts use a local TCP channel.")
                path = self.directory / "remote.sock"
                self._server = await asyncio.start_unix_server(
                    self._client, str(path), limit=1_000_000
                )
                os.chmod(path, 0o600)
                endpoint = {"transport": "unix", "path": str(path)}
            self.advert = hosts_dir(self.home) / (project_id(self.project) + ".json")
            write_private_json(
                self.advert,
                {
                    "version": 1,
                    "project": str(self.project),
                    "name": self.project.name,
                    "agent": self.agent_id,
                    "pid": os.getpid(),
                    "editor": self.editor,
                    "token": self._token,
                    "started": self.started,
                    **endpoint,
                },
            )
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        if self.advert is not None:
            with contextlib.suppress(OSError):
                # Only this host's own advertisement: another may have replaced it.
                current = json.loads(self.advert.read_text(encoding="utf-8"))
                if current.get("pid") == os.getpid():
                    self.advert.unlink()
        for origin in [*self._relays.values()]:
            origin.done(False, "The chat closed.")
        self._relays.clear()
        while not self.inbox.empty():
            _, origin = self.inbox.get_nowait()
            origin.done(False, "The chat closed.")
        for queue in [*self._subscribers]:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)
        if self._server is not None:
            self._server.close()
        for task in [*self._tasks]:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server is not None:
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        if self.directory is not None:
            shutil.rmtree(self.directory, ignore_errors=True)
            self.directory = None

    # --- Out: events --------------------------------------------------------------------

    def publish(self, event: dict[str, Any]) -> None:
        """One event the host emitted (``bridge.stdio.emit``), for every device."""
        if self._loop is not None and threading.get_ident() != self._thread:
            self._loop.call_soon_threadsafe(self.publish, event)
            return
        shown = self.projected(event)
        if shown is None:
            return
        self.seq += 1
        item = (self.seq, shown)
        self.ring.append(item)
        for queue in [*self._subscribers]:
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                # Too far behind: it reconnects and resumes from its last seq.
                self._subscribers.discard(queue)
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                queue.put_nowait(None)

    def projected(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """``event`` as a device sees it: None for the editor's bookkeeping; media named
        by change and index; no other absolute path."""
        kind = str(event.get("kind", ""))
        if kind in HIDDEN:
            return None
        shown = scrub(event, str(self.project))
        assert isinstance(shown, dict)
        media = event.get("media")
        identifier = str(event.get("transaction_id", ""))
        if isinstance(media, list) and identifier:
            self.media[identifier] = [m for m in media if isinstance(m, dict)]
            if len(self.media) > 50:
                self.media.pop(next(iter(self.media)))
            shown["media"] = [
                _media_item(item, identifier, index)
                for index, item in enumerate(self.media[identifier])
            ]
        return shown

    def media_path(self, transaction_id: str, index: int, side: str) -> Path:
        """The file a change's media item shows on ``side``: only one the host sent for
        that change, inside that change's own folder, not through a link, and a picture
        or a sound. Raises ValueError otherwise."""
        if side not in SIDES:
            raise ValueError("No such picture.")
        items = self.media.get(transaction_id)
        if items is None or not 0 <= index < len(items):
            raise ValueError("No such picture.")
        named = items[index].get(side)
        if not isinstance(named, str) or not named:
            raise ValueError("No such picture.")
        return checked_media(self.project, transaction_id, Path(named))

    # --- In: actions --------------------------------------------------------------------

    def relay(self, action: dict[str, Any], origin: Origin) -> None:
        """Hand ``action`` to the sidebar, which does it as if clicked."""
        origin.ref = origin.ref or secrets.token_hex(8)
        self._relays[origin.ref] = origin
        self.to_editor(
            {
                "kind": "remote_action",
                "ref": origin.ref,
                "action": action,
                "device": {"id": origin.device_id, "name": origin.device_name},
            }
        )

    def acked(self, ref: str, ok: bool, text: str = "") -> None:
        """The sidebar's answer to a relayed action."""
        origin = self._relays.pop(ref, None)
        if origin is not None:
            origin.done(ok, text)

    # --- Connections --------------------------------------------------------------------

    async def _client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            request = json.loads(line)
            if not isinstance(request, dict):
                raise TypeError("Invalid request.")
            token = request.get("token")
            if not isinstance(token, str) or not secrets.compare_digest(
                token, self._token
            ):
                raise ValueError("Remote channel authentication failed.")
            op = request.get("op")
            if op == "subscribe":
                await self._subscribe(request, writer)
                return
            answer = await self._answer(op, request)
            writer.write((json.dumps(answer) + "\n").encode())
            await writer.drain()
        except (OSError, ValueError, TypeError, TimeoutError) as exc:
            with contextlib.suppress(OSError):
                writer.write(
                    (json.dumps({"ok": False, "text": str(exc)}) + "\n").encode()
                )
                await writer.drain()
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
            if task is not None:
                self._tasks.discard(task)

    def hello(self) -> dict[str, Any]:
        return {
            "kind": "hello",
            "project": self.project.name,
            "agent": self.agent_id,
            "editor": self.editor,
            "seq": self.seq,
            "oldest": self.ring[0][0] if self.ring else self.seq + 1,
            "started": self.started,
        }

    async def _subscribe(
        self, request: dict[str, Any], writer: asyncio.StreamWriter
    ) -> None:
        since = request.get("since", 0)
        since = since if isinstance(since, int) and since >= 0 else 0
        queue: asyncio.Queue[tuple[int, dict[str, Any]] | None] = asyncio.Queue(
            maxsize=BACKLOG
        )
        hello = self.hello()
        # Asked from further back than the ring holds: the device reads the chat again.
        # From 0, the ring is the device's catch-up: what waits now is in it.
        hello["gap"] = since + 1 < int(hello["oldest"])
        backlog = [item for item in self.ring if item[0] > since]
        self._subscribers.add(queue)
        try:
            writer.write((json.dumps(hello) + "\n").encode())
            for seq, event in backlog:
                writer.write((json.dumps({"seq": seq, "event": event}) + "\n").encode())
            await writer.drain()
            while True:
                item = await queue.get()
                if item is None:
                    return
                seq, event = item
                writer.write((json.dumps({"seq": seq, "event": event}) + "\n").encode())
                await writer.drain()
        finally:
            self._subscribers.discard(queue)

    async def _answer(self, op: Any, request: dict[str, Any]) -> dict[str, Any]:
        if op == "media":
            index = request.get("index")
            path = self.media_path(
                str(request.get("transaction_id", "")),
                index if isinstance(index, int) else -1,
                str(request.get("side", "")),
            )
            return {"ok": True, "path": str(path)}
        if op == "state":
            return {"ok": True, **self.hello()}
        if op != "action":
            raise ValueError("Unknown request.")
        device = request.get("device")
        if not isinstance(device, dict):
            raise TypeError("Missing device.")
        scopes = device.get("scopes")
        origin = Origin(
            device_id=str(device.get("id", ""))[:40],
            device_name=" ".join(str(device.get("name", "")).split())[:60],
            scopes=tuple(str(s) for s in scopes) if isinstance(scopes, list) else (),
        )
        try:
            # Checked here as well: nothing off the list reaches the input loop.
            action = admit(request.get("action"), origin.scopes)
        except Refused as exc:
            return {"ok": False, "refused": True, "text": str(exc)}
        reply = self.replies.get(action["kind"])
        if reply is not None:
            event = await asyncio.to_thread(reply)
            return {"ok": True, "event": self.projected(event) or {}}
        assert self._loop is not None
        origin.answer = self._loop.create_future()
        await self.inbox.put((action, origin))
        relayed = self.editor and route(action) == "relay"
        try:
            return await asyncio.wait_for(
                asyncio.shield(origin.answer),
                RELAY_SECONDS if relayed else DIRECT_SECONDS,
            )
        except TimeoutError:
            if origin.ref:
                self._relays.pop(origin.ref, None)
            return {
                "ok": False,
                "text": NO_EDITOR_ANSWER if relayed else "Still working on it.",
            }


class Inputs:
    """The host loop's input: the editor's lines first, then the remote channel's."""

    def __init__(self, early: Any, channel: RemoteChannel | None) -> None:
        self.early = early
        self._channel = channel
        self._editor: asyncio.Future[bytes] | None = None
        self._remote: asyncio.Future[tuple[dict[str, Any], Origin]] | None = None
        self._changed = asyncio.Event()
        self._wake: asyncio.Future[Any] | None = None

    @property
    def channel(self) -> RemoteChannel | None:
        return self._channel

    @channel.setter
    def channel(self, channel: RemoteChannel | None) -> None:
        """Remote was turned on: a read already waiting takes the channel's lines too."""
        self._channel = channel
        self._changed.set()

    async def readline(self) -> tuple[bytes, Origin | None]:
        """The next line and, for a remote one, where it came from. b"" once the editor's
        input ended (the host stops then, whatever a device sends)."""
        while True:
            if self._editor is None:
                self._editor = asyncio.ensure_future(self.early.readline())
            if self._wake is None or self._wake.done():
                self._changed.clear()
                self._wake = asyncio.ensure_future(self._changed.wait())
            waiting: set[asyncio.Future[Any]] = {self._editor, self._wake}
            if self._channel is not None:
                if self._remote is None:
                    self._remote = asyncio.ensure_future(self._channel.inbox.get())
                waiting.add(self._remote)
            await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            if self._editor.done():
                line = self._editor.result()
                self._editor = None
                return line, None
            remote = self._remote
            if remote is None or not remote.done():
                continue  # Remote was turned on or off meanwhile.
            self._remote = None
            if remote.cancelled():
                continue
            action, origin = remote.result()
            return json.dumps(action).encode(), origin

    async def drop_channel(self) -> None:
        """Remote was turned off: stop reading its actions (the channel answers them)."""
        self._channel = None
        pending, self._remote = self._remote, None
        self._changed.set()
        if pending is not None and not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending
        elif pending is not None and not pending.cancelled():
            with contextlib.suppress(Exception):
                pending.result()[1].done(False, "Remote was turned off.")

    async def close(self) -> None:
        for pending in (self._editor, self._remote, self._wake):
            if pending is not None and not pending.done():
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pending
        remote = self._remote
        if remote is not None and remote.done() and not remote.cancelled():
            with contextlib.suppress(Exception):
                remote.result()[1].done(False, "The chat closed.")


def _media_item(
    item: dict[str, Any], transaction_id: str, index: int
) -> dict[str, Any]:
    shown = {k: v for k, v in scrub(item, "").items() if k not in FILE_KEYS}
    for side in SIDES:
        if isinstance(item.get(side), str) and item[side]:
            shown[side] = f"media/{transaction_id}/{index}/{side}"
    return shown
