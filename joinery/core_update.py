"""Bring a project's Joinery-owned game files up to date (``joinery update-core``).

A game keeps its own copies of the files Joinery owns: the game core
(``blueprints/_core/overlay``: ``core/replays/*``, menus, settings, the sound
nodes), each feature's overlay it has (``features/<id>/...``: the behaviour
agent, the camera rigs, online play) and the blockout level script
(``game/blockout/blockout_level.gd``).
After a Joinery update those copies are older than the ones it ships.

``plan`` compares each copy with the current version and with the version
Joinery installed: the game manifest records the core's and each feature's
files at install, and
``RECORD`` (``.joinery/core-files.json``) the other files Joinery writes once.
For a file installed before either record existed, the project's git history
gives the version first committed. A copy that still matches what was installed
is updated; one that differs was edited by the user or an agent and is kept
and listed, unless ``force``. A file with no known installed version counts
as edited.

``stage`` puts every update up as ONE change for review (accept or reject, as
with any change), and records the new installed hashes in both records, so the
next update knows them.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from joinery import blueprints
from joinery.blockout import build as blockout_build
from joinery.transactions import FileTransaction

#: Installed hashes of the Joinery-owned files the game manifest does not track.
RECORD = ".joinery/core-files.json"
#: The game core's unit in the game manifest.
CORE_UNIT = "_core"
#: The blockout level script: written once with the first blockout.
BLOCKOUT_SCRIPT = blockout_build.SCRIPT_PATH.removeprefix("res://")
#: The change's summary in the Review tab and the chat.
SUMMARY = "Update Joinery's core game files"
GIT_TIMEOUT = 20


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class CorePlan:
    """What updating would do, file by file (project paths)."""

    #: Unchanged since Joinery installed them; replaced by the current version.
    update: list[str] = field(default_factory=list)
    #: New in the current core; added.
    create: list[str] = field(default_factory=list)
    #: Edited since they were installed; kept unless forced.
    edited: list[str] = field(default_factory=list)
    #: Installed once and deleted since; left out.
    deleted: list[str] = field(default_factory=list)
    #: Already the current version.
    same: list[str] = field(default_factory=list)
    #: path -> the current version's bytes, for every file Joinery owns here.
    files: dict[str, bytes] = field(default_factory=dict)

    def writes(self, force: bool = False) -> list[str]:
        return [*self.update, *self.create, *(self.edited if force else [])]

    def as_json(self) -> dict[str, Any]:
        return {
            "update": self.update,
            "create": self.create,
            "edited": self.edited,
            "deleted": self.deleted,
            "same": len(self.same),
        }


def owners(project: Path) -> dict[str, str]:
    """Each file of the core's and the project's features' overlays -> its unit in the
    game manifest (``_core`` or the feature's id). The blueprint's own files are the
    game's to change, so they are not here."""
    found: dict[str, str] = {}
    record = blueprints.read_record(project)
    if record is None:
        return found
    resolution = blueprints.resolve(record.blueprint, record.features)
    for unit in blueprints._units(resolution):
        if unit.kind == "core":
            found.update(dict.fromkeys(unit.files, CORE_UNIT))
        elif unit.kind == "feature":
            found.update(dict.fromkeys(unit.files, unit.id))
    return found


def _sources(project: Path) -> dict[str, Path]:
    record = blueprints.read_record(project)
    if record is None:
        return {}
    resolution = blueprints.resolve(record.blueprint, record.features)
    sources: dict[str, Path] = {}
    for unit in blueprints._units(resolution):
        if unit.kind in {"core", "feature"}:
            sources.update(unit.files)
    return sources


def owned(project: Path) -> dict[str, bytes]:
    """The current version of every Joinery-owned file this project has."""
    files = {rel: path.read_bytes() for rel, path in _sources(project).items()}
    if (project / BLOCKOUT_SCRIPT).is_file():
        files[BLOCKOUT_SCRIPT] = blockout_build.SCRIPT_SOURCE.read_bytes()
    return files


def _manifest(project: Path) -> dict[str, Any]:
    return blueprints._read_manifest(project, project)


def _record(root: Path, project: Path) -> dict[str, str]:
    """RECORD's installed hashes, from ``root`` (a stage) else the project."""
    for base in (root, project):
        path = base / RECORD
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            files = data.get("files") if isinstance(data, dict) else None
            if isinstance(files, dict):
                return {str(k): str(v) for k, v in files.items()}
            return {}
    return {}


def remember(root: Path, project: Path, files: dict[str, bytes]) -> None:
    """Record ``files`` as installed by Joinery, in ``root`` (a stage, whose
    RECORD the accept carries into the project, or the project itself)."""
    known = _record(root, project)
    known.update({rel: _sha(data) for rel, data in files.items()})
    path = root / RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"files": dict(sorted(known.items()))}, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def _first_committed(project: Path, rel: str) -> str | None:
    """The hash of ``rel`` as first committed to the project's git, if it is."""
    try:
        added = subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "log",
                "--diff-filter=A",
                "--format=%H",
                "--",
                rel,
            ],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
        commits = added.stdout.split()
        if added.returncode != 0 or not commits:
            return None
        shown = subprocess.run(
            ["git", "-C", str(project), "show", f"{commits[-1]}:{rel}"],
            capture_output=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _sha(shown.stdout) if shown.returncode == 0 else None


def installed(project: Path) -> dict[str, str]:
    """The hash each Joinery-owned file had when Joinery installed it, where known."""
    units = _manifest(project).get("units", {})
    known: dict[str, str] = {}
    if isinstance(units, dict):
        for unit_id in {CORE_UNIT, *owners(project).values()}:
            unit = units.get(unit_id)
            files = unit.get("files") if isinstance(unit, dict) else None
            if isinstance(files, dict):
                known.update({str(k): str(v) for k, v in files.items()})
    known.update(_record(project, project))
    return known


def plan(project: Path) -> CorePlan:
    """What ``stage`` would change. Reads the project, writes nothing."""
    result = CorePlan(files=owned(project))
    known = installed(project)
    for rel, data in sorted(result.files.items()):
        path = project / rel
        was = known.get(rel)
        if not path.is_file():
            (result.deleted if was is not None else result.create).append(rel)
            continue
        current = _sha(path.read_bytes())
        if current == _sha(data):
            result.same.append(rel)
            continue
        if was is None:
            # Installed before Joinery kept hashes: the first commit is the
            # version it installed, when the project has its history.
            was = _first_committed(project, rel)
        (result.update if was == current else result.edited).append(rel)
    return result


def _write_records(stage: Path, project: Path, files: dict[str, bytes]) -> None:
    """The new installed hashes: the core's and the features' in the game manifest
    (under their unit), the rest in RECORD."""
    manifest = _manifest(project)
    units = manifest.get("units")
    unit_of = owners(project)
    rest = dict(files)
    changed = False
    for rel in list(rest):
        unit = units.get(unit_of.get(rel, "")) if isinstance(units, dict) else None
        tracked = unit.get("files") if isinstance(unit, dict) else None
        if rel != BLOCKOUT_SCRIPT and isinstance(tracked, dict):
            tracked[rel] = _sha(rest.pop(rel))
            changed = True
    if changed:
        target = stage / blueprints.MANIFEST
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(blueprints._manifest_text(manifest), encoding="utf-8")
    if rest:
        remember(stage, project, rest)


def pending(project: Path) -> FileTransaction | None:
    """A core update already waiting for review, if there is one."""
    for journal in sorted((project / ".joinery/transactions").glob("*/journal.json")):
        try:
            tx = FileTransaction.load(project, journal.parent.name)
        except (OSError, ValueError):
            continue
        if tx.summary == SUMMARY and tx.state in {"staging", "review"}:
            return tx
    return None


def stage(
    project: Path, *, force: bool = False, found: CorePlan | None = None
) -> FileTransaction | None:
    """Every update as one change waiting for review, or None when nothing would
    change. Edited files are kept (and stay listed in the plan) unless ``force``."""
    from joinery.game_setup import stage_change  # Late: game_setup is heavy.

    found = found or plan(project)
    writes = found.writes(force)
    if not writes:
        return None

    def edit(root: Path) -> None:
        for rel in writes:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(found.files[rel])
        # Every file now holds what Joinery installed, so record them all.
        current = {
            rel: data
            for rel, data in found.files.items()
            if rel in writes or rel in found.same
        }
        _write_records(root, project, current)

    return stage_change(project, SUMMARY, edit)


def offer(project: Path) -> dict[str, Any]:
    """After the add-on is refreshed: stage the core update for review when there
    is one and none waits already. Never raises; the outcome says what happened."""
    try:
        if pending(project) is not None:
            return {"state": "pending"}
        found = plan(project)
        tx = stage(project, found=found)
    except (OSError, ValueError, RuntimeError) as exc:
        return {"state": "failed", "message": str(exc) or type(exc).__name__}
    if tx is None:
        return {"state": "current", **found.as_json()}
    return {"state": "staged", "transaction_id": tx.id, **found.as_json()}


def _words(found: CorePlan, force: bool) -> list[str]:
    lines: list[str] = []
    if found.update:
        lines.append(f"Updates {len(found.update)} file(s): " + ", ".join(found.update))
    if found.create:
        lines.append(
            f"Adds {len(found.create)} new file(s): " + ", ".join(found.create)
        )
    if found.edited:
        verb = "Replaces" if force else "Keeps"
        why = "" if force else " (pass --force to replace them)"
        lines.append(
            f"{verb} {len(found.edited)} file(s) edited since Joinery installed them"
            f"{why}: " + ", ".join(found.edited)
        )
    if found.deleted:
        lines.append("Leaves out deleted file(s): " + ", ".join(found.deleted))
    return lines


def main(args: Any) -> int:
    """``joinery update-core --project P [--check] [--force] [--json]``."""
    project = Path(args.project).resolve()
    try:
        found = plan(project)
        tx = None if args.check else stage(project, force=args.force, found=found)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}) if args.json else str(exc))
        return 1
    data: dict[str, Any] = {**found.as_json(), "forced": bool(args.force)}
    lines = _words(found, args.force) or ["Joinery's core game files are current."]
    if tx is not None:
        data.update(transaction_id=tx.id, state=tx.state, summary=tx.summary)
        lines.append(
            "Waiting for your review. Accept with: joinery transactions accept "
            f"--project {project} --id {tx.id}"
        )
    data["words"] = lines
    print(json.dumps(data, indent=2) if args.json else "\n".join(lines))
    return 0
