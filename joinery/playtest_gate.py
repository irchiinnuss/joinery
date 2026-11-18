"""When the playtester may run: only when the user asks (ADR 0038, "Only when asked").

A playtest is the user's choice. It runs from ``/playtest``, the playtest card's own
buttons, a request in the chat that asks for one in so many words ("playtest it", "run
the bot"), or ``[agents] playtest_after_change = true``, which the user turned on.
Agents never start one on their own: the gateway's ``playtest`` method refuses the lead
and tasks unless the user's request that started their turn asks for a playtest
(:func:`asks`), and the policy hook refuses ``joinery playtest`` in an agent's shell, so
the same holds for Codex, Claude Code, Gemini CLI and opencode.

``[agents] playtester = false`` turns the playtester off for the project: ``/playtest``
and its card are hidden, the ``playtest`` tool refuses every caller, and the "done when"
items that need a playtest (``reach``, ``no_errors``) are not checked. The Settings page's
"Playtester" switch writes it through ``joinery playtester``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

from joinery.roles import set_agents_values

#: A request that asks for a playtest: the word itself, or the bot asked to play.
_ASKS = re.compile(
    r"\bplay[\s-]?test(?:s|ing|ed|er)?\b"
    r"|\btest[\s-]?play(?:s|ing|ed)?\b"
    r"|\b(?:run|start|use|let|have|send|get)\s+(?:the\s+|a\s+|your\s+)?"
    r"(?:test\s+|playtest\s+|play\s+)?bot\b"
    r"|\bbot\s+play(?:s|ing)?\b",
    re.IGNORECASE,
)
#: The same words after a "don't": the request says not to.
_NOT = re.compile(
    r"\b(?:don'?t|do\s+not|no|not|without|never|skip|stop)\s+"
    r"(?:\w+\s+){0,3}?(?:play[\s-]?test|test[\s-]?play|(?:the\s+)?bot\b)",
    re.IGNORECASE,
)

#: What an agent is told when it calls ``playtest`` without being asked.
NOT_ASKED = (
    "Playtests run only when the user asks for one. Don't start one yourself: if "
    "playing the game would help, tell the user they can run /playtest (or ask you "
    "to playtest it), and carry on with what they asked."
)
#: What an agent is told when the project turned the playtester off.
OFF = (
    "The playtester is off for this project (Settings > Playtester). Don't play the "
    "game; check your change another way, or tell the user they can turn it on."
)


def asks(text: str) -> bool:
    """Whether a user's request asks, in so many words, for a playtest."""
    if not text:
        return False
    found = _ASKS.search(text)
    if found is None:
        return False
    # "Don't playtest it" asks for nothing; "fix X, then playtest it" still asks.
    negated = _NOT.search(text)
    return negated is None or negated.end() < found.start()


def enabled(project: Path) -> bool:
    """``[agents] playtester`` (default true): false turns the playtester off."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    table = raw.get("agents", {})
    return not (isinstance(table, dict) and table.get("playtester") is False)


def set_enabled(project: Path, on: bool) -> None:
    """Save ``[agents] playtester``, keeping every other line of joinery.toml."""
    set_agents_values(project, {"playtester": "true" if on else "false"})
    if enabled(project) is not on:
        raise ValueError("joinery.toml keeps [agents] in a form Joinery can't edit.")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--set", choices=("on", "off"), default=None)
    parser.add_argument("--json", action="store_true")


def main(args: argparse.Namespace) -> int:
    """The Settings page's "Playtester" switch."""
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
    if args.json:
        print(json.dumps({"playtester": on}))
    else:
        print(
            "Playtester: on. It plays only when you ask (/playtest)."
            if on
            else "Playtester: off."
        )
    return 0
