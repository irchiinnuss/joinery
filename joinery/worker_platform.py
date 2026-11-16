"""Which platform each offline worker image runs as: native where it can be.

Godot publishes a Linux arm64 build, so the Godot check worker
(``joinery-godot-worker``: Godot's import and the project's tests) is built for
the host's own architecture and runs natively on Apple Silicon. Blender,
Material Maker and Surge XT publish no Linux arm64 build, so the shared tool
images that carry them (the runtime and every worker built on it) stay
linux/amd64 and run emulated on an arm64 host (docs/adr/0090).

A run asks for the platform of the image actually present, so an image built
before this, or on another machine, still runs as what it is; only a build
picks the platform from the host.
"""

from __future__ import annotations

import platform
import subprocess
import time

AMD64 = "linux/amd64"
ARM64 = "linux/arm64"
#: The Godot-only worker the checks run in when it is there.
GODOT_WORKER = "joinery-godot-worker"
#: Images whose every tool has a Linux arm64 build: built for the host.
NATIVE_CAPABLE = frozenset({GODOT_WORKER})
#: How long an image's inspected platform is trusted (a rebuild can change it).
_TTL = 60.0
_known: dict[str, tuple[float, str | None]] = {}


def host() -> str:
    """The host's own Linux platform for containers."""
    machine = platform.machine().lower()
    return ARM64 if machine in {"arm64", "aarch64"} else AMD64


def build_platform(image: str) -> str:
    """The platform to build ``image`` for: the host's, when every tool in it has
    a build for it; otherwise linux/amd64."""
    base = image.split(":", 1)[0]
    return host() if base in NATIVE_CAPABLE else AMD64


def present(image: str) -> str | None:
    """The platform of the local ``image`` (``linux/arm64``), None when it is not
    there or Docker can't say."""
    now = time.monotonic()
    known = _known.get(image)
    if known is not None and now - known[0] < _TTL:
        return known[1]
    try:
        done = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Os}}/{{.Architecture}}",
                image,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    found = done.stdout.strip() if done.returncode == 0 else ""
    value = found if found.startswith("linux/") else None
    _known[image] = (now, value)
    return value


def platform_for(image: str) -> str:
    """The ``--platform`` to run ``image`` with: what the local image is, else
    what it would be built as."""
    return present(image) or build_platform(image)


def emulated(image: str) -> bool:
    """``image`` runs under emulation on this host (an amd64 image on arm64)."""
    return platform_for(image) != host()


def forget() -> None:
    """Drop what is known about local images (after a build)."""
    _known.clear()
