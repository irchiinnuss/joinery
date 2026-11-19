"""The asset gate over what a staged change touched, fast enough for every step.

Precheck's "assets" check (``lockfile.check_assets``) hashes every asset of the
stage. A to-do built a step at a time can't wait for that after each step, yet
a step that leaves a model out of step with its ``joinery.lock`` entry (or a
half-made one with none) blocks every step after it, and nothing says so until
precheck at the very end. So when a step is marked done or skipped, the same
check runs over the asset files the change touched only: found by a stat
comparison with the project (size and modification time; staging clones keep
both) and by the lock entries that differ from the project's. Only those files
are hashed, and Godot is not involved.

:func:`gate` is the gate a whole change passes at submit: every problem is found,
but only those with the assets the change touched hold it. One with an asset it
didn't touch (a recipe's output gone from disk since an earlier change, say) is
the project's, not this change's: it is said, with how to fix it, and never
blocks the change. Rule Zero and provenance stay strict for every file the change
makes, removes or rewrites, and for its own lock entries.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from joinery.catalog import load_catalog
from joinery.lockfile import LOCK_NAME, asset_files, asset_roots, check_assets

#: How to fix an asset problem a change didn't make.
ELSEWHERE_FIX = (
    "To fix it, ask Journeyman to make that asset again from its source, or to "
    "remove it and its recipe."
)


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        found = os.stat(path)
    except OSError:
        return None
    return found.st_size, found.st_mtime_ns


def _entries(project: Path) -> dict[str, dict[str, Any]]:
    try:
        data = tomllib.loads((project / LOCK_NAME).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {
        str(entry.get("path")): entry
        for entry in data.get("asset", [])
        if isinstance(entry, dict)
    }


def changed_paths(project: Path, stage: Path) -> set[str]:
    """Asset files the stage made, removed or rewrote, and those whose lock entry
    it changed, as project paths."""
    staged, original = asset_files(stage), asset_files(project)
    changed = staged ^ original
    for rel in staged & original:
        if _stat(stage / rel) != _stat(project / rel):
            changed.add(rel)
    before, after = _entries(project), _entries(stage)
    changed |= {
        rel for rel in before.keys() | after.keys() if before.get(rel) != after.get(rel)
    }
    return changed


def problems(project: Path, stage: Path) -> list[str]:
    """The asset gate's problems with what the stage changed ([] when none)."""
    paths = changed_paths(project, stage)
    if not paths:
        return []
    return check_assets(stage, load_catalog(), paths)


def split(
    stage: Path, found: Iterable[str], touched: set[str]
) -> tuple[list[str], list[str]]:
    """``found`` asset-gate lines as (those that hold the change, those about an
    asset under art/ or audio/ it didn't touch). A line about anything else (the
    lock itself, an extension) always holds it."""
    roots = tuple(
        folder.relative_to(stage).as_posix() + "/" for folder in asset_roots(stage)
    )
    held: list[str] = []
    elsewhere: list[str] = []
    for line in found:
        rel = line.split(": ", 1)[0]
        if rel.startswith(roots) and rel not in touched:
            elsewhere.append(line)
        else:
            held.append(line)
    return held, elsewhere


def gate(
    project: Path,
    stage: Path,
    also: Iterable[str] = (),
    found: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """The asset gate for the change staged in ``stage``: (problems with what it
    touched, which hold it; problems with assets it didn't touch, which don't).
    ``also`` are further paths the change touched (its diff); ``found`` the whole
    gate's problems when the caller ran it already."""
    touched = changed_paths(project, stage) | set(also)
    if found is None:
        found = check_assets(stage, load_catalog())
    return split(stage, found, touched)


def elsewhere_note(lines: list[str]) -> str:
    """The review's line for asset problems a change didn't make."""
    shown = "; ".join(lines[:3]) + (
        f"; and {len(lines) - 3} more" if len(lines) > 3 else ""
    )
    return (
        "Not part of this change, so it isn't held: "
        + (
            "an asset it doesn't touch is"
            if len(lines) == 1
            else "assets it doesn't touch are"
        )
        + f" out of step with joinery.lock ({shown}). "
        + ELSEWHERE_FIX
    )
