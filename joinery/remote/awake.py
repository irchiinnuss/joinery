"""Keep the machine awake while Remote serves, if the user asks (ADR 0093). Off by default.

macOS: ``caffeinate -s``, which holds only while on AC power, for as long as the server
runs. Linux: ``systemd-inhibit`` blocking sleep, held only while the machine is on mains
power (``/sys/class/power_supply``), so a laptop on battery sleeps as usual. Windows:
the server process asks ``SetThreadExecutionState`` itself. A Mac laptop with its lid closed still sleeps unless
it is on power with an external display.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

#: Where Linux lists power supplies (each with ``type`` and ``online``).
POWER_SUPPLIES = Path("/sys/class/power_supply")


def on_mains(root: Path | None = None) -> bool:
    """Linux: whether the machine runs on mains power. One with no mains supply
    listed (a desktop, a VM) counts as on mains; one whose mains supplies are all
    offline runs on battery."""
    root = POWER_SUPPLIES if root is None else root
    mains: list[bool] = []
    try:
        supplies = list(root.iterdir())
    except OSError:
        return True
    for supply in supplies:
        try:
            kind = (supply / "type").read_text().strip()
            if kind == "Mains":
                mains.append((supply / "online").read_text().strip() == "1")
        except OSError:
            continue
    return not mains or any(mains)


class KeepAwake:
    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.windows = False

    @property
    def on(self) -> bool:
        return self.windows or (
            self.process is not None and self.process.poll() is None
        )

    def start(self) -> bool:
        """Hold the machine awake; False when this system has no way to."""
        if self.on:
            return True
        if sys.platform == "darwin" and shutil.which("caffeinate"):
            argv = ["caffeinate", "-s", "-w", str(os.getpid())]
        elif sys.platform.startswith("linux") and shutil.which("systemd-inhibit"):
            argv = [
                "systemd-inhibit",
                "--what=sleep:idle",
                "--who=Joinery Remote",
                "--why=Your phone can reach Joinery",
                "--mode=block",
                "sleep",
                "infinity",
            ]
        elif os.name == "nt":
            self.windows = _windows_state(True)
            return self.windows
        else:
            return False
        self.process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        if self.windows:
            _windows_state(False)
            self.windows = False

    def follow(self, wanted: bool) -> None:
        if wanted and sys.platform.startswith("linux") and not on_mains():
            wanted = False  # On battery: sleep as usual (caffeinate -s does the same).
        if wanted and not self.on:
            self.start()
        elif not wanted and self.on:
            self.stop()


def _windows_state(on: bool) -> bool:
    import ctypes

    native: Any = ctypes  # windll exists only on Windows.
    continuous, system = 0x80000000, 0x00000001
    flags = continuous | system if on else continuous
    return bool(native.windll.kernel32.SetThreadExecutionState(flags))
