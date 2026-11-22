"""Tailscale, the one way Joinery Remote is reached from away (ADR 0093).

Joinery's server binds to loopback only. ``tailscale serve`` puts it on the machine's
own tailnet name with a real HTTPS certificate, over WireGuard, with no port opened to
the internet. Joinery only reads Tailscale's status and prints (or, when the user says
so, runs) that one command; it never changes Tailscale's settings otherwise.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

MAC_APP = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
#: The ``tailscale`` program to use instead of the installed one (tests); "none" means
#: Tailscale isn't installed.
PROGRAM_ENV = "JOINERY_TAILSCALE"


def executable() -> str:
    """The ``tailscale`` command, or "" when Tailscale isn't installed."""
    chosen = os.environ.get(PROGRAM_ENV)
    if chosen is not None:
        return "" if chosen.strip() in {"", "none"} else chosen
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


#: What a ``tailscale serve`` handler says when it proxies to a loopback port.
_LOOPBACK_PROXY = re.compile(
    r"^(?:http://)?(?:127\.0\.0\.1|localhost|\[::1\]):(\d+)/?$", re.IGNORECASE
)
UNKNOWN: dict[str, Any] = {"known": False, "origin": "", "funnel": False}


def served(port: int, program: str | None = None) -> dict[str, Any]:
    """Whether ``tailscale serve`` carries this machine's ``127.0.0.1:<port>`` right now.

    Read from ``tailscale serve status --json``, which prints Tailscale's
    ``ipn.ServeConfig``: ``Web`` maps ``"<name>:<https port>"`` to handlers by mount
    point, ``AllowFunnel`` marks the same keys public, and ``Foreground`` holds the
    config of a ``tailscale serve`` running in a terminal. Returns ``{known, origin,
    funnel}``: ``known`` is False when the status couldn't be read (nothing is then
    concluded), ``origin`` is the ``https://`` address that proxies ``/`` to the port on
    the tailnet only, and ``funnel`` is True when such an address is also public through
    Funnel, which Remote never uses.
    """
    program = executable() if program is None else program
    if not program:
        return dict(UNKNOWN)
    try:
        done = subprocess.run(
            [program, "serve", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        text = (done.stdout or "").strip()
        data = json.loads(text) if text else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return dict(UNKNOWN)
    if done.returncode != 0 or not isinstance(data, dict):
        return dict(UNKNOWN)
    configs: list[Any] = [data]
    foreground = data.get("Foreground")
    if isinstance(foreground, dict):
        configs += list(foreground.values())
    found, funnel = "", False
    for config in configs:
        if not isinstance(config, dict) or not isinstance(config.get("Web"), dict):
            continue
        public = config.get("AllowFunnel")
        public = public if isinstance(public, dict) else {}
        for host_port, site in sorted(config["Web"].items()):
            handlers = site.get("Handlers") if isinstance(site, dict) else None
            root = handlers.get("/") if isinstance(handlers, dict) else None
            proxy = str(root.get("Proxy", "")) if isinstance(root, dict) else ""
            match = _LOOPBACK_PROXY.match(proxy.strip())
            address = _origin(str(host_port))
            if not match or int(match.group(1)) != port or not address:
                continue
            if public.get(host_port):
                funnel = True
            else:
                found = found or address
    return {"known": True, "origin": found, "funnel": funnel}


def _origin(host_port: str) -> str:
    """``mac.tail1234.ts.net:443`` as ``https://mac.tail1234.ts.net``: the Origin a
    browser sends there, and (without the scheme) the Host ``tailscale serve`` passes
    on unchanged."""
    host, _, port = host_port.rpartition(":")
    host = host.lower().rstrip(".")
    if not port.isdigit() or not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host):
        return ""
    return f"https://{host}" if port == "443" else f"https://{host}:{port}"


def serve_command(port: int, program: str = "tailscale") -> list[str]:
    """The command that serves Joinery Remote on the tailnet name, in the background."""
    return [program, "serve", "--bg", "--https=443", f"http://127.0.0.1:{port}"]


#: Where Tailscale says to turn on Serve or HTTPS for the tailnet, in its output.
ENABLE_LINK = re.compile(r"https://login\.tailscale\.com/\S+")


def serve(port: int, program: str) -> tuple[bool, str]:
    """Run :func:`serve_command`. (ok, what to tell the user). A tailnet without Serve or
    HTTPS turned on makes Tailscale print a link and wait: that link is returned."""
    try:
        done = subprocess.run(
            serve_command(port, program),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        said, ok = (done.stdout or "") + (done.stderr or ""), done.returncode == 0
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or b""
        said = out.decode(errors="replace") if isinstance(out, bytes) else str(out)
        ok = False
    except OSError as exc:
        return False, f"Tailscale couldn't be run: {exc}"
    link = ENABLE_LINK.search(said)
    if link:
        return False, (
            "Turn on HTTPS for your tailnet first (one click in Tailscale), then try "
            f"again: {link.group(0)}"
        )
    return ok, said.strip() or ("" if ok else "tailscale serve failed.")
