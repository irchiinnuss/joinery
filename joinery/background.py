"""Fire-and-forget asyncio tasks that are neither lost nor silent.

The event loop keeps only a weak reference to a task, so a task nobody holds can be
garbage-collected while it awaits, and dies with ``GeneratorExit`` halfway through.
:func:`spawn` keeps every task it starts until it ends, and logs a task that ends
with an exception nobody will ever retrieve.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

log = logging.getLogger(__name__)

#: Every task started by :func:`spawn` that has not ended yet.
RUNNING: set[asyncio.Task[Any]] = set()


def spawn[T](
    work: Awaitable[T],
    *,
    name: str | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> asyncio.Task[T]:
    """Start ``work`` as a task that stays referenced until it ends.

    Without ``loop`` it runs on the running loop (and raises ``RuntimeError``, like
    :func:`asyncio.create_task`, when there is none). A cancelled task ends quietly; one
    that raises is logged with its traceback.
    """
    task = asyncio.ensure_future(work, loop=loop or asyncio.get_running_loop())
    if name is not None:
        task.set_name(name)
    RUNNING.add(task)
    task.add_done_callback(_ended)
    return task


def _ended(task: asyncio.Task[Any]) -> None:
    RUNNING.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.error("Background task %s failed", task.get_name(), exc_info=error)
