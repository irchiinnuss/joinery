"""Changes not yet played by you: which accepted changes no person has played since.

Checks, tests, replays and the playtest bot prove the game *works*; none of them
says whether a person has actually *played* it since a change. So every accepted
change that affects the game (scenes, scripts, assets, project settings; not
docs, design notes, tests or Joinery's own state) gets a stamp, and a real
person's run of the game from the editor after it clears the stamp.

What counts as a person's run is decided where the run happens: the game core's
Replays autoload (``res://core/replays/replays.gd``, ADR 0059) reports an editor
run to the editor through the debugger once it has lasted :data:`PLAY_SECONDS`
with the person's own input, and never for a replay, the playtest bot, a test,
a headless run or a run with Joinery's or a test's arguments. The add-on hears
it and calls ``joinery played record``; everything else reads the state here.

The stamps live in ``.joinery/played.json``: per person and per machine (who
played is the person at this editor), so it stays out of git with the rest of
``.joinery/``. Every agent sees the same state because it lives in the shared
core: the gateway puts it on each decision, ``joinery status`` and ``joinery
run`` print it, and the Timeline marks its entries.

    joinery played status --project P [--json]
    joinery played record --project P --started T --seconds S [--json]
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from joinery.transactions import atomic_json

#: Where the stamps are kept, relative to the game project (out of git).
STATE = Path(".joinery") / "played.json"
VERSION = 1
#: A run must last this long, with the person's input, to count as played.
PLAY_SECONDS = 10.0
#: Stamps kept at most; the oldest go first.
MAX_STAMPS = 200
#: Files and folders that are not the game: docs, design notes, tests, Joinery's own.
NOT_GAME_DIRS = (
    ".joinery/",
    ".godot/",
    "docs/",
    "design/",
    "tests/",
    "art/reference/",
    "addons/joinery/",
    "addons/gut/",
)
NOT_GAME_FILES = frozenset(
    {
        ".gitignore",
        ".gitattributes",
        "joinery.toml",
        "joinery.lock",
        "LICENSE",
        "JOURNEYMAN.md",
    }
)
NOT_GAME_SUFFIXES = (".md", ".txt", ".rst", ".pdf")
#: What the change card and the Timeline say about a stamped change.
CHANGE_LINE = "Not played since this change"


def affects_game(paths: Iterable[str]) -> bool:
    """Whether a change to ``paths`` changes the game a person plays."""
    for raw in paths:
        path = raw.removeprefix("res://").lstrip("/")
        if not path or path in NOT_GAME_FILES:
            continue
        if (path.rstrip("/") + "/").startswith(NOT_GAME_DIRS) or path.lower().endswith(
            NOT_GAME_SUFFIXES
        ):
            continue
        return True
    return False


def _read(project: Path) -> dict[str, Any]:
    try:
        raw = json.loads((project / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": VERSION, "unplayed": [], "last_played": 0.0}
    stamps = [
        stamp
        for stamp in raw.get("unplayed") or []
        if isinstance(stamp, dict) and isinstance(stamp.get("id"), str)
    ]
    last = raw.get("last_played", 0.0)
    return {
        "version": VERSION,
        "unplayed": stamps,
        "last_played": float(last) if isinstance(last, (int, float)) else 0.0,
    }


def _write(project: Path, state: Mapping[str, Any]) -> None:
    path = project / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, dict(state))


def stamp(
    project: Path,
    identifier: str,
    summary: str,
    paths: Sequence[str],
    commit: str = "",
    when: float | None = None,
) -> bool:
    """Stamp an accepted change "not yet played by you" when it affects the game.
    Returns whether it was stamped. Bookkeeping: never raises on a bad file."""
    if not identifier or not affects_game(paths):
        return False
    state = _read(project)
    stamps = [s for s in state["unplayed"] if s.get("id") != identifier]
    stamps.append(
        {
            "id": identifier,
            "summary": " ".join(summary.split())[:160],
            "accepted": time.time() if when is None else when,
            **({"commit": commit} if commit else {}),
        }
    )
    state["unplayed"] = stamps[-MAX_STAMPS:]
    try:
        _write(project, state)
    except OSError:
        return False
    return True


def recommit(project: Path, identifier: str, commit: str) -> None:
    """The change's Timeline entry was rewritten (Godot's import files joined it)."""
    state = _read(project)
    changed = False
    for entry in state["unplayed"]:
        if entry.get("id") == identifier and commit and entry.get("commit") != commit:
            entry["commit"] = commit
            changed = True
    if changed:
        try:
            _write(project, state)
        except OSError:
            pass


def forget(project: Path, identifier: str) -> None:
    """A reverted change is gone from the game: it has nothing left to play."""
    state = _read(project)
    kept = [s for s in state["unplayed"] if s.get("id") != identifier]
    if len(kept) != len(state["unplayed"]):
        state["unplayed"] = kept
        try:
            _write(project, state)
        except OSError:
            pass


def record_play(project: Path, started: float, seconds: float) -> dict[str, Any]:
    """A person ran the game from the editor, starting at ``started`` (seconds since
    the epoch) for ``seconds``. A run long enough clears every change accepted
    before it started; a short one changes nothing. Returns :func:`summary`."""
    if seconds < PLAY_SECONDS or started <= 0:
        return {**summary(project), "counted": False}
    state = _read(project)
    state["unplayed"] = [
        s for s in state["unplayed"] if float(s.get("accepted", 0.0)) > started
    ]
    state["last_played"] = max(state["last_played"], started)
    _write(project, state)
    return {**summary(project), "counted": True}


def unplayed(project: Path) -> list[dict[str, Any]]:
    """The stamped changes, oldest first."""
    return list(_read(project)["unplayed"])


def is_unplayed(project: Path, identifier: str) -> bool:
    return any(s.get("id") == identifier for s in unplayed(project))


def count_text(count: int) -> str:
    """ "3 changes not played yet"; "" for none."""
    if count <= 0:
        return ""
    return f"{count} change{'' if count == 1 else 's'} not played yet"


def summary(project: Path) -> dict[str, Any]:
    """{count, text, ids, commits, last_played} for the sidebar, status and runs."""
    state = _read(project)
    stamps = state["unplayed"]
    return {
        "count": len(stamps),
        "text": count_text(len(stamps)),
        "ids": [str(s["id"]) for s in stamps],
        "commits": [str(s["commit"]) for s in stamps if s.get("commit")],
        "last_played": state["last_played"],
    }


# --- Command line -----------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``played status|record``."""
    parser = subparsers.add_parser(
        "played",
        help="accepted changes no person has played since: list, or record a play",
    )
    parser.add_argument("action", choices=["status", "record"])
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "--started", type=float, default=0.0, help="record: when the run started"
    )
    parser.add_argument(
        "--seconds", type=float, default=0.0, help="record: how long it lasted"
    )
    parser.add_argument("--json", action="store_true")


def run(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    if not (project / "project.godot").is_file():
        message = f"No Godot project at {project}."
        print(json.dumps({"error": message}) if args.json else message)
        return 2
    try:
        if args.action == "record":
            data = record_play(project, args.started, args.seconds)
        else:
            data = {**summary(project), "changes": unplayed(project)}
    except OSError as exc:
        print(json.dumps({"error": str(exc)}) if args.json else str(exc))
        return 1
    if args.json:
        print(json.dumps(data))
        return 0
    if not data["count"]:
        print("Every accepted change has been played since.")
        return 0
    print(data["text"] + ":")
    for entry in data.get("changes") or unplayed(project):
        print(f"  {entry.get('summary') or entry['id']}")
    return 0
