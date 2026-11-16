"""Accept changes automatically (ADR 0085): the project's opt-in, and the rule.

``[agents] auto_accept = true`` in joinery.toml keeps a submitted change without
asking when nothing about it needs the user: every check passed, the reviewer
agent left nothing that holds it, and the change leaves the art direction and
the game brief's direction alone. Anything else waits in review as it always
has. Off by default.

The reviewer's findings come in three levels. A ``block`` always holds the
change. A ``warn`` holds it when ``[agents] auto_accept_hold = "warn"`` asks
for that, and always when it says the game breaks or work is lost (rule
``broken`` or ``work_lost``); with the default, ``"block"``, other warnings are
shown with the kept change. A note (``info``) never holds it and is shown with
it. A routine edit of ``design/brief.md`` (controls, verbs, a renamed action)
is kept with a note; one that changes its direction (pitch, genre, audience,
fantasy, core loop, scope, features, Keep, Avoid, monetisation, or those facts)
waits. A change that crosses one of the brief's Keep or Avoid lines (moves a pinned
value, adds something to avoid; ``joinery.keep_avoid``) waits too. A change to a
file Joinery owns in the game (the shared core and test harness,
``joinery.core_update.owned``) always waits, with a note. A change the agent says
it made differently from the request (submit_for_review's ``not_as_asked``)
waits too ("differs from your request"); what it only guessed is shown with the
kept change.

The rule lives here and is applied by the turn (``joinery.turn``), so every agent
(Claude Code, Codex, Gemini CLI, opencode) and every parallel task is judged the
same way. The accept itself is the user's own Accept path, so the commit, the
Timeline entry, history and lock entries are the same; undoing it is the same
Revert any accepted change has.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from joinery.brief import BRIEF_PATH
from joinery.done_when import Result
from joinery.peer_review import PeerReview, ReviewNote
from joinery.roles import set_agents_values

KEY = "auto_accept"
#: ``[agents] auto_accept_hold``: the reviewer level that holds a change.
HOLD_KEY = "auto_accept_hold"
HOLD_LEVELS = ("block", "warn")
DEFAULT_HOLD = "block"
#: A warning of these kinds holds the change whatever the setting says.
ALWAYS_HOLD_RULES = frozenset({"broken", "work_lost"})
#: The art direction: its approval is the user's alone (art_approval), so a change
#: to it always waits.
DIRECTION_FILES = frozenset({"art/bible.md"})
#: The game brief: it waits only when its direction changes (``brief.direction_changes``).
BRIEF = BRIEF_PATH.as_posix()
#: The art references beside the bible, and Joinery's own state (approvals).
DIRECTION_DIRS = ("art/reference/", ".joinery/")
#: The words the sidebar and the agent use.
ACCEPTED = "Accepted automatically"


def enabled(project: Path) -> bool:
    """``[agents] auto_accept`` in joinery.toml; off when unset or unreadable."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    table = raw.get("agents", {})
    return isinstance(table, dict) and table.get(KEY, False) is True


def hold_level(project: Path) -> str:
    """``[agents] auto_accept_hold``: "block" (the default) or "warn"."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_HOLD
    table = raw.get("agents", {})
    value = table.get(HOLD_KEY, DEFAULT_HOLD) if isinstance(table, dict) else ""
    return value if value in HOLD_LEVELS else DEFAULT_HOLD


def set_hold_level(project: Path, level: str) -> None:
    """Save which reviewer level holds a change, keeping the rest of joinery.toml."""
    if level not in HOLD_LEVELS:
        raise ValueError("The hold level is block or warn.")
    set_agents_values(project, {HOLD_KEY: f'"{level}"'})
    if hold_level(project) != level:
        raise ValueError("joinery.toml keeps [agents] in a form Joinery can't edit.")


def set_enabled(project: Path, on: bool) -> None:
    """Save the choice, keeping every other line of joinery.toml."""
    set_agents_values(project, {KEY: "true" if on else "false"})
    if enabled(project) is not on:
        raise ValueError("joinery.toml keeps [agents] in a form Joinery can't edit.")


def direction_paths(paths: Iterable[str]) -> list[str]:
    """The paths among ``paths`` that are art direction or approval state."""
    found = []
    for path in paths:
        clean = path.removeprefix("res://")
        if clean in DIRECTION_FILES or clean.startswith(DIRECTION_DIRS):
            found.append(clean)
    return found


def holds(note: ReviewNote, hold_on: str = DEFAULT_HOLD) -> bool:
    """Whether a reviewer note keeps the change waiting for the user."""
    if note.severity == "block":
        return True
    if note.severity == "warn":
        return hold_on == "warn" or note.rule in ALWAYS_HOLD_RULES
    return False


def shown_notes(
    peer: PeerReview | None,
    hold_on: str = DEFAULT_HOLD,
    brief_sections: Sequence[str] = (),
) -> list[str]:
    """What a kept change says beside "Accepted automatically": the reviewer notes
    that didn't hold it, and the brief sections a routine edit touched."""
    lines = [
        ("Warning: " if note.severity == "warn" else "Note: ")
        + note.text
        + (f" ({note.file})" if note.file and note.file not in note.text else "")
        for note in (peer.notes if peer is not None else [])
        if not holds(note, hold_on)
    ]
    if brief_sections:
        lines.append(
            "Note: updated the game brief ("
            + ", ".join(brief_sections)
            + "); its direction is unchanged."
        )
    return lines


def held(
    *,
    changes: Sequence[Mapping[str, str]],
    notes: Sequence[str] = (),
    peer: PeerReview | None = None,
    reviewer_on: bool = False,
    done: Sequence[Result] = (),
    live_edits: int = 0,
    live_scenes: Iterable[str] = (),
    editor_keeps_live: bool = False,
    art_gate: bool = False,
    last_chance: bool = False,
    unsaved: Iterable[str] = (),
    hold_on: str = DEFAULT_HOLD,
    brief_direction: Sequence[str] | None = None,
    owned: Sequence[str] = (),
    crossed: Sequence[str] = (),
    not_as_asked: Sequence[str] = (),
) -> list[str]:
    """Why a change that reached review waits for the user; empty when it may be
    kept without asking. Each reason is a short plain line, for logs and metrics.

    ``notes`` are the submit's own notes for the user (placement and the like);
    ``peer`` the reviewer agent's review (``reviewer_on`` when one should have
    run); ``done`` the to-do's done-when results; ``live_edits`` the live scene
    edits waiting with the change in ``live_scenes`` ("res://…", "" for a scene
    never saved), which only the editor can keep: with ``editor_keeps_live`` it
    does, with its own Accept (the chat's own request); otherwise they wait.
    ``art_gate`` an art gate riding on the review; ``last_chance`` Joinery's own
    submit of a change the agent left; ``unsaved`` the scenes that had unsaved
    edits in the editor when the request was sent ("res://…"): the user's own
    work, which an accept must never write over or save. ``hold_on`` is the
    reviewer level that holds (``hold_level``); ``brief_direction`` the parts of
    the brief's direction the change alters (``brief.direction_changes``), None
    when nobody looked, which holds a change to the brief. ``owned`` are the
    Joinery-owned files it changes (``owned_changes``): always a hold.
    ``crossed`` the brief's Keep/Avoid lines it crosses (``keep_avoid.check``, as
    ``Flag.reason``): always a hold, naming the lines.
    ``not_as_asked`` are the agent's own lines on where it departed from the
    request: any one holds the change (:data:`DEPARTED`). What it only guessed
    never holds it; it is shown with the kept change (:func:`guessed_notes`).
    """
    reasons: list[str] = []
    paths = [str(c.get("path", "")) for c in changes]
    if not paths and not live_edits:
        reasons.append("no file changes")
    if direction_paths(paths):
        reasons.append("it changes the art direction")
    if any(p.removeprefix("res://") == BRIEF for p in paths):
        if brief_direction is None:
            reasons.append("it changes the game brief")
        elif brief_direction:
            reasons.append(
                "it changes the game brief's direction ("
                + ", ".join(brief_direction)
                + ")"
            )
    if crossed:
        reasons.append(
            "it crosses the game brief's Keep/Avoid lines ("
            + "; ".join(crossed[:3])
            + ")"
        )
    if any(line.strip() for line in not_as_asked):
        reasons.append(DEPARTED)
    if owned:
        reasons.append(OWNED_REASON + " (" + ", ".join(owned[:3]) + ")")
    if notes:
        reasons.append("it has notes for the user")
    found = peer.notes if peer is not None else []
    if any(n.severity == "block" for n in found):
        reasons.append("the reviewer found a problem that wasn't fixed")
    warnings = [n for n in found if n.severity == "warn" and holds(n, hold_on)]
    if any(n.rule in ALWAYS_HOLD_RULES for n in warnings):
        reasons.append("the reviewer warned the game may break or work be lost")
    elif warnings:
        reasons.append("the reviewer raised a warning")
    if reviewer_on and (peer is None or peer.status in {"timeout", "error"}):
        reasons.append("the reviewer didn't finish")
    if any(result.status != "pass" for result in done):
        reasons.append("a done-when item didn't pass")
    live = [s.removeprefix("res://") for s in live_scenes] if live_edits else []
    if live_edits and not editor_keeps_live:
        reasons.append("it has live scene edits")
    if art_gate:
        reasons.append("the art direction needs approval")
    if last_chance:
        reasons.append("Joinery submitted it for the agent")
    open_unsaved = {u.removeprefix("res://") for u in unsaved}
    if open_unsaved.intersection([*paths, *live]):
        reasons.append("a scene it changes has unsaved edits")
    if "" in live:
        reasons.append("a scene it changes isn't saved yet")
    return reasons


#: Why a change whose checks failed, or could not run, waits.
FAILED_CHECK = "it failed a check"
#: Why an unfinished change a stopped run kept waits.
UNCHECKED = "it stopped before Joinery checked it"


def check_reasons(failed: Iterable[tuple[str, str]]) -> list[str]:
    """Held reasons for a change kept with failed checks: one per check, with the
    first line of what it said ("it failed a check (tests: player_test failed)").
    Joinery's own failure to run them reads as itself, never blank."""
    reasons: list[str] = []
    for check, message in failed:
        line = next((t.strip() for t in message.splitlines() if t.strip()), "")
        if line.startswith("Joinery could not run its checks"):
            reason = line[:200]
        else:
            reason = (
                f"{FAILED_CHECK} ({check}" + (f": {line[:160]}" if line else "") + ")"
            )
        if reason not in reasons:
            reasons.append(reason)
    return reasons or [FAILED_CHECK]


#: Why a change the agent made differently from the request waits.
DEPARTED = "differs from your request"
#: What a kept change says about each thing the agent guessed.
GUESSED = "Guessed: "


def guessed_notes(guessed: Sequence[str]) -> list[str]:
    """The agent's guesses as notes shown with a kept change ("Guessed: oak")."""
    return [GUESSED + line.strip() for line in guessed if line.strip()]


#: Why a change to Joinery's own game files waits, in the held reasons.
OWNED_REASON = "a Joinery-owned harness file changed"
#: What the agent and the user are told about it.
OWNED_NOTE = (
    "This change edits files Joinery owns in the game ({paths}): the shared core "
    "and test harness every test relies on. It waits for you, and Joinery's "
    "updates stop reaching an edited file. Usually the game or its own test "
    "should change instead."
)


def owned_changes(project: Path, paths: Iterable[str]) -> list[str]:
    """The Joinery-owned game files (core, test harness) among ``paths``.

    A source folder's ``.gdignore`` marker is not one: Joinery's tools write it
    themselves the first time they put a source there (the music tool into
    ``audio/sources/`` and ``recipes/``), the same empty file ``update-core``
    would add, so it never holds a change on its own.
    """
    from joinery import core_update, source_folders

    try:
        owned = core_update.owned(project)
    except (OSError, ValueError):
        return []
    markers = {
        f"{folder}/{source_folders.MARKER}" for folder in source_folders.SOURCE_ONLY
    }
    found = {p.removeprefix("res://") for p in paths}.intersection(owned)
    return sorted(found - markers)


def owned_note(paths: Sequence[str]) -> str:
    return OWNED_NOTE.format(paths=", ".join(paths[:3]))


def unsaved_scenes(editor_context: Mapping[str, str]) -> list[str]:
    """The editor's scenes with unsaved edits, from its context."""
    try:
        found = json.loads(editor_context.get("unsaved_scenes", "[]") or "[]")
    except ValueError:
        return []
    return [str(s) for s in found] if isinstance(found, list) else []


# --- `joinery auto-accept` ---------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--set", choices=["on", "off"], default=None)
    parser.add_argument(
        "--hold-on",
        choices=list(HOLD_LEVELS),
        default=None,
        help="the reviewer level that keeps a change waiting: block (default) or warn",
    )
    parser.add_argument("--json", action="store_true")


def main(args: argparse.Namespace) -> int:
    """The Settings page's "Accept changes automatically" switch and its hold level."""
    project = args.project.resolve()
    try:
        if args.set is not None:
            set_enabled(project, args.set == "on")
        if getattr(args, "hold_on", None) is not None:
            set_hold_level(project, args.hold_on)
        on = enabled(project)
        level = hold_level(project)
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(str(exc), file=sys.stderr)
        return 1
    data: dict[str, Any] = {"enabled": on, "hold_on": level}
    if args.json:
        print(json.dumps(data))
    else:
        print(
            "Changes that pass every check are kept without asking. "
            + (
                "Reviewer warnings keep a change waiting."
                if level == "warn"
                else "Only the reviewer's blocking findings keep a change waiting."
            )
            if on
            else "Every change waits for your Accept."
        )
    return 0
