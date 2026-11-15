"""Docker calls that never hang: a stuck Docker says so instead of waiting an hour.

Docker Desktop can stop answering while its process stays up; every ``docker``
command then blocks with no output. Quick calls (``image inspect``, ``ps``,
``info``, ``rm``) get a short limit here, and when a long one (``run``,
``exec``) runs past its own limit, :func:`responding` asks Docker for its
version so a hung daemon is told apart from a slow tool. Either way the error is
:data:`NOT_RESPONDING`, which says what to do.
"""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any

NOT_RESPONDING = "Docker isn't responding — restart Docker Desktop, then try again."
#: Seconds a quick Docker call (inspect, ps, info, rm) may take.
QUICK = 30
#: Seconds the "is Docker there" probe may take.
PROBE = 15
_PROBE = ["docker", "version", "--format", "{{.Server.Version}}"]


class DockerNotResponding(RuntimeError):
    """Docker didn't answer in time; the message says to restart Docker Desktop."""

    def __init__(self, what: str = "") -> None:
        detail = f" (`{what}` got no answer)" if what else ""
        super().__init__(NOT_RESPONDING.removesuffix(".") + detail + ".")


def _what(argv: list[str]) -> str:
    return " ".join(argv[:3])


def run(
    argv: list[str], timeout: float | None = None, **kwargs: Any
) -> subprocess.CompletedProcess[Any]:
    """``subprocess.run`` for a quick Docker call, bounded by ``timeout``.

    Raises :class:`DockerNotResponding` when Docker doesn't answer in time."""
    try:
        return subprocess.run(argv, timeout=timeout or QUICK, check=False, **kwargs)
    except subprocess.TimeoutExpired:
        raise DockerNotResponding(_what(argv)) from None


def responding(timeout: float | None = None) -> bool:
    """Whether Docker answers at all (a missing or stopped Docker answers too)."""
    try:
        subprocess.run(
            _PROBE, capture_output=True, timeout=timeout or PROBE, check=False
        )
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        return True
    return True


async def responding_async(timeout: float | None = None) -> bool:
    """:func:`responding` for an event loop."""
    try:
        process = await asyncio.create_subprocess_exec(
            *_PROBE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return True
    try:
        await asyncio.wait_for(process.wait(), timeout or PROBE)
    except TimeoutError:
        process.kill()
        await process.wait()
        return False
    return True
