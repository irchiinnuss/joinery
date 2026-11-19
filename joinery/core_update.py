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

A rejected update is remembered by what it would change (:func:`fingerprint`: each
file's hash before and after) in ``DECLINED`` (``.joinery/core-declined.json``), and
from the rejected changes' journals, so an add-on refresh never offers the same one
again. A different update (Joinery ships new versions of those files, or the game's
copies change) is offered as usual. ``joinery update-core`` always stages it: that is
the way back to an update turned down.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from joinery import blueprints, source_folders
from joinery.blockout import build as blockout_build
from joinery.transactions import DIRECTORY, FileTransaction

#: Installed hashes of the Joinery-owned files the game manifest does not track.
RECORD = ".joinery/core-files.json"
#: Core updates the user rejected: {fingerprint: {files, declined}}.
DECLINED = ".joinery/core-declined.json"
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
    #: Source folders left without a .gdignore: {folder: game files loaded from it}.
    unmarked: dict[str, list[str]] = field(default_factory=dict)

    def writes(self, force: bool = False) -> list[str]:
        return [*self.update, *self.create, *(self.edited if force else [])]

    def as_json(self) -> dict[str, Any]:
        return {
            "update": self.update,
            "create": self.create,
            "edited": self.edited,
            "deleted": self.deleted,
            "same": len(self.same),
            "unmarked": self.unmarked,
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
    """The current version of every Joinery-owned file this project has, with the
    empty ``.gdignore`` of each source folder that lacks one (source_folders)."""
    files = {rel: path.read_bytes() for rel, path in _sources(project).items()}
    if (project / BLOCKOUT_SCRIPT).is_file():
        files[BLOCKOUT_SCRIPT] = blockout_build.SCRIPT_SOURCE.read_bytes()
    files.update(dict.fromkeys(source_folders.missing(project), b""))
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
    for folder in source_folders.SOURCE_ONLY:
        if (project / folder).is_dir() and not (
            project / folder / source_folders.MARKER
        ).exists():
            used = source_folders.game_uses(project, folder)
            if used:
                result.unmarked[folder] = used
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


def fingerprint(changes: list[tuple[str, str, str]]) -> str:
    """One update's identity: each file's path with its hash before and after ("" for
    a file that isn't there). The same files going the same way: the same update."""
    lines = sorted("\0".join(change) for change in changes)
    return _sha("\n".join(lines).encode("utf-8"))


def _planned(project: Path, found: CorePlan) -> list[tuple[str, str, str]]:
    """What staging ``found`` would change, as (path, before, after)."""
    changes: list[tuple[str, str, str]] = []
    for rel in found.writes():
        path = project / rel
        before = _sha(path.read_bytes()) if path.is_file() else ""
        changes.append((rel, before, _sha(found.files[rel])))
    return changes


def _journal_changes(tx: FileTransaction) -> list[tuple[str, str, str]]:
    return [
        (str(c["path"]), str(c.get("before", "")), str(c.get("after", "")))
        for c in tx.changes
        if DIRECTORY not in (c.get("before"), c.get("after"))
    ]


def declined(project: Path) -> dict[str, dict[str, Any]]:
    """Core updates the user rejected: {fingerprint: {files, declined}}, from
    ``DECLINED`` and from the journals of rejected core updates (kept in ``DECLINED``
    the first time they are read, so pruning old changes forgets none)."""
    path = project / DECLINED
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    known: dict[str, dict[str, Any]] = {
        str(k): dict(v)
        for k, v in (raw.get("declined", {}) if isinstance(raw, dict) else {}).items()
        if isinstance(v, dict)
    }
    found = dict(known)
    for journal in sorted((project / ".joinery/transactions").glob("*/journal.json")):
        try:
            tx = FileTransaction.load(project, journal.parent.name)
        except (OSError, ValueError):
            continue
        if tx.summary != SUMMARY or tx.state != "rejected":
            continue
        changes = _journal_changes(tx)
        if changes:
            found.setdefault(
                fingerprint(changes),
                {"files": sorted(c[0] for c in changes), "declined": tx.decided},
            )
    if found != known:
        _save_declined(project, found)
    return found


def _save_declined(project: Path, found: dict[str, dict[str, Any]]) -> None:
    path = project / DECLINED
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps({"declined": dict(sorted(found.items()))}, indent=2)
        path.write_text(text + "\n", encoding="utf-8")
    except OSError:
        pass  # The journals still say so.


def offer(project: Path) -> dict[str, Any]:
    """After the add-on is refreshed: stage the core update for review when there
    is one, none waits already, and the user didn't reject this same one before.
    Never raises; the outcome says what happened."""
    try:
        if pending(project) is not None:
            return {"state": "pending"}
        found = plan(project)
        changes = _planned(project, found)
        if changes and fingerprint(changes) in declined(project):
            # Turned down before, and nothing about it changed since.
            return {"state": "declined", **found.as_json()}
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
    for folder, used in sorted(found.unmarked.items()):
        lines.append(
            f"Leaves {folder}/ without a .gdignore: the game loads "
            + ", ".join(used[:3])
            + (f" and {len(used) - 3} more" if len(used) > 3 else "")
            + " from it. Move those files out of the source folder, then run "
            "update-core again."
        )
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
    if args.check and found.writes():
        turned_down = fingerprint(_planned(project, found)) in declined(project)
        data["declined"] = turned_down
        if turned_down:
            lines.append(
                "You turned this update down before, so an add-on update doesn't "
                "offer it again. Run update-core without --check to review it."
            )
    if tx is not None:
        data.update(transaction_id=tx.id, state=tx.state, summary=tx.summary)
        lines.append(
            "Waiting for your review. Accept with: joinery transactions accept "
            f"--project {project} --id {tx.id}"
        )
    data["words"] = lines
    print(json.dumps(data, indent=2) if args.json else "\n".join(lines))
    return 0
