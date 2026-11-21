"""Joinery Remote's audit log: every remote action and refusal, every pairing and revoke.

One line of JSON per entry, in the per-user directory (``audit.jsonl``) and, for an
action in a project, also in the project's ``.joinery/remote/audit.jsonl`` (git-ignored
with the rest of ``.joinery``). No message bodies: the chat's own record has them.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from joinery.platform_support import private_file
from joinery.remote.paths import remote_home

FILE = "audit.jsonl"
PROJECT_DIR = ".joinery/remote"
#: Bigger than this, the log keeps its newer half.
LIMIT_BYTES = 2_000_000


def record(
    action: str,
    result: str,
    *,
    device: dict[str, Any] | None = None,
    project: Path | None = None,
    home: Path | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Append one entry; ``result`` is ok, refused or error. Never raises."""
    entry: dict[str, Any] = {"ts": time.time(), "action": action, "result": result}
    if device is not None:
        entry["device_id"] = str(device.get("id", ""))
        entry["device_name"] = str(device.get("name", ""))
    if project is not None:
        entry["project"] = str(project)
    entry.update({k: v for k, v in fields.items() if v not in (None, "")})
    targets = [(home or remote_home()) / FILE]
    if project is not None and (project / ".joinery").is_dir():
        targets.append(project / PROJECT_DIR / FILE)
    for path in targets:
        try:
            _append(path, entry)
        except OSError:
            pass
    return entry


def _append(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8") as stream:
        if new:
            private_file(path)
        stream.write(json.dumps(entry, sort_keys=True) + "\n")
    if path.stat().st_size > LIMIT_BYTES:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        temp = path.with_suffix(".tmp")
        temp.write_text("".join(lines[len(lines) // 2 :]), encoding="utf-8")
        os.replace(temp, path)


def entries(
    limit: int = 200, project: Path | None = None, home: Path | None = None
) -> list[dict[str, Any]]:
    """The newest ``limit`` entries, newest last."""
    path = (
        project / PROJECT_DIR / FILE
        if project is not None
        else (home or remote_home()) / FILE
    )
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            found.append(item)
    return found
