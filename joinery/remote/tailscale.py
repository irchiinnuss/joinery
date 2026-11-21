"""Tailscale, the one way Joinery Remote is reached from away (ADR 0093).

Joinery's server binds to loopback only. ``tailscale serve`` puts it on the machine's
own tailnet name with a real HTTPS certificate, over WireGuard, with no port opened to
the internet. Joinery only reads Tailscale's status and prints (or, when the user says
so, runs) that one command; it never changes Tailscale's settings otherwise.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

MAC_APP = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")


def executable() -> str:
    """The ``tailscale`` command, or "" when Tailscale isn't installed."""
    found = shutil.which("tailscale")
    if found:
        return found
    if sys.platform == "darwin" and MAC_APP.is_file():
        return str(MAC_APP)
    return ""


def status(program: str | None = None) -> dict[str, Any]:
    """``{installed, running, dns_name, origin, message}`` from ``tailscale status``."""
    program = executable() if program is None else program
    if not program:
        return {
            "installed": False,
            "running": False,
            "dns_name": "",
            "origin": "",
            "message": "Tailscale isn't installed. Install it on this machine and your "
            "phone, then sign both in to the same tailnet.",
        }
    try:
        done = subprocess.run(
            [program, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        data = json.loads(done.stdout) if done.stdout.strip() else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        data = {}
    state = str(data.get("BackendState", "")) if isinstance(data, dict) else ""
    me = data.get("Self") if isinstance(data, dict) else None
    name = str(me.get("DNSName", "")).rstrip(".") if isinstance(me, dict) else ""
    running = state == "Running" and bool(name)
    return {
        "installed": True,
        "running": running,
        "dns_name": name,
        "origin": f"https://{name}" if running else "",
        "message": ""
        if running
        else "Tailscale is installed but not connected. Sign in and connect it.",
    }


def serve_command(port: int, program: str = "tailscale") -> list[str]:
    """The command that serves Joinery Remote on the tailnet name, in the background."""
    return [program, "serve", "--bg", "--https=443", f"http://127.0.0.1:{port}"]


def serve(port: int, program: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        serve_command(port, program),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
