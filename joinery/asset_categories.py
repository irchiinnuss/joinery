"""The Assets view's own categories, kept in ``design/asset_categories.toml``.

The user groups assets into named categories ("Yard props") in the editor; the file
travels with the project through git. It organises the project and is not an asset, so
it has no lockfile entry. Read here so agents see the user's words for groups of assets.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PATH = Path("design") / "asset_categories.toml"


def load(project: Path) -> list[tuple[str, list[str]]]:
    """``(name, asset paths)`` in the user's order; paths are project-relative.

    Tolerant like the editor: a missing or broken file is no categories, a repeated
    name merges into the first, and an asset is kept only where it first appears.
    """
    try:
        data = tomllib.loads((project / PATH).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []
    tables = data.get("category", [])
    merged: dict[str, list[str]] = {}
    placed: set[str] = set()
    for table in tables if isinstance(tables, list) else []:
        if not isinstance(table, dict):
            continue
        name = " ".join(str(table.get("name", "")).split())
        assets = table.get("assets", [])
        if not name:
            continue
        into = merged.setdefault(name, [])
        for asset in assets if isinstance(assets, list) else []:
            if not isinstance(asset, str):
                continue
            path = asset.strip().removeprefix("res://")
            if path and path not in placed:
                placed.add(path)
                into.append(path)
    return [(name, sorted(paths)) for name, paths in merged.items()]
