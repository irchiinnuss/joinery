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
from pathlib import Path
from typing import Any

from joinery.roles import set_agents_values

KEY = "auto_accept"


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
