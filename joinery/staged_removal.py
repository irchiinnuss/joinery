"""Take back an asset made earlier in the same change.

An agent that makes a take it then drops (a sound in the wrong tool, a first
draft) deletes the files it made. Their ``joinery.lock`` entries, Godot's
``.import`` sidecars and the recipes that made them stayed behind, so the change
failed its provenance check ("in joinery.lock but missing on disk") and the
only way through was to make the take again and ship it unused.

:func:`forget` clears what a deletion leaves, only for files and recipes this
change created: anything the project already has is a removal the user reviews
through the tidy tools, and stays as it is.
"""

from __future__ import annotations

from pathlib import Path

from joinery import lockfile
from joinery.recipe import load_recipe


def _relative(project: Path, stage: Path, value: str) -> str:
    """A deleted path as the project names it ("" when it is outside both)."""
    path = Path(value.removeprefix("res://"))
    if not path.is_absolute():
        return path.as_posix()
    for root in (stage, project):
        for base in (root, root.resolve()):
            if path.is_relative_to(base):
                return path.relative_to(base).as_posix()
    return ""


def _new(project: Path, rel: str) -> bool:
    return not (project / rel).exists()


def forget(project: Path, stage: Path, deleted: list[str]) -> list[str]:
    """Clear what deleting ``deleted`` (project paths, gone from ``stage``) left
    behind for assets this change made. Returns the paths it removed or dropped."""
    cleared: list[str] = []
    deleted = [_relative(project, stage, p) for p in deleted]
    gone = [
        rel
        for rel in deleted
        if rel and not (stage / rel).exists() and _new(project, rel)
    ]
    locked = {e.path for e in lockfile.read_assets(stage)}
    drop = {rel for rel in gone if rel in locked}
    for rel in gone:
        sidecar = stage / (rel + ".import")
        if sidecar.is_file():
            sidecar.unlink()
            cleared.append(rel + ".import")
    recipes = stage / "recipes"
    if recipes.is_dir():
        for toml in sorted(recipes.glob("*.toml")):
            rel = toml.relative_to(stage).as_posix()
            if not _new(project, rel):
                continue
            try:
                recipe = load_recipe(toml)
            except (OSError, ValueError):
                continue
            if any((stage / out).exists() for out in recipe.outputs):
                continue
            # Every output this new recipe made is gone: so is the recipe.
            drop |= {out for out in recipe.outputs if out in locked}
            for path in (toml, stage / recipe.script):
                if path.is_file() and _new(project, path.relative_to(stage).as_posix()):
                    path.unlink()
                    cleared.append(path.relative_to(stage).as_posix())
        for script in sorted(recipes.glob("*.*")):
            # A new recipe script whose recipe was deleted by hand.
            rel = script.relative_to(stage).as_posix()
            if script.suffix == ".toml" or not _new(project, rel):
                continue
            if not script.with_suffix(".toml").exists() and rel in _scripts_named(
                stage, deleted
            ):
                script.unlink()
                cleared.append(rel)
    if drop:
        lockfile.write_assets(stage, drop, [])
        cleared += sorted(f"joinery.lock: {rel}" for rel in drop)
    return cleared


def _scripts_named(stage: Path, deleted: list[str]) -> set[str]:
    """The scripts of recipes named in ``deleted`` (``recipes/<id>.toml``)."""
    found: set[str] = set()
    for rel in deleted:
        path = Path(rel)
        if path.parent.as_posix() == "recipes" and path.suffix == ".toml":
            for script in (stage / "recipes").glob(path.stem + ".*"):
                if script.suffix != ".toml":
                    found.add(script.relative_to(stage).as_posix())
    return found
