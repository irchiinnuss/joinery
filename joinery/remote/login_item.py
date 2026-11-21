"""The always-on helper as a login item (ADR 0093): ``joinery remote install-helper``.

A per-user LaunchAgent on macOS, a ``systemd --user`` unit on Linux, a logon task on
Windows. Each runs ``joinery remote serve --helper`` and is removed again by
``joinery remote uninstall-helper``. Nothing runs as another user or at boot.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from joinery.remote.paths import HOME_ENV

LABEL = "dev.joinery.remote"
UNIT = "joinery-remote.service"
TASK = "Joinery Remote"
#: Tests: where the files go, and that nothing is loaded.
DIR_ENV = "JOINERY_REMOTE_LOGIN_DIR"
NO_LOAD_ENV = "JOINERY_REMOTE_NO_LOAD"


def command() -> list[str]:
    """What the login item runs. In an installed Joinery (the app sets ``JOINERY_HOME``)
    it goes through ``uv`` against the app's own folder, as the app's launcher does, so
    an update in place is picked up; a checkout runs its own Python."""
    home = os.environ.get("JOINERY_HOME", "")
    uv = shutil.which("uv")
    if home and uv:
        return [
            uv,
            "run",
            "--frozen",
            "--quiet",
            "--project",
            home,
            "joinery",
            "remote",
            "serve",
            "--helper",
        ]
    return [sys.executable, "-m", "joinery.remote.cli", "serve", "--helper"]


def _env() -> dict[str, str]:
    keep = {
        k: os.environ[k]
        for k in (HOME_ENV, "JOINERY_HOME", "UV_PROJECT_ENVIRONMENT", "UV_NO_DEV")
        if os.environ.get(k)
    }
    # A login item starts with a bare PATH: uv, git and the tools need the user's.
    keep["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    return keep


def path() -> Path:
    override = os.environ.get(DIR_ENV, "")
    if sys.platform == "darwin":
        base = Path(override) if override else Path.home() / "Library/LaunchAgents"
        return base / f"{LABEL}.plist"
    if os.name == "nt":
        base = Path(override) if override else Path.home() / "AppData/Local/Joinery"
        return base / "remote-task.txt"
    config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    base = Path(override) if override else Path(config) / "systemd/user"
    return base / UNIT


def contents(log: Path) -> bytes:
    """The login item's file for this system."""
    if sys.platform == "darwin":
        return plistlib.dumps(
            {
                "Label": LABEL,
                "ProgramArguments": command(),
                "RunAtLoad": True,
                # Started again if it crashes; not after `joinery remote stop`.
                "KeepAlive": {"SuccessfulExit": False},
                "EnvironmentVariables": _env(),
                "StandardErrorPath": str(log),
                "ProcessType": "Background",
            }
        )
    if os.name == "nt":
        return subprocess.list2cmdline(command()).encode()
    env = "".join(f"Environment={k}={shlex.quote(v)}\n" for k, v in _env().items())
    return (
        "[Unit]\nDescription=Joinery Remote\n\n"
        f"[Service]\nExecStart={shlex.join(command())}\n{env}"
        "Restart=on-failure\n\n[Install]\nWantedBy=default.target\n"
    ).encode()


def _load(argv: list[str]) -> None:
    if os.environ.get(NO_LOAD_ENV) == "1":
        return
    subprocess.run(argv, capture_output=True, timeout=30, check=False)


def install(log: Path) -> dict[str, Any]:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(contents(log))
    if sys.platform == "darwin":
        domain = f"gui/{os.getuid()}"
        _load(["launchctl", "bootout", domain, str(target)])
        _load(["launchctl", "bootstrap", domain, str(target)])
    elif os.name == "nt":
        _load(
            [
                "schtasks",
                "/Create",
                "/F",
                "/SC",
                "ONLOGON",
                "/TN",
                TASK,
                "/TR",
                target.read_text(),
            ]
        )
    else:
        _load(["systemctl", "--user", "daemon-reload"])
        _load(["systemctl", "--user", "enable", "--now", UNIT])
    return status()


def uninstall() -> dict[str, Any]:
    target = path()
    if sys.platform == "darwin":
        _load(["launchctl", "bootout", f"gui/{os.getuid()}", str(target)])
    elif os.name == "nt":
        _load(["schtasks", "/Delete", "/F", "/TN", TASK])
    else:
        _load(["systemctl", "--user", "disable", "--now", UNIT])
    target.unlink(missing_ok=True)
    if sys.platform != "darwin" and os.name != "nt":
        _load(["systemctl", "--user", "daemon-reload"])
    return status()


def refresh(log: Path) -> bool:
    """Rewrite an installed login item when this install would write it differently
    (Joinery moved or was updated). True when it changed."""
    target = path()
    if not target.is_file() or target.read_bytes() == contents(log):
        return False
    install(log)
    return True


def status() -> dict[str, Any]:
    target = path()
    return {"installed": target.is_file(), "path": str(target)}
