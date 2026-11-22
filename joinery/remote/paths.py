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


def home_path() -> Path:
    """Where the per-user directory is, without making it."""
    override = os.environ.get(HOME_ENV, "").strip()
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Joinery/remote"
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        return Path(local) / "Joinery/remote"
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(state) / "joinery/remote"


#: Windows folders already given their private ACL by this process.
_PROTECTED: set[Path] = set()


def kept_private(folder: Path, *, parents: bool = False) -> Path:
    """``folder``, made if missing, and private (0700) every time it is used: a folder
    someone loosened since (or made before Joinery did) is put right, not trusted."""
    if not folder.is_dir():
        folder.mkdir(parents=parents, mode=0o700)
        private_directory(folder)
        _PROTECTED.add(folder)
    elif os.name != "nt":
        if folder.stat().st_mode & 0o077:
            private_directory(folder)
    elif folder not in _PROTECTED:
        private_directory(folder)
        _PROTECTED.add(folder)
    return folder


def remote_home() -> Path:
    """The per-user directory: device token hashes, pairing codes, the host list and
    the machine's audit log. Private (0700), checked on every use."""
    return kept_private(home_path(), parents=True)


def hosts_dir(home: Path | None = None) -> Path:
    """Where each running chat host advertises its remote channel."""
    base = kept_private(home, parents=True) if home else remote_home()
    return kept_private(base / "hosts")


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


def known_path(home: Path | None = None) -> Path:
    return (home or remote_home()) / "known.json"


def remember(project: Path, agent: str, home: Path | None = None) -> None:
    """Note a project a host served, so a phone can open it again with no editor open
    (the always-on helper). Never raises."""
    import time

    path = known_path(home)
    try:
        known = read_json(path)
        known = known if isinstance(known, dict) else {}
        known[project_id(project)] = {
            "project": str(project),
            "name": project.name,
            "agent": agent,
            "seen": time.time(),
        }
        # The newest 50.
        kept = dict(
            sorted(known.items(), key=lambda kv: -float(kv[1].get("seen", 0)))[:50]
        )
        write_private_json(path, kept)
    except (OSError, ValueError, TypeError):
        pass


def known(home: Path | None = None) -> dict[str, dict[str, Any]]:
    """{project id: {project, name, agent, seen}} for projects hosts served here."""
    data = read_json(known_path(home))
    if not isinstance(data, dict):
        return {}
    return {
        k: v
        for k, v in data.items()
        if isinstance(v, dict) and Path(str(v.get("project", ""))).is_dir()
    }
