"""Keep a change whose assets lack provenance, minus those assets (Rule Zero).

An asset with no valid ``joinery.lock`` record (a render a stalled worker cut
short, a file no recipe made) is never accepted. That used to discard the whole
change, so the rest of the agent's work survived only in its chat. Now, when no
one is left to fix it, the refused files are taken out of the stage (a file the
project had goes back to the project's version and its lock record; a new one is
removed with its ``.import`` sidecar) and the rest waits for review with the
blocked files listed. Nothing about the provenance gate is relaxed: the stage is
checked again afterwards and, unless it then passes, the change is still dropped.
"""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath

from joinery.catalog import load_catalog
from joinery.lockfile import check_assets, read_assets, write_assets


def _inside(root: Path, rel: str) -> Path | None:
    pure = PurePosixPath(rel)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        return None
    return root / rel


def block(
    stage: Path, original: Path, paths: list[str], project: Path | None = None
) -> list[str]:
    """Take ``paths`` out of the change staged in ``stage``.

    ``original`` holds the files as they were before the change. Returns the
    provenance problems the stage still has afterwards: empty means the rest of
    the change passes the gate and may wait for review. With ``project``, only
    problems with what the change still touches count, as at submit
    (``step_assets.gate``).
    """
    before = {entry.path: entry for entry in read_assets(original)}
    restored = []
    for rel in paths:
        staged, earlier = _inside(stage, rel), _inside(original, rel)
        if staged is None or earlier is None:
            continue
        for suffix in ("", ".import"):
            target = staged.with_name(staged.name + suffix)
            source = earlier.with_name(earlier.name + suffix)
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            elif target.is_file() or target.is_symlink():
                target.unlink()
        # Folders only the blocked file needed go too.
        folder = staged.parent
        while folder != stage and not (original / folder.relative_to(stage)).exists():
            try:
                folder.rmdir()
            except OSError:
                break
            folder = folder.parent
        if rel in before:
            restored.append(before[rel])
    write_assets(stage, set(paths), restored)
    if project is not None:
        from joinery import step_assets

        return step_assets.gate(project, stage)[0]
    return check_assets(stage, load_catalog())


def note(paths: list[str]) -> str:
    """The review's and the agent's line for the files left out."""
    return (
        "Blocked, not part of this change: "
        + ", ".join(paths)
        + " had no provenance record (no joinery.lock entry from a recipe), so "
        "Joinery left "
        + ("it" if len(paths) == 1 else "them")
        + " out; accepting keeps the rest. Redo exactly "
        + ("this render" if len(paths) == 1 else "these renders")
        + " with the tool that makes "
        + ("it" if len(paths) == 1 else "them")
        + ", so each gets its recipe and lock entry."
    )
