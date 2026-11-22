"""The editor's input while the chat host still connects to its agent.

Connecting can take long or never finish (a provider login nobody answers). The host must still
end when the editor does: on EOF, on a ``shutdown`` line, or when the editor process is gone
(``end_with_editor`` feeds EOF). So while it connects the host reads its input, keeps every
line for the main loop, and gives up connecting once the input has ended.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from collections.abc import Awaitable


class EarlyInput:
    """The editor's input, with the lines read while connecting put back in front."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self.reader = reader
        #: The ``shutdown`` line that ended the input while connecting, if one did.
        self.shutdown: dict[str, object] | None = None
        self._kept: deque[bytes] = deque()

    @property
    def ended(self) -> bool:
        """The input's EOF is among the kept lines."""
        return b"" in self._kept

    async def until_ended(self) -> None:
        """Read and keep lines until the input ends (EOF or a ``shutdown`` line)."""
        while line := await self.reader.readline():
            self._kept.append(line)
            with contextlib.suppress(ValueError):
                data = json.loads(line)
                if isinstance(data, dict) and data.get("kind") == "shutdown":
                    self.shutdown = data
                    return
        self._kept.append(b"")

    def keep(self, line: bytes) -> None:
        """Keep ``line``, read elsewhere (joinery.remote.handoff), for the main loop."""
        self._kept.append(line)
        with contextlib.suppress(ValueError):
            data = json.loads(line)
            if isinstance(data, dict) and data.get("kind") == "shutdown":
                self.shutdown = data

    async def readline(self) -> bytes:
        """The next line: one kept while connecting first; b"" once the input ended."""
        if self._kept:
            return self._kept.popleft()
        return await self.reader.readline()


async def connect_or_end(connect: Awaitable[None], early: EarlyInput) -> bool:
    """Run [connect]; False when the editor's input ended first (connecting is cancelled).

    A failure to connect is raised as it would be without the watch."""
    connecting = asyncio.ensure_future(connect)
    watching = asyncio.ensure_future(early.until_ended())
    try:
        await asyncio.wait({connecting, watching}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        connecting.cancel()
        watching.cancel()
        await asyncio.gather(connecting, watching, return_exceptions=True)
        raise
    if connecting.done():
        # Connected (or failed to): stop watching; a line it was reading stays in the reader.
        watching.cancel()
        await asyncio.gather(watching, return_exceptions=True)
        connecting.result()
        return True
    connecting.cancel()
    await asyncio.gather(connecting, return_exceptions=True)
    return False
