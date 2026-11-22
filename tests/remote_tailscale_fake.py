"""A stand-in ``tailscale`` program for Joinery Remote's tests: never the real one.

It answers ``status --json`` and ``serve status --json`` from files beside it, and
``serve --bg --https=443 http://127.0.0.1:<port>`` by writing the serve config Tailscale
would then report. Every call is logged to ``calls.log``.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

NAME = "mac.tail1234.ts.net"

SCRIPT = r"""
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
with open(os.path.join(here, "calls.log"), "a") as log:
    log.write(json.dumps(args) + "\n")
def read(name):
    try:
        with open(os.path.join(here, name)) as stream:
            return stream.read()
    except OSError:
        return "{}"
if args[:1] == ["status"]:
    print(read("status.json"))
elif args[:2] == ["serve", "status"]:
    print(read("serve.json"))
elif args[:1] == ["serve"]:
    me = json.loads(read("status.json"))["Self"]["DNSName"].rstrip(".")
    target = args[-1]
    config = {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {me + ":443": {"Handlers": {"/": {"Proxy": target}}}},
    }
    with open(os.path.join(here, "serve.json"), "w") as stream:
        json.dump(config, stream)
else:
    sys.exit(2)
"""


def serve_config(
    port: int, name: str = NAME, *, https: int = 443, funnel: bool = False
) -> dict[str, Any]:
    """What ``tailscale serve status --json`` prints while ``port`` is served."""
    key = f"{name}:{https}"
    config: dict[str, Any] = {
        "TCP": {str(https): {"HTTPS": True}},
        "Web": {key: {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{port}"}}}},
    }
    if funnel:
        config["AllowFunnel"] = {key: True}
    return config


def install(
    folder: Path,
    *,
    running: bool = True,
    serve: dict[str, Any] | None = None,
    name: str = NAME,
) -> str:
    """Make the fake in ``folder``; returns the program to run."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "fake_tailscale.py").write_text(SCRIPT, encoding="utf-8")
    status = {
        "BackendState": "Running" if running else "Stopped",
        "Self": {"DNSName": name + "."},
    }
    (folder / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (folder / "serve.json").write_text(json.dumps(serve or {}), encoding="utf-8")
    if os.name == "nt":
        program = folder / "tailscale.cmd"
        program.write_text(
            f'@"{sys.executable}" "{folder / "fake_tailscale.py"}" %*\n',
            encoding="utf-8",
        )
    else:
        program = folder / "tailscale"
        program.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{folder / "fake_tailscale.py"}" "$@"\n',
            encoding="utf-8",
        )
        program.chmod(program.stat().st_mode | stat.S_IXUSR)
    return str(program)


def calls(folder: Path) -> list[list[str]]:
    log = folder / "calls.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]
