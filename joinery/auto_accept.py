"""Accept changes automatically (ADR 0085): the project's opt-in, and the rule.

``[agents] auto_accept = true`` in joinery.toml keeps a submitted change without
asking when nothing about it needs the user: every check passed with nothing to
say, the reviewer agent raised no warning, and the change leaves the art
direction alone. Anything else waits in review as it always has. Off by default.

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

from joinery.done_when import Result
from joinery.peer_review import PeerReview
from joinery.roles import set_agents_values

KEY = "auto_accept"
#: The art direction and the game brief: their approval is the user's alone
#: (art_approval, team.SHARED_DOCUMENTS), so a change to one always waits.
DIRECTION_FILES = frozenset({"art/bible.md", "design/brief.md"})
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


def held(
    *,
    changes: Sequence[Mapping[str, str]],
    notes: Sequence[str] = (),
    peer: PeerReview | None = None,
    reviewer_on: bool = False,
    done: Sequence[Result] = (),
    live_edits: int = 0,
    art_gate: bool = False,
    last_chance: bool = False,
    unsaved: Iterable[str] = (),
) -> list[str]:
    """Why a change that reached review waits for the user; empty when it may be
    kept without asking. Each reason is a short plain line, for logs and metrics.

    ``notes`` are the submit's own notes for the user (placement and the like);
    ``peer`` the reviewer agent's review (``reviewer_on`` when one should have
    run); ``done`` the to-do's done-when results; ``live_edits`` the live scene
    edits waiting with the change, which the editor itself keeps; ``art_gate``
    an art gate riding on the review; ``last_chance`` Joinery's own submit of a
    change the agent left; ``unsaved`` the scenes with unsaved edits in the editor
    ("res://…"), whose saved file an accept would overwrite.
    """
    reasons: list[str] = []
    paths = [str(c.get("path", "")) for c in changes]
    if not paths:
        reasons.append("no file changes")
    if direction_paths(paths):
        reasons.append("it changes the art direction or the game brief")
    if notes:
        reasons.append("it has notes for the user")
    if peer is not None and any(n.severity in {"warn", "block"} for n in peer.notes):
        reasons.append("the reviewer raised a warning")
    if reviewer_on and (peer is None or peer.status in {"timeout", "error"}):
        reasons.append("the reviewer didn't finish")
    if any(result.status != "pass" for result in done):
        reasons.append("a done-when item didn't pass")
    if live_edits:
        reasons.append("it has live scene edits")
    if art_gate:
        reasons.append("the art direction needs approval")
    if last_chance:
        reasons.append("Joinery submitted it for the agent")
    open_unsaved = {u.removeprefix("res://") for u in unsaved}
    if open_unsaved.intersection(paths):
        reasons.append("a scene it changes has unsaved edits")
    return reasons


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
    parser.add_argument("--json", action="store_true")


def main(args: argparse.Namespace) -> int:
    """The Settings page's "Accept changes automatically" switch."""
    project = args.project.resolve()
    try:
        if args.set is not None:
            set_enabled(project, args.set == "on")
        on = enabled(project)
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(str(exc), file=sys.stderr)
        return 1
    data: dict[str, Any] = {"enabled": on}
    if args.json:
        print(json.dumps(data))
    else:
        print(
            "Changes that pass every check are kept without asking."
            if on
            else "Every change waits for your Accept."
        )
    return 0
