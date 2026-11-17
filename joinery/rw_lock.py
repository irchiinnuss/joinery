"""A read/write lock for asyncio: many readers at once, or one writer alone.

A writer waiting goes before readers that arrive after it, so a steady stream of
reads never starves a change. Releasing never awaits, so it is safe in a
``finally`` while the task is being cancelled.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import TracebackType


class ReadWriteLock:
    """``async with lock.read`` for a read, ``async with lock.write`` for a change."""

    def __init__(self) -> None:
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0
        self._waiters: list[asyncio.Future[None]] = []
        self.read = LockSide(self, exclusive=False)
        self.write = LockSide(self, exclusive=True)

    @property
    def readers(self) -> int:
        return self._readers

    def write_locked(self) -> bool:
        return self._writer

    def _wake(self) -> None:
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)

    async def _until(self, ready: Callable[[], bool]) -> None:
        while not ready():
            waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiters.append(waiter)
            try:
                await waiter
            finally:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)

    async def acquire_read(self) -> None:
        await self._until(lambda: not self._writer and not self._writers_waiting)
        self._readers += 1

    def release_read(self) -> None:
        if self._readers <= 0:
            raise RuntimeError("Read lock released more often than acquired.")
        self._readers -= 1
        if not self._readers:
            self._wake()

    async def acquire_write(self) -> None:
        self._writers_waiting += 1
        acquired = False
        try:
            await self._until(lambda: not self._writer and not self._readers)
            acquired = True
        finally:
            self._writers_waiting -= 1
            if acquired:
                self._writer = True
            else:
                # Cancelled while waiting: readers held back for it may go on.
                self._wake()

    def release_write(self) -> None:
        if not self._writer:
            raise RuntimeError("Write lock released without being held.")
        self._writer = False
        self._wake()


class LockSide:
    """One side of a :class:`ReadWriteLock`, used as an async context manager."""

    def __init__(self, lock: ReadWriteLock, *, exclusive: bool) -> None:
        self._lock = lock
        self._exclusive = exclusive

    def locked(self) -> bool:
        """For the write side: a writer holds it. For the read side: a reader does."""
        if self._exclusive:
            return self._lock.write_locked()
        return self._lock.readers > 0

    async def __aenter__(self) -> None:
        if self._exclusive:
            await self._lock.acquire_write()
        else:
            await self._lock.acquire_read()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._exclusive:
            self._lock.release_write()
        else:
            self._lock.release_read()
