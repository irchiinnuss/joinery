"""File transactions: isolated staging, conflict detection and crash recovery.

Storage model
-------------
* ``stage`` is a full working copy of the project for the tools to edit. It is
  made with copy-on-write clones where the filesystem supports them (APFS,
  Btrfs, XFS with reflink), so it costs almost nothing until a tool writes, and
  it is deleted as soon as the user decides.
* ``original`` holds only the files this transaction replaces or deletes. They
  are captured from the project when the change goes to review and checked
  again before the first write on accept (copy-on-first-write), never as a
  copy of the whole project.
* The journal keeps every change with its before and after hash, so a revert
  works from ``original`` plus the journal alone.
* Restore data is kept for recent accepted transactions only (see
  :class:`HistoryPolicy`); older journals stay, marked ``revertible: false``.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import tomllib
import uuid
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path
from typing import Any, BinaryIO, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from joinery.platform_support import lock_file, unlock_file
from joinery.source_folders import hide_state

EXCLUDED = {".git", ".godot", ".joinery", "__pycache__", ".pytest_cache", ".venv"}
DIRECTORY = "directory"
VERSION = 2
PENDING = frozenset({"new", "staging", "review", "committing"})
TRANSACTIONS = ".joinery/transactions"
NOT_REVERTIBLE = (
    "This change can no longer be reverted. Its restore data was cleared to save space."
)
#: Joinery's own records that a staged change may update. `.joinery` stays out
#: of the stage and the review, but adding or removing a game feature in the
#: stage rewrites the game manifest there. Accept carries it into the project,
#: reject drops it with the stage, and revert puts the earlier one back.
CARRIED = (".joinery/game-manifest.json", ".joinery/core-files.json")
#: Where a transaction keeps the CARRIED records as they were when it began, so
#: a record changed by another accepted change meanwhile is merged, not replaced.
CARRIED_BASE = "carried-base"
#: Marks a change kept waiting for review across sessions (FileTransaction.hold).
HELD = "held.json"
#: The files of a reverted change as it was accepted, kept so the revert can be
#: undone (FileTransaction.unrevert). Older reverts read them from the Timeline.
REDO = "redo"
#: The asset lock, merged entry by entry when a revert is undone (rebase.merge_lock).
LOCK = "joinery.lock"
NOT_REVERTED = "Only a reverted change can be put back."


class RevertConflict(ValueError):
    """Files a reverted change would put back were edited since the revert."""

    def __init__(self, paths: list[str]) -> None:
        self.paths = list(paths)
        shown = ", ".join(self.paths[:3])
        if len(self.paths) > 3:
            shown += f" and {len(self.paths) - 3} more"
        one = len(self.paths) == 1
        super().__init__(
            f"{shown} {'was' if one else 'were'} changed since the revert. Putting the "
            f"change back would replace {'it' if one else 'them'}. Keep your files "
            "(nothing changes), or replace them: your versions are saved, so Revert "
            "brings them back."
        )


class ProjectLease:
    """One authoring host owns recovery/review for a project at a time.

    Reconnecting closes the previous host's pipes and starts a new host at once;
    the old one lets go of the lock a moment later, once it sees the pipes close.
    So the lease waits up to ``wait`` seconds before deciding another editor has it.
    """

    def __init__(self, project: Path, wait: float = 10.0) -> None:
        path = project / ".joinery/session.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file: BinaryIO = path.open("a+b")
        deadline = time.monotonic() + wait
        while True:
            try:
                lock_file(self.file)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self.file.close()
                    raise ValueError(
                        "This project already has an active Joinery chat connection. Close it before reconnecting from another editor."
                    ) from None
                time.sleep(0.2)

    def close(self) -> None:
        if not self.file.closed:
            unlock_file(self.file)
            self.file.close()


def _check_links(root: Path) -> None:
    for directory, children, files in os.walk(root, followlinks=False):
        children[:] = [name for name in children if name not in EXCLUDED]
        parent = Path(directory)
        for name in [*children, *files]:
            if (parent / name).is_symlink():
                raise ValueError("Project transactions do not follow symbolic links.")


def inventory(root: Path, *, include_dirs: bool = False) -> dict[str, str]:
    found = {}
    for directory, children, files in os.walk(root, followlinks=False):
        children[:] = [name for name in children if name not in EXCLUDED]
        parent = Path(directory)
        if include_dirs and parent != root:
            found[parent.relative_to(root).as_posix()] = DIRECTORY
        for name in [*children, *files]:
            if (parent / name).is_symlink():
                raise ValueError("Project transactions do not follow symbolic links.")
        for name in files:
            path = parent / name
            found[path.relative_to(root).as_posix()] = _sha256(path)
    return found


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- Copy-on-write clones ---------------------------------------------------

_FICLONE = 0x40049409  # Linux ioctl: share extents between two files.
_CLONE_NOFOLLOW = 0x0001  # macOS clonefile(2) flag.
_NO_CLONE = {
    errno.ENOTSUP,
    errno.EOPNOTSUPP,
    errno.EXDEV,
    errno.EINVAL,
    errno.ENOTTY,
    errno.ENOSYS,
    errno.EPERM,
}
#: (source device, target device) pairs where cloning was tried and refused.
_unclonable: set[tuple[int, int]] = set()


@cache
def _clonefile() -> Callable[[bytes, bytes, int], int] | None:
    if sys.platform != "darwin":
        return None
    try:
        function = ctypes.CDLL(None, use_errno=True).clonefile
    except (AttributeError, OSError):
        return None
    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    function.restype = ctypes.c_int
    result: Callable[[bytes, bytes, int], int] = function
    return result


def clone_file(source: Path, target: Path) -> bool:
    """Make ``target`` a copy-on-write clone of ``source``.

    Returns False, leaving no ``target`` behind, when the platform or filesystem
    cannot clone; the caller then copies. ``target`` must not exist.
    """
    if os.environ.get("JOINERY_NO_CLONE"):
        return False
    key = (source.stat().st_dev, target.parent.stat().st_dev)
    if key in _unclonable:
        return False
    clonefile = _clonefile()
    if clonefile is not None:
        if clonefile(os.fsencode(source), os.fsencode(target), _CLONE_NOFOLLOW) == 0:
            return True
        code = ctypes.get_errno()
        if code not in _NO_CLONE:
            raise OSError(code, os.strerror(code), str(target))
        _unclonable.add(key)
        return False
    if sys.platform.startswith("linux"):
        import fcntl

        try:
            with source.open("rb") as reader, target.open("xb") as writer:
                fcntl.ioctl(writer.fileno(), _FICLONE, reader.fileno())
            shutil.copystat(source, target)
            return True
        except OSError as exc:
            target.unlink(missing_ok=True)
            if exc.errno not in _NO_CLONE:
                raise
    _unclonable.add(key)
    return False


def copy_file(source: Path, target: Path) -> None:
    """Clone one file where the filesystem allows it, otherwise copy it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.unlink()
    if not clone_file(source, target):
        shutil.copy2(source, target)


def place_file(source: Path, target: Path) -> None:
    """Put a copy of ``source`` at the project's ``target`` in one step, dated now.

    A copy keeps the source's time, and git on Windows (no inode or change time to
    compare) takes a file with the time and size its index holds as unchanged: a
    same-size picture put back from an older copy would never be committed.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(prefix=".joinery-", dir=target.parent)
    os.close(descriptor)
    try:
        copy_file(source, Path(temp))
        os.utime(temp)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _copy_entry(source: str, target: str) -> str:
    copy_file(Path(source), Path(target))
    return target


def clone_tree(source: Path, target: Path) -> None:
    """Copy a project tree (minus Joinery's own state) using clones where possible."""
    # Validate before copying so a symlink cannot import files outside the project.
    _check_links(source)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(*EXCLUDED),
        copy_function=_copy_entry,
        dirs_exist_ok=True,
    )


#: Godot's import cache: models, textures and sounds as Godot loads them.
IMPORT_CACHE = ".godot"


def seed_import_cache(source: Path, target: Path) -> bool:
    """Give ``target`` (a copy of the project ``source``) the project's import cache,
    so Godot's import there redoes only what differs instead of every asset. Files
    keep their times, so a changed asset is still imported again. The cache is
    never part of a change (EXCLUDED); a copy that fails leaves the target to
    import from scratch. True when it copied."""
    cache = source / IMPORT_CACHE
    if not cache.is_dir() or cache.is_symlink() or (target / IMPORT_CACHE).exists():
        return False
    try:
        shutil.copytree(
            cache, target / IMPORT_CACHE, symlinks=True, copy_function=_copy_entry
        )
    except (OSError, shutil.Error):
        shutil.rmtree(target / IMPORT_CACHE, ignore_errors=True)
        return False
    return True


def tree_bytes(root: Path) -> int:
    """Apparent size of every regular file below ``root``."""
    total = 0
    for directory, _children, files in os.walk(root, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                continue
    return total


def _size(path: Path) -> int:
    try:
        return os.lstat(path).st_size
    except OSError:
        return 0


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as file:
            json.dump(data, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _safe(path: str) -> None:
    parts = Path(path).parts
    if Path(path).is_absolute() or ".." in parts or not parts:
        raise ValueError("Unsafe journal path.")


def _apply_order(change: dict[str, str]) -> tuple[int, int]:
    """Folders made first (outermost first), files next, folders removed last
    (innermost first)."""
    depth = len(Path(change["path"]).parts)
    if change["after"] == DIRECTORY:
        return 0, depth
    if change["before"] == DIRECTORY:
        return 2, -depth
    return 1, depth


class FileTransaction:
    """No writes to working sources until explicit accept. No git reset needed."""

    def __init__(self, project: Path, identifier: str | None = None) -> None:
        self.project = project.resolve()
        if not (self.project / "project.godot").is_file():
            raise ValueError("Expected a Godot project.")
        self.id = identifier or uuid.uuid4().hex
        if len(self.id) != 32 or any(ch not in "0123456789abcdef" for ch in self.id):
            raise ValueError("Invalid transaction identity.")
        self.directory = self.project / TRANSACTIONS / self.id
        #: Files this transaction replaces or deletes, as they were before it.
        self.snapshot = self.directory / "original"
        self.stage = self.directory / "stage"
        #: The change's files as accepted, while it is reverted (REDO).
        self.redo = self.directory / REDO
        self.journal = self.directory / "journal.json"
        self.state = "new"
        self.base: dict[str, str] = {}
        self.changes: list[dict[str, str]] = []
        #: CARRIED files this transaction replaced, with before and after hashes.
        self.carried: list[dict[str, str]] = []
        #: The change's name: the agent's one line, or a title from its files.
        self.summary = ""
        #: The user's request the change answers, kept as the timeline entry's detail.
        self.request = ""
        self.include_dirs = True
        self.revertible = True
        self.created = 0.0
        self.decided = 0.0
        #: The apps that made this change ("blender", "joinery"), in first-use order.
        self.tools: list[str] = []
        #: What adding the last decision to the git timeline did (timeline.record).
        self.timeline: dict[str, str] = {}
        #: The reviewer agent's notes ([{severity, text, file?}]) and how its review went.
        self.review_notes: list[dict[str, str]] = []
        self.reviewer: dict[str, Any] = {}
        #: Kept without the user's Accept, by the project's choice (joinery.auto_accept).
        self.auto_accepted = False
        #: What a kept change says beside "Accepted automatically" (auto_accept.shown_notes).
        self.auto_notes: list[str] = []
        #: What the agent assumed that the user didn't say, and where it departed from
        #: the request and why (submit_for_review's guessed and not_as_asked).
        self.guessed: list[str] = []
        self.not_as_asked: list[str] = []
        #: The chat whose request made it, so a follow-up in that chat builds on it.
        self.chat_id = ""
        #: Kept for review before Joinery's checks ran on it (a run that stopped).
        self.unchecked = False
        #: Each decision in order: [{state, at, by?, from?, commit?}], so a change
        #: reverted and put back again says so.
        self.history: list[dict[str, Any]] = []
        #: The paired device the next decision comes from ("Ana's iPhone"), "" for the
        #: editor or the CLI (joinery.remote). Its Timeline entry says so.
        self.decided_from = ""

    @property
    def original(self) -> Path:
        """Where to read a changed file as it was before this transaction.

        Nothing is written to the project before accept, so while the tools are
        still working the project itself holds the earlier files. From review on
        the snapshot holds every file the transaction replaces or deletes. Read
        only: never write through this path.
        """
        return self.project if self.state in {"new", "staging"} else self.snapshot

    def begin(self, summary: str) -> None:
        if self.directory.exists():
            raise ValueError("Transaction already exists.")
        # The stage and the backups are copies of the game's scripts: Godot's
        # language server must not take them for the project's own classes.
        self.directory.mkdir(parents=True, mode=0o700)
        hide_state(self.project)
        try:
            clone_tree(self.project, self.stage)
            # The first import of the stage then redoes only what the change made.
            seed_import_cache(self.project, self.stage)
            for path in CARRIED:
                if (self.project / path).is_file():
                    copy_file(self.project / path, self.directory / CARRIED_BASE / path)
            # The stage is the starting point: an edit to the project while the
            # tools work is caught on accept instead of reviewed as theirs.
            self.base = inventory(self.stage, include_dirs=self.include_dirs)
            self.summary = summary
            self.created = time.time()
            self.state = "staging"
            self._record()
        except BaseException:
            shutil.rmtree(self.directory)
            raise

    def note_tool(self, tool: str) -> None:
        """Remember that ``tool`` worked on this change; recorded with the journal."""
        if tool and tool not in self.tools:
            self.tools.append(tool)

    def _record(self, **extra: Any) -> None:
        if "changes" in extra:
            self.changes = list(extra.pop("changes"))
        data: dict[str, Any] = {
            "version": VERSION,
            "id": self.id,
            "state": self.state,
            "summary": self.summary,
            "include_dirs": self.include_dirs,
            **({"request": self.request} if self.request else {}),
            "created": self.created,
        }
        if self.tools:
            data["tools"] = self.tools
        if self.review_notes:
            data["review_notes"] = self.review_notes
        if self.reviewer:
            data["reviewer"] = self.reviewer
        if self.auto_accepted:
            data["auto_accepted"] = True
        if self.auto_notes:
            data["auto_notes"] = self.auto_notes
        if self.guessed:
            data["guessed"] = self.guessed
        if self.not_as_asked:
            data["not_as_asked"] = self.not_as_asked
        if self.chat_id:
            data["chat_id"] = self.chat_id
        if self.unchecked:
            data["unchecked"] = True
        if self.state in PENDING:
            # The full inventory is only needed until the decision.
            data["base"] = self.base
        if self.changes:
            data["changes"] = self.changes
        if self.carried:
            data["carried"] = self.carried
        if self.decided:
            data["decided"] = self.decided
        if self.history:
            data["history"] = self.history
        if self.state == "accepted":
            data["revertible"] = self.revertible
        atomic_json(self.journal, {**data, **extra})

    def diff(self) -> list[dict[str, str]]:
        after = inventory(self.stage, include_dirs=self.include_dirs)
        return [
            {
                "path": path,
                "before": self.base.get(path, ""),
                "after": after.get(path, ""),
                "change": "created"
                if path not in self.base
                else "deleted"
                if path not in after
                else "modified",
            }
            for path in sorted(self.base.keys() | after.keys())
            if self.base.get(path) != after.get(path)
        ]

    def _capture(self, changes: list[dict[str, str]]) -> list[str]:
        """Copy each file about to be replaced or deleted into the snapshot.

        Only a project file that still matches the reviewed ``before`` hash is
        captured. Returns the paths that could not be captured.
        """
        missing = []
        for change in changes:
            before = change["before"]
            if not before or before == DIRECTORY:
                continue
            target = self.snapshot / change["path"]
            if target.is_file() and _sha256(target) == before:
                continue
            source = self.project / change["path"]
            if source.is_file() and not source.is_symlink():
                copy_file(source, target)
                if _sha256(target) == before:
                    continue
                target.unlink()
            missing.append(change["path"])
        return missing

    def review(self) -> list[dict[str, str]]:
        if self.state != "staging":
            raise ValueError("Transaction is not staging.")
        changes = self.diff()
        if not changes:
            raise ValueError("The tool produced no project changes.")
        # A file edited by hand since the snapshot is refused on accept anyway.
        self._capture(changes)
        self.state = "review"
        self._record(changes=changes)
        return changes

    def hold(self, by: str) -> None:
        """Keep this change waiting for the user's review after the session that made
        it ends: a headless run (``joinery run``) or ``joinery memory add``. A chat
        host rejects any other change still pending when it closes or starts."""
        if self.state != "review":
            raise ValueError("Only a change waiting for review can be kept for it.")
        atomic_json(self.directory / HELD, {"by": by, "at": time.time()})

    @property
    def held(self) -> bool:
        return (self.directory / HELD).is_file()

    def mark_checked(self) -> None:
        """Its scripts compiled after all (``submission.compile_check``)."""
        if self.unchecked:
            self.unchecked = False
            self._record()

    def reopen(self) -> None:
        """Take a change waiting for review back to staging, so the chat that made
        it carries on in it instead of beginning a second change for the same
        request. Its review (notes, the reviewer's read) is over: the next submit
        checks and reviews it again."""
        if self.state != "review":
            raise ValueError("Only a change waiting for review can be reopened.")
        (self.directory / HELD).unlink(missing_ok=True)
        self.state = "staging"
        self.review_notes, self.reviewer, self.auto_notes = [], {}, []
        self.unchecked = False
        self._record()

    def accept(self) -> list[dict[str, str]]:
        if self.state != "review":
            raise ValueError("Review the transaction before accepting it.")
        changes = self.diff()
        reviewed = json.loads(self.journal.read_text()).get("changes")
        if changes != reviewed:
            raise ValueError(
                "Staged files changed after review. Review the updated transaction before accepting."
            )
        current = inventory(self.project, include_dirs=self.include_dirs)
        for change in changes:
            if (
                change["before"]
                and change["after"]
                and (DIRECTORY in {change["before"], change["after"]})
            ):
                raise ValueError(
                    "Replace files and directories in separate reviewed transactions."
                )
            if current.get(change["path"], "") != change["before"]:
                raise ValueError(
                    f"{change['path']} changed after the snapshot. The working file was preserved."
                )
        # Every file to be replaced is in the snapshot before the first write.
        missing = self._capture(changes)
        if missing:
            raise ValueError(
                f"{missing[0]} changed after the snapshot. The working file was preserved."
            )
        # A durable intent journal precedes the first working-tree mutation.
        self.state = "committing"
        self._record(changes=changes)
        applied = []
        try:
            for change in sorted(changes, key=_apply_order):
                path = change["path"]
                target = self.project / path
                if change["after"] == DIRECTORY:
                    target.mkdir(parents=True, exist_ok=True)
                elif change["before"] == DIRECTORY:
                    target.rmdir()
                elif change["after"]:
                    place_file(self.stage / path, target)
                else:
                    target.unlink()
                applied.append(change)
            self._carry()
            self.state = "accepted"
            self.decided = time.time()
            self.history.append(
                {"state": "accepted", "at": self.decided, **self._from()}
            )
            self._record(changes=changes)
        except BaseException:
            self._uncarry()
            self._restore(applied)
            self.state = "failed"
            self._record(changes=changes)
            self._discard(self.stage, self.snapshot)
            raise
        self._discard(self.stage)
        self._add_to_timeline(changes, reverted=False)
        self._note_commit()
        self._stamp_unplayed(changes)
        self._index_recipes()
        self._housekeep()
        return changes

    def _index_recipes(self) -> None:
        """Index the project's recipes with this change's summary (joinery.skills)
        before retention can remove its journal. Bookkeeping: never fails a decision."""
        from joinery import skills  # Late: skills reads recipes, not transactions.

        try:
            skills.refresh(self.project)
        except (OSError, ValueError):
            pass

    def _stamp_unplayed(self, changes: list[dict[str, str]]) -> None:
        """Stamp a change to the game "not yet played by you" (joinery.played).
        Bookkeeping: never fails a decision already made."""
        from joinery import played  # Late: played writes through this module.

        try:
            played.stamp(
                self.project,
                self.id,
                self.summary,
                [
                    change["path"]
                    for change in changes
                    if DIRECTORY not in {change["before"], change["after"]}
                ],
                commit=self.timeline.get("commit", ""),
                when=self.decided,
            )
        except (OSError, ValueError):
            pass

    def _add_to_timeline(
        self, changes: list[dict[str, str]], reverted: bool, restored: bool = False
    ) -> None:
        """Commit exactly this change's files to the project's git timeline.

        Bookkeeping like retention: a timeline that can't be written never fails
        a decision already made. The outcome is kept in ``self.timeline``.
        """
        from joinery import timeline  # Late: timeline stages restores through here.

        self.timeline = timeline.record(
            self.project,
            self._timeline_paths(changes),
            self.summary,
            reverted,
            request=self.request,
            restored=restored,
            decided_from="" if reverted or restored else self.decided_from,
        )

    def _from(self) -> dict[str, str]:
        """The decision's device, for its history entry ({} from the editor)."""
        return {"from": self.decided_from} if self.decided_from else {}

    def _note_commit(self) -> None:
        """Keep the timeline entry the last decision made with that decision, so the
        Timeline can offer Undo revert on the right entry. Never fails a decision."""
        commit = self.timeline.get("commit", "")
        if not commit or not self.history:
            return
        self.history[-1]["commit"] = commit
        try:
            self._record()
        except OSError:
            pass

    def _timeline_paths(self, changes: list[dict[str, str]]) -> list[str]:
        return [
            change["path"]
            for change in [*changes, *self.carried]
            if DIRECTORY not in {change["before"], change["after"]}
        ]

    def commit_generated(self) -> None:
        """Once Godot has imported an accepted change, commit the ``.uid`` and
        ``.import`` files it wrote beside the change's files, so nothing the change
        brought is left out of the timeline. Bookkeeping: never fails the decision."""
        from joinery import timeline

        if self.state != "accepted" or self.timeline.get("state") not in {
            "committed",
            "unchanged",
        }:
            return
        outcome = timeline.add_generated(
            self.project,
            self._timeline_paths(self.changes),
            self.timeline.get("commit", ""),
            timeline.message_for(
                self.summary,
                request=self.request,
                decided_from=str(self.history[-1].get("from", ""))
                if self.history
                else "",
            ),
        )
        if outcome.get("state") == "committed":
            self.timeline = outcome
            from joinery import played

            played.recommit(self.project, self.id, outcome.get("commit", ""))
        elif outcome:
            self.timeline = {**self.timeline, **outcome}

    def _carry(self) -> None:
        """Copy the stage's CARRIED records into the project, keeping the old ones.

        The plan is journalled before the first copy, so recovery after a crash
        knows which records to put back.
        """
        planned: list[dict[str, str]] = []
        for path in CARRIED:
            staged = self.stage / path
            if not staged.is_file() or staged.is_symlink():
                continue
            target = self.project / path
            before = _sha256(target) if target.is_file() else ""
            self._merge_carried(path, before)
            after = _sha256(staged)
            if before == after:
                continue
            if before:
                copy_file(target, self.snapshot / path)
            planned.append({"path": path, "before": before, "after": after})
        if not planned:
            return
        self.carried = planned
        self._record(changes=self.changes)
        for entry in planned:
            place_file(self.stage / entry["path"], self.project / entry["path"])

    def _merge_carried(self, path: str, now: str) -> None:
        """Another change rewrote the project's ``path`` since this one began: put
        this change's own edits on top of it in the stage, entry by entry."""
        base = self.directory / CARRIED_BASE / path
        if not now or (base.is_file() and _sha256(base) == now):
            return
        from joinery.rebase import merge_manifest  # Late: rebase imports this module.

        try:
            merged = merge_manifest(
                base.read_text(encoding="utf-8") if base.is_file() else "{}",
                (self.project / path).read_text(encoding="utf-8"),
                (self.stage / path).read_text(encoding="utf-8"),
            )
        except (OSError, ValueError):
            return
        (self.stage / path).write_text(merged, encoding="utf-8")

    def _uncarry(self) -> None:
        """Put back each CARRIED record this transaction replaced."""
        for entry in self.carried:
            target = self.project / entry["path"]
            if not target.is_file() or _sha256(target) != entry["after"]:
                continue
            saved = self.snapshot / entry["path"]
            if entry["before"] and saved.is_file():
                place_file(saved, target)
            elif not entry["before"]:
                target.unlink()

    def _carried_conflicts(self) -> list[str]:
        return [
            entry["path"]
            for entry in self.carried
            if (self.project / entry["path"]).is_file()
            and _sha256(self.project / entry["path"]) != entry["after"]
        ]

    def _restore(self, changes: list[dict[str, str]]) -> None:
        removing = {change["path"] for change in changes if not change["before"]}
        # Refuse before restoring any file if a newly created folder now contains
        # unrelated work. A late rmdir failure would otherwise leave a partial revert.
        current = inventory(self.project, include_dirs=self.include_dirs)
        for change in changes:
            if (
                change["after"] == DIRECTORY
                and not change["before"]
                and any(
                    path.startswith(change["path"] + "/") and path not in removing
                    for path in current
                )
            ):
                raise ValueError(
                    "Cannot restore a folder containing newer manual edits."
                )
        ordered = sorted(
            changes,
            key=lambda change: (
                0
                if change["before"] == DIRECTORY
                else 2
                if change["after"] == DIRECTORY
                else 1,
                -len(Path(change["path"]).parts)
                if change["after"] == DIRECTORY
                else len(Path(change["path"]).parts),
            ),
        )
        for change in ordered:
            target = self.project / change["path"]
            if change["before"] == DIRECTORY:
                target.mkdir(parents=True, exist_ok=True)
            elif change["after"] == DIRECTORY:
                if target.is_dir():
                    target.rmdir()
            elif change["before"]:
                place_file(self.snapshot / change["path"], target)
            elif target.exists():
                target.unlink()

    def _check_snapshot(self, changes: list[dict[str, str]]) -> None:
        for change in changes:
            before = change["before"]
            if not before or before == DIRECTORY:
                continue
            path = self.snapshot / change["path"]
            if not path.is_file() or _sha256(path) != before:
                raise ValueError(
                    f"The saved copy of {change['path']} is missing or damaged. Nothing was changed."
                )

    def reject(self) -> None:
        if self.state not in {"staging", "review"}:
            raise ValueError("This transaction is not pending.")
        self.state = "rejected"
        self.decided = time.time()
        if self.decided_from:
            self.history.append(
                {"state": "rejected", "at": self.decided, **self._from()}
            )
        self._record()
        self._discard(self.stage, self.snapshot)
        self._housekeep()

    def revert(self) -> None:
        if self.state != "accepted":
            raise ValueError("Only an accepted transaction can be reverted.")
        # Retention and compaction must not prune the restore data mid-revert, and
        # must not rewrite this journal from a stale copy afterwards.
        with _wait_for_history(self.project):
            # Another process may have pruned or reverted it since this was loaded.
            raw = json.loads(self.journal.read_text())
            if raw.get("state") != "accepted":
                raise ValueError("Only an accepted transaction can be reverted.")
            if raw.get("revertible") is False:
                self.revertible = False
            if not self.revertible:
                raise ValueError(NOT_REVERTIBLE)
            changes = self.changes or self.diff()
            current = inventory(self.project, include_dirs=self.include_dirs)
            for change in changes:
                if current.get(change["path"], "") != change["after"]:
                    raise ValueError("Cannot revert over newer manual edits.")
            if self._carried_conflicts():
                raise ValueError(
                    "A later change updated this project's game features. Revert that one first."
                )
            self._check_snapshot([*changes, *self.carried])
            # The accepted files are kept first, so the revert can be undone.
            self._discard(self.redo)
            self._keep_redo([*changes, *self.carried])
            self._restore(changes)
            self._uncarry()
            if not self.history:  # A journal from before decisions were listed.
                self.history.append({"state": "accepted", "at": self.decided})
            self.state = "reverted"
            self.decided = time.time()
            self.history.append({"state": "reverted", "at": self.decided})
            self._record(changes=changes)
            self._discard(self.stage, self.snapshot)
        self._add_to_timeline(changes, reverted=True)
        self._note_commit()
        from joinery import played

        played.forget(self.project, self.id)

    def _keep_redo(self, entries: list[dict[str, str]]) -> None:
        """Copy each file the change wrote, still as it wrote it, into REDO."""
        for entry in entries:
            after = entry["after"]
            if not after or after == DIRECTORY:
                continue
            source = self.project / entry["path"]
            if (
                source.is_file()
                and not source.is_symlink()
                and _sha256(source) == after
            ):
                copy_file(source, self.redo / entry["path"])

    def _now(self, path: str) -> str:
        """What ``path`` holds in the project now: its hash, DIRECTORY or ""."""
        target = self.project / path
        if target.is_symlink():
            raise ValueError("Project transactions do not follow symbolic links.")
        if target.is_dir():
            return DIRECTORY
        return _sha256(target) if target.is_file() else ""

    def _from_timeline(self, path: str, digest: str, target: Path) -> bool:
        """Write ``path`` as the Timeline kept it with hash ``digest`` to ``target``."""
        from joinery import timeline  # Late: timeline stages restores through here.

        if not digest or digest == DIRECTORY:
            return False
        data = timeline.find_blob(self.project, path, digest)
        if data is None:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return True

    def _has_redo(self, entry: dict[str, str]) -> bool:
        """REDO holds ``entry``'s file as the change wrote it, or can from the Timeline."""
        after = entry["after"]
        if not after or after == DIRECTORY:
            return True
        kept = self.redo / entry["path"]
        if kept.is_file() and _sha256(kept) == after:
            return True
        return self._from_timeline(entry["path"], after, kept)

    def _merge_lock(self, entries: list[dict[str, str]], now: str) -> str:
        """This change's joinery.lock entries put into the project's lock as it is
        now, or "" when the locks can't be read or the accepted one is gone."""
        from joinery.rebase import merge_lock  # Late: rebase imports this module.

        entry = next(e for e in entries if e["path"] == LOCK)
        if not now or now == DIRECTORY or not self._has_redo(entry):
            return ""
        try:
            return merge_lock(
                (self.project / LOCK).read_text(encoding="utf-8"),
                (self.redo / LOCK).read_text(encoding="utf-8"),
                {e["path"] for e in entries},
            )
        except (OSError, ValueError, UnicodeDecodeError):
            return ""

    def unrevert(self, overwrite: bool = False) -> list[dict[str, str]]:
        """Put a reverted change back as it was accepted (Undo revert).

        The accepted files come from REDO, kept by the revert, or for an older
        revert from the Timeline. A file edited since the revert is never replaced
        silently: :class:`RevertConflict` names them, and ``overwrite`` replaces
        them after saving the edited copies, which then become what a later Revert
        puts back. Like accept, the files about to be replaced are saved before the
        first write, the intent is journalled, and a failure puts everything back.
        Returns the change's files as now recorded.
        """
        if self.state != "reverted":
            raise ValueError(NOT_REVERTED)
        with _wait_for_history(self.project):
            raw = json.loads(self.journal.read_text())
            if raw.get("state") != "reverted":
                raise ValueError(NOT_REVERTED)
            if not self.changes:
                raise ValueError(
                    "This change's record lists no files, so it can't be put back."
                )
            entries = [*self.changes, *self.carried]
            now = {entry["path"]: self._now(entry["path"]) for entry in entries}
            edited = sorted(
                entry["path"]
                for entry in entries
                if now[entry["path"]] not in {entry["before"], entry["after"]}
            )
            # Every change that makes an asset adds to joinery.lock: this change's own
            # entries go back into the lock as it is now, entry by entry, as accept
            # does (rebase.merge_lock), instead of replacing later changes' entries.
            moved = set(edited)
            afters: dict[str, str] = {}
            merged = self._merge_lock(entries, now[LOCK]) if LOCK in edited else ""
            if merged:
                edited.remove(LOCK)
                afters[LOCK] = hashlib.sha256(merged.encode("utf-8")).hexdigest()
            if edited and not overwrite:
                raise RevertConflict(edited)
            for path in edited:
                entry = next(e for e in entries if e["path"] == path)
                if (
                    DIRECTORY in {now[path], entry["after"]}
                    and now[path]
                    and entry["after"]
                ):
                    raise ValueError(
                        f"{path} is now a folder where the change had a file, or the "
                        "other way round. Nothing was changed."
                    )

            def rebased(entry: dict[str, str]) -> dict[str, str]:
                path = entry["path"]
                after = afters.get(path, entry["after"])
                before = now[path] if path in moved else entry["before"]
                kind = (
                    "created" if not before else "deleted" if not after else "modified"
                )
                return {**entry, "before": before, "after": after, "change": kind}

            changes = [rebased(entry) for entry in self.changes]
            carried = [
                {"path": e["path"], "before": e["before"], "after": e["after"]}
                for e in map(rebased, self.carried)
            ]
            todo = [e for e in [*changes, *carried] if now[e["path"]] != e["after"]]
            # Written from here instead of REDO: the merged lock.
            sources: dict[str, Path] = {}
            if merged:
                sources[LOCK] = self.directory / "merged-lock"
                sources[LOCK].write_bytes(merged.encode("utf-8"))
            missing = [
                entry["path"]
                for entry in todo
                if entry["path"] not in sources and not self._has_redo(entry)
            ]
            if missing:
                self._discard(*sources.values())
                more = f" and {len(missing) - 1} more" if len(missing) > 1 else ""
                raise ValueError(
                    f"Joinery no longer has {missing[0]}{more} as this change made it, "
                    "and the Timeline doesn't either. Nothing was changed."
                )
            # What a later Revert puts back: each file as it is now, or, for one put
            # back by hand already, as the Timeline kept it before the change.
            self._discard(self.snapshot)
            befores = {e["path"]: e["before"] for e in [*changes, *carried]}
            lost = [
                path
                for path in self._capture([*changes, *carried])
                if not self._from_timeline(path, befores[path], self.snapshot / path)
            ]
            reverted = self.changes, self.carried
            self.changes, self.carried = changes, carried
            self.state = "committing"
            self._record(changes=changes)
            applied: list[dict[str, str]] = []
            try:
                for entry in sorted(todo, key=_apply_order):
                    path = entry["path"]
                    target = self.project / path
                    if entry["after"] == DIRECTORY:
                        target.mkdir(parents=True, exist_ok=True)
                    elif entry["before"] == DIRECTORY:
                        target.rmdir()
                    elif entry["after"]:
                        place_file(sources.get(path, self.redo / path), target)
                    elif target.exists():
                        target.unlink()
                    applied.append(entry)
            except BaseException:
                self._restore(applied)
                self.state = "reverted"
                self.changes, self.carried = reverted
                self._record(changes=self.changes)
                raise
            if not self.history:  # A revert from before decisions were listed.
                self.history.append({"state": "reverted", "at": self.decided})
            self.state = "accepted"
            self.revertible = not lost
            self.decided = time.time()
            self.history.append(
                {"state": "accepted", "at": self.decided, "by": "undo revert"}
            )
            self._record(changes=changes)
            self._discard(self.redo, *sources.values())
        self._add_to_timeline(changes, reverted=False, restored=True)
        self._note_commit()
        self._stamp_unplayed(changes)
        self._index_recipes()
        self._housekeep()
        return changes

    @classmethod
    def load(cls, project: Path, identifier: str) -> FileTransaction:
        transaction = cls(project, identifier)
        raw = json.loads(transaction.journal.read_text())
        if raw.get("version") not in {1, VERSION} or raw.get("id") != identifier:
            raise ValueError("Invalid transaction journal.")
        transaction.base = raw.get("base", {})
        transaction.changes = list(raw.get("changes") or [])
        transaction.carried = [
            entry
            for entry in raw.get("carried") or []
            if isinstance(entry, dict) and entry.get("path") in CARRIED
        ]
        transaction.state = raw["state"]
        transaction.summary = raw["summary"]
        transaction.request = str(raw.get("request") or "")
        transaction.include_dirs = raw.get("include_dirs", False)
        transaction.revertible = raw.get("revertible", True) is not False
        transaction.created = float(raw.get("created", 0.0))
        transaction.decided = float(raw.get("decided", 0.0))
        transaction.tools = [
            str(tool) for tool in raw.get("tools") or [] if isinstance(tool, str)
        ]
        transaction.review_notes = [
            {
                str(k): str(v)
                for k, v in note.items()
                if k in {"severity", "text", "file"}
            }
            for note in raw.get("review_notes") or []
            if isinstance(note, dict)
        ]
        reviewer = raw.get("reviewer")
        transaction.reviewer = dict(reviewer) if isinstance(reviewer, dict) else {}
        transaction.auto_accepted = raw.get("auto_accepted") is True
        transaction.auto_notes = [
            note for note in raw.get("auto_notes") or [] if isinstance(note, str)
        ]
        transaction.guessed = [
            line for line in raw.get("guessed") or [] if isinstance(line, str)
        ]
        transaction.not_as_asked = [
            line for line in raw.get("not_as_asked") or [] if isinstance(line, str)
        ]
        transaction.chat_id = str(raw.get("chat_id") or "")
        transaction.unchecked = raw.get("unchecked") is True
        transaction.history = [
            dict(step)
            for step in raw.get("history") or []
            if isinstance(step, dict) and isinstance(step.get("state"), str)
        ]
        # Never accept journal paths outside this project, even after local tampering.
        for path in transaction.base:
            _safe(path)
        for change in transaction.changes:
            _safe(change["path"])
        return transaction

    @property
    def undo_revert_commit(self) -> str:
        """The timeline entry of this change's revert, while it is reverted."""
        if self.state != "reverted" or not self.history:
            return ""
        last = self.history[-1]
        return str(last.get("commit", "")) if last.get("state") == "reverted" else ""

    def recover(self) -> None:
        if self.state != "committing":
            return
        changes = self.changes or self.diff()
        current = inventory(self.project, include_dirs=self.include_dirs)
        applied = []
        for change in changes:
            actual = current.get(change["path"], "")
            if actual == change["after"]:
                applied.append(change)
            elif actual != change["before"]:
                raise ValueError(
                    "Recovery found a newer manual edit; automatic restore was stopped."
                )
        self._check_snapshot(applied)
        self._restore(applied)
        self._uncarry()
        self.state = "recovered"
        self.decided = time.time()
        self._record(changes=changes)
        self._discard(self.stage, self.snapshot)

    # --- Restore data ------------------------------------------------------

    def restore_bytes(self) -> int:
        """Bytes a revert of this accepted transaction, or an undo of its revert,
        would read."""
        if self.state == "reverted":
            return tree_bytes(self.redo)
        if self.state != "accepted" or not self.revertible:
            return 0
        if not self.changes:  # An old journal: the whole folder is restore data.
            return tree_bytes(self.stage) + tree_bytes(self.snapshot)
        return sum(_size(self.snapshot / path) for path in self._needed())

    def leftover_bytes(self) -> int:
        """Bytes kept for a finished transaction that nothing can use any more."""
        if self.state in PENDING:
            return 0
        total = tree_bytes(self.stage) + tree_bytes(self.snapshot)
        if self.state == "reverted":
            return total
        return total - self.restore_bytes()

    def _needed(self) -> set[str]:
        return {
            change["path"]
            for change in [*self.changes, *self.carried]
            if change["before"] and change["before"] != DIRECTORY
        }

    def _unneeded(self, root: Path) -> Iterator[Path]:
        """Snapshot files a revert never reads (left by older full copies)."""
        needed = self._needed()
        prefixes = {
            Path(path).parts[: depth + 1]
            for path in needed
            for depth in range(len(Path(path).parts))
        }
        stack = [root]
        while stack:
            folder = stack.pop()
            if not folder.is_dir():
                continue
            for child in folder.iterdir():
                parts = child.relative_to(root).parts
                if child.is_dir() and not child.is_symlink() and parts in prefixes:
                    stack.append(child)
                elif Path(*parts).as_posix() not in needed or not child.is_file():
                    yield child

    def tidy(self) -> None:
        """Delete restore data a finished transaction no longer needs."""
        if self.state in PENDING:
            return
        if self.state == "accepted" and self.revertible:
            if not self.changes:
                return  # An old journal without its changes still reverts from stage.
            self._discard(self.stage, *list(self._unneeded(self.snapshot)))
        else:
            self._discard(self.stage, self.snapshot)

    def drop_restore_data(self, reason: str) -> None:
        """Keep the journal, delete what a revert would need."""
        if self.state in PENDING:
            raise ValueError("A pending transaction keeps its restore data.")
        if self.state == "accepted" and self.revertible:
            self.revertible = False
            self._record(pruned=reason)
        self._discard(self.stage, self.snapshot, self.redo)

    @staticmethod
    def _discard(*paths: Path) -> None:
        for path in paths:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

    def _housekeep(self) -> None:
        # Pruning is bookkeeping; it must never fail a decision already made.
        try:
            apply_retention(self.project)
        except (OSError, ValueError):
            pass


#: Generated files left out of a change's title.
_GENERATED = (".uid", ".import")


def change_title(changes: list[dict[str, str]]) -> str:
    """A short name for a change from the files it touches ("Add hall.toml and
    hall.tscn", "Change door.gd, hinge.gd and 3 more files"), for a change whose
    maker gave it none. Never the request's words."""
    files = [
        change
        for change in changes
        if DIRECTORY not in {change.get("before"), change.get("after")}
    ]
    named = [c for c in files if not c["path"].endswith(_GENERATED)] or files
    if not named:
        return "Project changes"
    kinds = {c.get("change", "modified") for c in named}
    verb = (
        "Add"
        if kinds == {"created"}
        else "Remove"
        if kinds == {"deleted"}
        else "Change"
    )
    names = list(dict.fromkeys(Path(c["path"]).name for c in named))
    if len(names) == 1:
        return f"{verb} {names[0]}"
    if len(names) <= 3:
        return f"{verb} {', '.join(names[:-1])} and {names[-1]}"
    return f"{verb} {', '.join(names[:2])} and {len(names) - 2} more files"


def stage_change[T](
    project: Path, summary: str, by: str, write: Callable[[Path], T]
) -> tuple[FileTransaction, T]:
    """A change a command makes (``write`` into the stage it is given), waiting for the
    user's review and kept for it after the command ends; nothing in the project moves
    until it is accepted. Returns the transaction and what ``write`` returned."""
    tx = FileTransaction(project)
    tx.note_tool("joinery")
    tx.begin(summary)
    try:
        made = write(tx.stage)
        tx.review()
        tx.hold(by)
    except BaseException:
        if tx.state in {"staging", "review"}:
            tx.reject()
        raise
    return tx, made


def review_line(project: Path, tx: FileTransaction) -> str:
    """How the user accepts a change a command staged."""
    return (
        "Waiting for your review. Accept with: joinery transactions accept "
        f"--project {project} --id {tx.id}"
    )


# --- Retention ----------------------------------------------------------------


class HistoryPolicy(BaseModel):
    """``[history]`` in a project's joinery.toml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Accepted transactions that keep their restore data, newest first.
    keep: int = Field(default=50, ge=1)
    #: Total restore data to keep, in megabytes.
    max_mb: int = Field(default=2048, ge=1)

    @property
    def max_bytes(self) -> int:
        return self.max_mb * 1024 * 1024

    @property
    def preset(self) -> str:
        """The preset id this policy matches, or ``custom``."""
        if self.keep == 1:
            return "latest"  # Its size cap never matters: the newest always stays.
        for name, preset in POLICY_PRESETS.items():
            if preset == self:
                return name
        return "custom"

    @property
    def label(self) -> str:
        """The policy in the words the setup screens use."""
        if self.preset in POLICY_LABELS:
            return POLICY_LABELS[self.preset]
        size = (
            f"{self.max_mb / 1024:g} GB"
            if self.max_mb >= 1024 and self.max_mb % 512 == 0
            else f"{self.max_mb} MB"
        )
        return f"Last {self.keep} changes, up to {size}"


#: The choices the setup screens offer; anything else is ``custom``.
POLICY_PRESETS: dict[str, HistoryPolicy] = {
    "keep-50": HistoryPolicy(keep=50, max_mb=2048),
    "keep-10": HistoryPolicy(keep=10, max_mb=1024),
    "latest": HistoryPolicy(keep=1, max_mb=1024),
}
POLICY_LABELS = {
    "keep-50": "Last 50 changes, up to 2 GB",
    "keep-10": "Last 10 changes, up to 1 GB",
    "latest": "Only the newest",
}


def load_policy(project: Path) -> HistoryPolicy:
    path = project / "joinery.toml"
    if not path.is_file():
        return HistoryPolicy()
    try:
        raw = tomllib.loads(path.read_text()).get("history", {})
        return HistoryPolicy.model_validate(raw)
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ValueError(f"joinery.toml [history] is invalid: {exc}") from None


def list_transactions(project: Path) -> list[FileTransaction]:
    """Every readable file transaction of a project, oldest first."""
    project = project.resolve()
    found = []
    for journal in sorted((project / TRANSACTIONS).glob("*/journal.json")):
        try:
            found.append(FileTransaction.load(project, journal.parent.name))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    return sorted(found, key=lambda tx: _when(tx))


def _when(tx: FileTransaction) -> float:
    if tx.decided or tx.created:
        return tx.decided or tx.created
    try:
        return tx.journal.stat().st_mtime
    except OSError:
        return 0.0


class HistoryLock:
    """Only one process prunes or compacts a project's history at a time."""

    def __init__(self, project: Path) -> None:
        path = project / ".joinery/history.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file: BinaryIO = path.open("a+b")
        try:
            lock_file(self.file)
        except BlockingIOError:
            self.file.close()
            raise ValueError(
                "Joinery is already tidying this project's history."
            ) from None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        if not self.file.closed:
            unlock_file(self.file)
            self.file.close()


def _wait_for_history(project: Path, wait: float = 10.0) -> HistoryLock:
    """The history lock, waiting briefly while retention or compaction holds it."""
    deadline = time.monotonic() + wait
    while True:
        try:
            return HistoryLock(project)
        except ValueError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def apply_retention(
    project: Path, policy: HistoryPolicy | None = None, dry_run: bool = False
) -> dict[str, int]:
    """Keep restore data for recent accepted transactions, prune the rest.

    The newest accepted transaction always stays revertible. Older ones keep
    their data while they are within both ``keep`` and ``max_mb``; everything
    beyond that window keeps its journal but is marked not revertible.
    ``dry_run`` only counts what would go.
    """
    project = project.resolve()
    policy = policy or load_policy(project)
    pruned = pruned_bytes = kept = kept_bytes = 0
    with HistoryLock(project):
        found = list_transactions(project)
        if not dry_run:
            for tx in found:
                tx.tidy()
        accepted = [tx for tx in reversed(found) if tx.state == "accepted"]
        window = True
        for index, tx in enumerate(accepted):
            if not tx.revertible:
                continue
            size = tx.restore_bytes()
            window = window and (
                index == 0
                or (index < policy.keep and kept_bytes + size <= policy.max_bytes)
            )
            if window:
                kept += 1
                kept_bytes += size
                continue
            pruned += 1
            pruned_bytes += size
            if not dry_run:
                tx.drop_restore_data("retention")
        # A revert is undone from its own copies while it is among the newest; an
        # older one still can be from the Timeline.
        reverted = [tx for tx in reversed(found) if tx.state == "reverted"]
        for tx in reverted[policy.keep :]:
            size = tx.restore_bytes()
            if size:
                pruned_bytes += size
                if not dry_run:
                    tx.drop_restore_data("retention")
    return {
        "kept": kept,
        "kept_bytes": kept_bytes,
        "pruned": pruned,
        "pruned_bytes": pruned_bytes,
    }
