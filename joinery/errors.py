"""Errors as words an agent can act on: never blank.

``str(TimeoutError())`` and ``str(asyncio.CancelledError())`` are empty, and a
TimeoutError is an OSError, so a handler that says ``str(exc)`` for an OSError
hands the agent an empty message. Everything that turns an error into a tool's
error text goes through :func:`error_text` or :func:`said`.
"""

from __future__ import annotations

import asyncio

#: What an agent is told after a check it can't speed up by asking again.
DONT_REPEAT = (
    "Don't call it again unchanged: the same checks on the same files take as long "
    "again."
)


def error_text(error: BaseException) -> str:
    """An error as words that are never blank: its type, then its message when it
    has one."""
    message = str(error).strip()
    name = type(error).__name__
    return f"{name}: {message}" if message else name


def said(error: BaseException, what: str = "the call") -> str:
    """An error's own message, or, when it has none, what happened in words:
    a time-out says it timed out, anything else names its type."""
    message = str(error).strip()
    if message:
        return message
    if isinstance(error, TimeoutError):
        return f"{what} timed out before it finished (no other detail was given)."
    if isinstance(error, asyncio.CancelledError):
        return f"{what} was cancelled before it finished."
    return f"{what} failed with {type(error).__name__} (no other detail was given)."
