"""What the machine itself needs for a turn: free disk space and a Docker that answers.

A change copies the project into a stage, imports it in Godot and runs its tests in
Docker workers. With the disk full or Docker stopped none of that can work, and
what the user saw was a step that kept counting ("Importing into Godot, 8 min…")
and then "Claude Code stopped with an error." with no reason. Here those two are
found before a turn starts, while a long step runs, and in the words of a failed
tool call (``ENOSPC``, Docker's "Cannot connect to the Docker daemon"), and said
the same plain way everywhere:

    The disk is full (2 GB free). Free space, then Retry.
    Docker isn't running. Start Docker Desktop, then Retry.

Nothing here starts, stops or changes Docker: it is only asked for its version.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path

#: Below this much free space a change can't be staged, imported and tested.
MIN_FREE = 3_000_000_000
DOCKER_DOWN = "Docker isn't running. Start Docker Desktop, then Retry."
#: Said to the agent after either: nothing in the project fixes it.
FOR_AGENT = (
    "This is the machine's, not the change's: stop and tell the user in these words; "
    "no tool call works until they fix it."
)
#: Seconds the Docker probe may take before Docker counts as not answering.
PROBE_SECONDS = 15.0
_PROBE = ("docker", "version", "--format", "{{.Server.Version}}")

#: A write that failed for want of space, in any tool's words.
_NO_SPACE = re.compile(
    r"No space left on device|ENOSPC|\[Errno 28\]|errno 28\b"
    r"|not enough (?:free )?disk space|There is not enough space on the disk",
    re.IGNORECASE,
)
#: Docker's daemon can't be reached (stopped, starting, or hung).
_DOCKER_DOWN = re.compile(
    r"Cannot connect to the Docker daemon|Is the docker daemon running"
    r"|error during connect|docker\.sock.*(?:connection refused|no such file)"
    r"|dockerDesktopLinuxEngine.*(?:cannot find|not found)"
    r"|Docker isn't responding|Docker Desktop is (?:stopped|not running|starting)",
    re.IGNORECASE,
)

#: How an agent's app says it stopped, with no reason of its own.
_PROVIDER_FAILED = re.compile(
    r"stopped with an error|could not (?:finish|complete) this (?:request|message)"
    r"|exited unexpectedly|crashed",
    re.IGNORECASE,
)

#: Says what is wrong with the machine now, or "" (the check the host wires in).
Check = Callable[[], Awaitable[str]]


class MachineProblem(RuntimeError):
    """The machine can't do the work (a full disk, Docker down); the message says
    what to do."""


def gigabytes(free: int) -> str:
    """ "2 GB", "0.4 GB": as a person reads free space."""
    value = free / 1_000_000_000
    return f"{value:.1f} GB" if value < 1 else f"{value:.0f} GB"


def disk_full(free: int | None) -> str:
    shown = f" ({gigabytes(free)} free)" if free is not None else ""
    return f"The disk is full{shown}. Free space, then Retry."


def free_bytes(path: Path) -> int | None:
    """Free space where ``path`` (or its nearest existing parent) lives."""
    for place in (path, *path.parents):
        try:
            return shutil.disk_usage(place).free
        except OSError:
            continue
    return None


def docker_data() -> list[Path]:
    """Where Docker keeps its images and containers on this machine, if there."""
    home = Path.home()
    if sys.platform == "darwin":
        found = [home / "Library/Containers/com.docker.docker"]
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        found = [Path(local) / "Docker"] if local else []
    else:
        found = [Path("/var/lib/docker"), home / ".docker/desktop"]
    return [path for path in found if path.exists()]


def disk_problem(
    places: Iterable[Path], free: Callable[[Path], int | None] | None = None
) -> str:
    """The plain words for the fullest of ``places`` when it is below
    :data:`MIN_FREE`, else ""."""
    lowest: int | None = None
    for place in places:
        left = (free or free_bytes)(place)
        if left is not None and (lowest is None or left < lowest):
            lowest = left
    return disk_full(lowest) if lowest is not None and lowest < MIN_FREE else ""


async def docker_problem(timeout: float = PROBE_SECONDS) -> str:
    """:data:`DOCKER_DOWN` when Docker is installed but its daemon doesn't answer
    (stopped, or hung past ``timeout``), else ""."""
    if shutil.which(_PROBE[0]) is None:
        return ""  # Not installed: the app checks say so (joinery doctor).
    try:
        process = await asyncio.create_subprocess_exec(
            *_PROBE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        return ""
    try:
        _out, err = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return DOCKER_DOWN
    if process.returncode == 0:
        return ""
    return DOCKER_DOWN if _DOCKER_DOWN.search(err.decode("utf-8", "replace")) else ""


def explain(text: str, places: Iterable[Path] = ()) -> str:
    """The plain words for a tool's error that is the machine's, not the change's:
    a write with no space left, or Docker's daemon out of reach. "" otherwise."""
    if _NO_SPACE.search(text):
        lowest = [free for free in map(free_bytes, places) if free is not None]
        return disk_full(min(lowest) if lowest else None)
    if _DOCKER_DOWN.search(text):
        return DOCKER_DOWN
    return ""


#: ``off`` turns the check off (tests with stand-in workers, a scripted host).
SWITCH = "JOINERY_MACHINE_CHECK"


def provider_failure(text: str) -> bool:
    """An agent's app saying it stopped, in words that give no cause."""
    return _PROVIDER_FAILED.search(text) is not None


def checker(project: Path) -> Check:
    """The check a chat host runs before a turn and while a long step runs: the
    project's disk and Docker's, then Docker itself."""

    async def check() -> str:
        if os.environ.get(SWITCH, "").lower() == "off":
            return ""
        places = [project, *docker_data()]
        found = await asyncio.to_thread(disk_problem, places)
        return found or await docker_problem()

    return check
