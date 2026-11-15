"""Whether a project's Godot import cache is current, and bringing it up to date.

Godot plays a project from its import cache (``.godot/``): imported models, textures
and sounds under ``.godot/imported`` and the table of script classes. A project that
gained a model or a ``class_name`` since its last import fails to load them ("Could
not find type", "Cannot open file 'res://.godot/imported/…'") until the cache is
rebuilt, which the editor does when it opens and headless runs never do by themselves.

:func:`stale` says what is out of date, cheaply (file times and names, no hashing):
an imported file newer than its ``.import``, or whose ``.import`` names an imported
copy that isn't there; a script with no ``.uid`` beside it, or whose ``class_name``
the class table doesn't list. :func:`ensure` runs ``godot --headless --import`` when
anything is. Playtests, store media, replays and save checks call it before they
play, and :func:`after_accept` refreshes the cache once a change is accepted.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path

from joinery.staged_assets import IMPORTED_SUFFIXES

CACHE = ".godot"
CLASS_TABLE = ".godot/global_script_class_cache.cfg"
#: Seconds one import may take.
TIMEOUT = 600
#: Reasons listed at most (one is enough to import).
LIMIT = 20
_CLASS_NAME = re.compile(r"^\s*class_name\s+(\w+)", re.MULTILINE)
_DEST = re.compile(r'"(res://\.godot/[^"]+)"')
#: Folders Godot never imports.
_SKIP = frozenset({".godot", ".git", ".joinery", "node_modules", "__pycache__"})
#: One import at a time in this process: a second caller then finds it current.
_LOCK = threading.Lock()


def _files(project: Path) -> Iterator[Path]:
    for root, dirs, files in os.walk(project):
        here = Path(root)
        if (here / ".gdignore").exists() and here != project:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in _SKIP and not d.startswith(".")]
        for name in files:
            yield here / name


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _asset_stale(project: Path, path: Path) -> bool:
    record = path.with_name(path.name + ".import")
    if not record.is_file() or _mtime(path) > _mtime(record):
        return True
    try:
        text = record.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    if "valid=false" in text:
        return False  # Godot keeps it unimported on purpose.
    dests = next(
        (line for line in text.splitlines() if line.startswith("dest_files=")), ""
    )
    return any(
        not (project / dest.removeprefix("res://")).is_file()
        for dest in _DEST.findall(dests)
    )


def _script_stale(path: Path, classes: str, table_time: float) -> bool:
    if not path.with_name(path.name + ".uid").is_file():
        return True
    if _mtime(path) <= table_time:
        return False
    try:
        found = _CLASS_NAME.search(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False
    return found is not None and f'&"{found.group(1)}"' not in classes


def stale(project: Path, limit: int = LIMIT) -> list[str]:
    """What the import cache is missing (project-relative), or [] when it is current."""
    if not (project / CACHE).is_dir():
        return [CACHE]
    table = project / CLASS_TABLE
    try:
        classes = table.read_text(encoding="utf-8", errors="replace")
    except OSError:
        classes = ""
    table_time = _mtime(table)
    found: list[str] = []
    for path in _files(project):
        suffix = path.suffix.lower()
        if suffix in IMPORTED_SUFFIXES:
            out = _asset_stale(project, path)
        elif suffix == ".gd":
            out = _script_stale(path, classes, table_time)
        else:
            continue
        if out:
            found.append(path.relative_to(project).as_posix())
            if len(found) >= limit:
                break
    return found


def loadable_marks(project: Path) -> dict[str, tuple[int, int]]:
    """Size and time of every file Godot imports (with its ``.import`` settings)
    and every script: two marks that differ mean an import has work to do."""
    marks: dict[str, tuple[int, int]] = {}
    for path in _files(project):
        suffix = path.suffix.lower()
        if suffix in IMPORTED_SUFFIXES or suffix in {".import", ".gd"}:
            try:
                stat = path.stat()
            except OSError:
                continue
            marks[path.relative_to(project).as_posix()] = (
                stat.st_size,
                stat.st_mtime_ns,
            )
    return marks


def run_import(godot: Path, project: Path, env: Mapping[str, str] | None = None) -> None:
    subprocess.run(
        [str(godot), "--headless", "--path", str(project), "--import"],
        env=dict(env) if env is not None else None,
        capture_output=True,
        timeout=TIMEOUT,
        check=False,
    )


def ensure(godot: Path, project: Path, env: Mapping[str, str] | None = None) -> bool:
    """Import the project when its cache is missing or stale. True when it imported."""
    with _LOCK:
        if not stale(project, limit=1):
            return False
        try:
            run_import(godot, project, env)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return True


def touches_imports(paths: Iterable[str]) -> bool:
    """A change to any of ``paths`` can leave the import cache stale."""
    return any(
        Path(p).suffix.lower() in IMPORTED_SUFFIXES
        or Path(p).suffix.lower() == ".gd"
        or p == "project.godot"
        for p in paths
    )


def after_accept(
    project: Path,
    changes: Iterable[Mapping[str, str]],
    *,
    wait: bool = False,
    godot: Path | None = None,
    then: Callable[[], object] | None = None,
) -> threading.Thread | None:
    """Bring the cache up to date after a change was accepted, so the next playtest,
    media run or replay plays what was accepted. Only a project that has a cache
    (one Godot opened) is imported; without one the next run makes it. Runs in the
    background unless ``wait``; returns the thread, or None when nothing needs it.
    ``then`` runs once the import is done (it commits the ``.uid`` and ``.import``
    files Godot wrote: FileTransaction.commit_generated)."""
    if not (project / CACHE).is_dir():
        return None
    if not touches_imports(str(c.get("path", "")) for c in changes):
        return None
    if godot is None:
        from joinery import playtest

        try:
            godot = playtest.godot_binary()
        except (OSError, ValueError, RuntimeError):
            return None
    if not godot.is_file():
        return None
    binary = godot

    def run() -> None:
        ensure(binary, project)
        if then is not None:
            then()

    if wait:
        run()
        return None
    thread = threading.Thread(target=run, name="joinery-import", daemon=True)
    thread.start()
    return thread
