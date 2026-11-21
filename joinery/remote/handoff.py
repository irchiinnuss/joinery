"""The editor takes a project back from a host the always-on helper started (ADR 0093).

With Remote kept available while Joinery is closed, a phone's request runs in a headless
host (``joinery.remote.helper``), which holds the project's lease. When the editor then
opens the project, its own host asks that one to let go: at once when nothing is at work,
else as soon as the running request finishes, never by stopping it. Changes waiting for
review are kept and appear in the editor's Review tab. The editor's lease wait
(``ProjectLease``) covers the moment it takes to close.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from joinery.remote.hosts import HostGone, find, request
from joinery.remote.paths import project_id
from joinery.remote.store import enabled

#: How long the editor waits for a phone's request to finish before it says so and stops
#: waiting (the lease's own error then explains).
BUSY_WAIT = 300.0
BUSY = (
    "A request from your phone is still running in this project. The editor takes "
    "over as soon as it finishes; stop it from the phone to take over now."
)


async def take_over(
    project: Path,
    emit: Callable[[dict[str, Any]], None],
    home: Path | None = None,
    wait: float = BUSY_WAIT,
) -> bool:
    """Ask a helper's host on ``project`` to let go; True when there was one."""
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
    if answer.get("busy"):
        emit({"kind": "notice", "level": "info", "text": BUSY})
    deadline = asyncio.get_running_loop().time() + wait
    while asyncio.get_running_loop().time() < deadline:
        if await asyncio.to_thread(find, host.id, home) is None:
            return True
        await asyncio.sleep(0.5)
    return True
