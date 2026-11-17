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
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from joinery.catalog import load_catalog
from joinery.lockfile import LOCK_NAME, asset_files, check_assets


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
