"""The running chat hosts the Remote server can reach, and how it talks to them.

Each host advertises its local channel (``bridge.remote_channel``) in the per-user
``hosts`` folder while Remote is on. The server only attaches to a host that runs: it
never starts a second host beside the editor's (``ProjectLease``); for a project with
no host, a :class:`joinery.remote.seams.HostLauncher` may start a headless one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from joinery.platform_support import ProcessWatch
from joinery.remote.paths import hosts_dir, read_json

#: Longest line a host sends (one event).
LINE_LIMIT = 4_000_000


class HostGone(ConnectionError):
    """The host closed or can't be reached."""


@dataclass(frozen=True)
class Host:
    id: str
    project: Path
    name: str
    agent: str
    pid: int
    editor: bool
    endpoint: dict[str, Any]
    token: str
    started: float
    #: Started by the always-on helper, with no editor (joinery.remote.helper).
    helper: bool = False

    def public(self) -> dict[str, Any]:
        """The host as a device sees it: never its path or token."""
        return {
            "id": self.id,
            "name": self.name,
            "agent": self.agent,
            "editor": self.editor,
            "started": self.started,
        }


def _alive(pid: int) -> bool:
    watch = ProcessWatch(pid)
    try:
        return watch.alive()
    finally:
        watch.close()


def discover(home: Path | None = None) -> list[Host]:
    """The hosts that advertise a channel and still run; a stale advertisement goes."""
    found: list[Host] = []
    for path in sorted(hosts_dir(home).glob("*.json")):
        data = read_json(path)
        if not isinstance(data, dict) or data.get("version") != 1:
            continue
        pid = data.get("pid")
        if not isinstance(pid, int) or not _alive(pid):
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        endpoint = {
            k: data[k] for k in ("transport", "path", "host", "port") if k in data
        }
        found.append(
            Host(
                id=path.stem,
                project=Path(str(data.get("project", ""))),
                name=str(data.get("name", "")),
                agent=str(data.get("agent", "")),
                pid=pid,
                editor=bool(data.get("editor", False)),
                endpoint=endpoint,
                token=str(data.get("token", "")),
                started=float(data.get("started", 0) or 0),
                helper=bool(data.get("helper", False)),
            )
        )
    return found


def find(project_id: str, home: Path | None = None) -> Host | None:
    return next((h for h in discover(home) if h.id == project_id), None)


async def _open(host: Host) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        if host.endpoint.get("transport") == "tcp":
            # Loopback only: a host never advertises another address.
            if host.endpoint.get("host") != "127.0.0.1":
                raise HostGone("The chat host's channel isn't local.")
            return await asyncio.open_connection(
                "127.0.0.1", int(host.endpoint.get("port", 0)), limit=LINE_LIMIT
            )
        return await asyncio.open_unix_connection(
            str(host.endpoint.get("path", "")), limit=LINE_LIMIT
        )
    except (OSError, ValueError) as exc:
        raise HostGone("The chat host can't be reached.") from exc


async def request(
    host: Host, payload: dict[str, Any], timeout: float = 150.0
) -> dict[str, Any]:
    """One request to ``host`` (an action, a media lookup, its state) and its answer."""
    reader, writer = await _open(host)
    try:
        writer.write((json.dumps({"token": host.token, **payload}) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
    except (OSError, TimeoutError) as exc:
        raise HostGone("The chat host didn't answer.") from exc
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()
    if not line:
        raise HostGone("The chat host closed.")
    answer = json.loads(line)
    if not isinstance(answer, dict):
        raise HostGone("The chat host answered wrongly.")
    return answer


async def subscribe(host: Host, since: int = 0) -> AsyncGenerator[dict[str, Any], None]:
    """The host's ``hello``, then its events as ``{seq, event}``, until it closes."""
    reader, writer = await _open(host)
    try:
        payload = {"token": host.token, "op": "subscribe", "since": since}
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
        while line := await reader.readline():
            item = json.loads(line)
            if isinstance(item, dict):
                yield item
    except (OSError, ValueError) as exc:
        raise HostGone("The chat host closed.") from exc
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()
