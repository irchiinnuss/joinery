"""Where Joinery Remote keeps its per-user state: never in a project, never in git."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from joinery.platform_support import private_directory, private_file

#: Overrides the per-user directory (tests, a second install).
HOME_ENV = "JOINERY_REMOTE_HOME"


def remote_home() -> Path:
    """The per-user directory: device token hashes, pairing codes, the host list and
    the machine's audit log. Created private (0700) on first use."""
    override = os.environ.get(HOME_ENV, "").strip()
    if override:
        base = Path(override)
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support/Joinery/remote"
    elif os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        base = Path(local) / "Joinery/remote"
    else:
        state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
        base = Path(state) / "joinery/remote"
    if not base.is_dir():
        base.mkdir(parents=True, mode=0o700)
        private_directory(base)
    return base


def hosts_dir(home: Path | None = None) -> Path:
    """Where each running chat host advertises its remote channel."""
    base = home or remote_home()
    if not base.is_dir():
        base.mkdir(parents=True, mode=0o700)
        private_directory(base)
    folder = base / "hosts"
    if not folder.is_dir():
        folder.mkdir(mode=0o700)
        private_directory(folder)
    return folder


def project_id(project: Path) -> str:
    """A project's id in URLs and file names: never its path."""
    return hashlib.sha256(str(project.resolve()).encode()).hexdigest()[:16]


def write_private_json(path: Path, data: Any) -> None:
    """Write ``data`` to ``path`` atomically, readable by this user only."""
    descriptor, temp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        private_file(Path(temp))
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=1, sort_keys=True)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    """``path`` as JSON, or None when it is missing or not JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
