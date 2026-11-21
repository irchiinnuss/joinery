"""Keeping the Remote server running, at this install's version (ADR 0093).

"Remote on" is a setting, so it outlives a restart of the machine; the server process
doesn't. Whenever Remote is on, an editor's chat host and ``joinery remote start`` call
:func:`ensure_server`: it starts the server when none runs, and replaces one that runs an
older Joinery (after an update), so a phone always meets the code that is installed.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from joinery import __version__
from joinery.platform_support import ProcessWatch, private_file
from joinery.remote import login_item
from joinery.remote.paths import read_json, remote_home
from joinery.remote.store import Store

SERVER_FILE = "server.json"
LOG_FILE = "server.log"
#: Tests and CI: hosts never start a server by themselves.
NO_SERVER_ENV = "JOINERY_REMOTE_NO_SERVER"


def server_info(home: Path | None = None) -> dict[str, Any]:
    """``{running, pid, port, version}`` of the server this user started."""
    data = read_json((home or remote_home()) / SERVER_FILE)
    if not isinstance(data, dict) or not isinstance(data.get("pid"), int):
        return {"running": False, "pid": 0, "port": 0, "version": ""}
    watch = ProcessWatch(int(data["pid"]))
    try:
        running = watch.alive()
    finally:
        watch.close()
    return {
        "running": running,
        "pid": data["pid"],
        "port": int(data.get("port", 0)),
        "version": str(data.get("version", "")),
    }


def stop_server(home: Path | None = None) -> bool:
    info = server_info(home)
    if not info["running"]:
        return False
    # Only the server this user started, by the pid it wrote itself.
    os.kill(int(info["pid"]), signal.SIGTERM)
    for _ in range(50):
        if not server_info(home)["running"]:
            break
        time.sleep(0.1)
    return True


def ensure_server(store: Store, port: int = 0, *, wait: bool = True) -> dict[str, Any]:
    """A server of this version runs while Remote is on: started, or restarted after an
    update. Returns :func:`server_info`."""
    home = store.home
    info = server_info(home)
    if info["running"] and info["version"] == __version__:
        return info
    if os.environ.get(NO_SERVER_ENV) == "1":
        return info
    if info["running"]:
        stop_server(home)
    port = port or int(store.config()["port"])
    helper = login_item.status()["installed"]
    if helper:
        # The helper's launcher picks up an update itself.
        login_item.refresh(home / LOG_FILE)
    log = home / LOG_FILE
    with log.open("ab") as stream:
        private_file(log)
        flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "joinery.remote.cli",
                "serve",
                "--port",
                str(port),
                *(["--helper"] if helper else []),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stream,
            creationflags=flags,
            start_new_session=os.name != "nt",
        )
    if wait:
        for _ in range(50):
            if server_info(home)["running"]:
                break
            time.sleep(0.1)
    return server_info(home)


def host_wants_server(home: Path | None = None) -> None:
    """From an editor's chat host while Remote is on: make sure the server runs. Never
    raises; never for the hosts the helper starts (the helper is the server)."""
    if os.environ.get("JOINERY_REMOTE_HELPER"):
        return
    try:
        ensure_server(Store(home), wait=False)
    except (OSError, ValueError):
        pass
