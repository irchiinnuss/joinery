"""The editor takes a project back from a host the always-on helper started (ADR 0093).

With Remote kept available while Joinery is closed, a phone's request runs in a headless
host (``joinery.remote.helper``), which holds the project's lease. When the editor then
opens the project, its own host asks that one to let go: at once when nothing is at work,
else as soon as the running request finishes. Changes waiting for review are kept and
appear in the editor's Review tab.

The editor's host can't start its own loop before then (the lease is the project's), but
it never goes deaf: while it waits it keeps reading the editor's lines. The sidebar is
told with a ``handover`` event (``waiting``) and offers "Stop it now" or "Wait". Stop
sends ``handover_stop``, and the phone's request is stopped as the phone's Stop would.
Every other line is kept for the loop, in order; the editor closing ends the wait. Once
asked to let go, the helper's host takes no new request from a phone.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from joinery.remote.hosts import Host, HostGone, find, request
from joinery.remote.paths import project_id
from joinery.remote.store import enabled

#: How long the editor waits for a phone's request it asked to stop.
STOP_WAIT = 60.0
BUSY = (
    "A request from your phone is still running in this project. The editor takes "
    "over as soon as it finishes, or stop it now."
)
STOPPING = "Stopping the phone's request…"
DONE = "The editor has this project again."
#: The line the sidebar sends for "Stop it now".
STOP_KIND = "handover_stop"


class EditorLines(Protocol):
    """The editor's input as :class:`bridge.early_input.EarlyInput` keeps it."""

    reader: asyncio.StreamReader

    def keep(self, line: bytes) -> None: ...


async def take_over(
    project: Path,
    emit: Callable[[dict[str, Any]], None],
    home: Path | None = None,
    wait: float | None = None,
    editor: EditorLines | None = None,
) -> bool:
    """Ask a helper's host on ``project`` to let go; True when there was one.

    ``wait``: give up after so many seconds (the lease's own error then explains);
    None waits as long as the helper's host runs, the editor is open and the user
    hasn't said otherwise. ``editor``: the editor's input, read meanwhile."""
    if not await asyncio.to_thread(enabled, home):
        return False  # Remote is off: no helper host, and nothing is created.
    try:
        host = await asyncio.to_thread(find, project_id(project), home)
    except OSError:
        return False
    if host is None or host.editor or not host.helper:
        return False
    try:
        answer = await request(host, {"op": "release"}, timeout=10)
    except HostGone:
        return False
    if not answer.get("ok"):
        return False
    busy = bool(answer.get("busy"))
    if busy:
        emit({"kind": "handover", "state": "waiting", "text": BUSY})
    loop = asyncio.get_running_loop()
    deadline = None if wait is None else loop.time() + wait
    reading: asyncio.Future[bytes] | None = None
    try:
        while deadline is None or loop.time() < deadline:
            if await asyncio.to_thread(find, host.id, home) is None:
                if busy:
                    emit({"kind": "handover", "state": "done", "text": DONE})
                return True
            if editor is None:
                await asyncio.sleep(0.5)
                continue
            if reading is None:
                reading = asyncio.ensure_future(editor.reader.readline())
            done, _ = await asyncio.wait({reading}, timeout=0.5)
            if not done:
                continue
            line, reading = reading.result(), None
            if _kind(line) == STOP_KIND:
                emit({"kind": "handover", "state": "stopping", "text": STOPPING})
                await _stop(host)
                deadline = loop.time() + STOP_WAIT
                continue
            editor.keep(line)
            if not line or _kind(line) == "shutdown":
                return True  # The editor closed: nothing more to wait for.
    finally:
        if reading is not None:
            # A line half read stays in the reader for the loop.
            reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)
    return True


async def _stop(host: Host) -> None:
    with contextlib.suppress(HostGone):
        await request(host, {"op": "release", "stop": True}, timeout=10)


def _kind(line: bytes) -> str:
    try:
        data = json.loads(line)
    except ValueError:
        return ""
    return str(data.get("kind", "")) if isinstance(data, dict) else ""
