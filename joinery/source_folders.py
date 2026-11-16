"""Folders that hold native sources and recipes, never game assets: Godot skips them.

``recipes/`` (replay scripts, preimages, history), ``art/sources/`` (``.blend``,
``.kra``, ``.ptex``, the maps a model's ``.blend`` loads), ``art/reference/`` and
``audio/sources/`` (Surge and SuperCollider patches, raw renders) are for the
tools, not the game. Each gets an empty ``.gdignore`` so Godot neither imports
nor parses what is inside: a project gets them when it is made, a change gets
one when a tool first writes into such a folder (``joinery.locking.write_lock``),
and ``joinery update-core`` adds the missing ones to an existing project.

A folder the game loads a file from is never marked: an ignored file is a missing
file to Godot. :func:`game_uses` names those files, so whoever marks the folder
can say why it didn't.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

#: Godot's "don't import or parse this folder" marker.
MARKER = ".gdignore"
#: Joinery's own state in a project (backups, stages, chats, caches): never the game's.
STATE = ".joinery"
#: Source-only folders, project-relative. Godot skips subfolders of an ignored one.
SOURCE_ONLY = ("recipes", "art/sources", "art/reference", "audio/sources")
#: Game files whose ``res://`` paths load other files.
_GAME_TEXT = frozenset({".tscn", ".tres", ".gd", ".gdshader", ".cfg", ".godot"})
#: Never game files: Godot's, Joinery's and git's own state, and the add-ons.
_SKIPPED = frozenset({".godot", ".git", ".joinery", "addons", "node_modules"})
_RES = re.compile(r"res://([^\"'\s)]+)")


def source_folder(path: str) -> str:
    """The source-only folder holding a project-relative ``path``, else ""."""
    pure = PurePosixPath(path.removeprefix("res://").lstrip("/"))
    for folder in SOURCE_ONLY:
        if pure.is_relative_to(folder) and pure != PurePosixPath(folder):
            return folder
    return ""


def ignored_folder(root: Path, path: str) -> str:
    """The folder with a ``.gdignore`` that hides ``path`` from Godot, else ""."""
    parts = PurePosixPath(path.removeprefix("res://").lstrip("/")).parts[:-1]
    for depth in range(1, len(parts) + 1):
        folder = "/".join(parts[:depth])
        if (root / folder / MARKER).exists():
            return folder
    return ""


def game_uses(root: Path, folder: str) -> list[str]:
    """Files under ``folder`` that the game loads by ``res://`` path from a scene,
    resource, script, shader or ``project.godot`` outside the source folders."""
    prefix = folder.rstrip("/") + "/"
    used: set[str] = set()
    for directory, children, names in os.walk(root, followlinks=False):
        here = Path(directory)
        rel = here.relative_to(root).as_posix()
        if rel != "." and (source_folder(rel + "/x") or (here / MARKER).exists()):
            children[:] = []
            continue
        children[:] = [c for c in children if c not in _SKIPPED]
        for name in names:
            if Path(name).suffix not in _GAME_TEXT:
                continue
            try:
                text = (here / name).read_text(errors="replace")
            except OSError:
                continue
            used.update(p for p in _RES.findall(text) if p.startswith(prefix))
    return sorted(used)


def mark(root: Path, *, create: bool = False) -> tuple[list[str], dict[str, list[str]]]:
    """Write a ``.gdignore`` into each source-only folder under ``root`` that has
    none. With ``create`` every source-only folder is made first (a new project).

    Returns (the markers written, project-relative; {folder: the game files it
    holds} for folders left unmarked because the game loads files from them).
    """
    if create:
        for folder in SOURCE_ONLY:
            (root / folder).mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    held: dict[str, list[str]] = {}
    for folder in SOURCE_ONLY:
        path = root / folder
        if not path.is_dir() or path.is_symlink() or (path / MARKER).exists():
            continue
        used = game_uses(root, folder)
        if used:
            held[folder] = used
            continue
        (path / MARKER).write_text("")
        written.append(f"{folder}/{MARKER}")
    return written, held


def hide_state(project: Path) -> None:
    """Give Joinery's own folder (``.joinery/``) a ``.gdignore``.

    Godot's file system skips hidden folders, but its GDScript language server
    lists every ``.gd`` under the project unless a ``.gdignore`` stops it. Without
    the marker the editor parses the backups and stages kept there (a change's
    ``original/`` files, old add-on copies) and reports each ``class_name`` twice
    ("Class "Player" hides a global script class"). Called whenever Joinery makes
    or updates a project, starts a change, and checks the add-on as the editor opens.
    """
    folder = project / STATE
    marker = folder / MARKER
    if marker.exists() or not (project / "project.godot").is_file():
        return
    try:
        folder.mkdir(exist_ok=True)
        marker.write_text("")
    except OSError:
        pass  # A read-only project: nothing Joinery writes there either.


def missing(root: Path) -> list[str]:
    """The ``.gdignore`` markers :func:`mark` would write, without writing them."""
    return [
        f"{folder}/{MARKER}"
        for folder in SOURCE_ONLY
        if (root / folder).is_dir()
        and not (root / folder).is_symlink()
        and not (root / folder / MARKER).exists()
        and not game_uses(root, folder)
    ]
