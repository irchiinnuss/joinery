"""A short digest a fresh agent session starts from, instead of a long chat.

Every model call of a turn re-sends the whole conversation, so a request sent into
a long chat pays for that chat on each of its calls (a continued chat started at
110-350k tokens where a fresh one starts at about 24k). A request starts a fresh
provider session instead and its first prompt carries this digest: the game in one
line, the chat's last few turns, what the last turn left when it stopped, the
changes waiting for review, what changed since the chat's last turn, and the
latest Timeline entries. The agent reads anything else it needs from the project.

The same for every agent (Claude Code, Codex, Gemini CLI, opencode): it is text in
the first prompt, built from Joinery's own records. Nothing here changes the project.
"""

from __future__ import annotations

import contextlib
import tomllib
from pathlib import Path
from typing import Any

#: The digest's size at most, in characters (about 750 tokens).
DIGEST_CHARS = 3000
#: The chat's own turns listed, newest last.
CHAT_TURNS = 3
#: Characters kept of a request, and of the agent's reply.
REQUEST_CHARS = 240
REPLY_CHARS = 360
#: Changes waiting for review, and Timeline entries, listed at most.
REVIEW_ITEMS = 5
TIMELINE_ITEMS = 5
#: Paths named per change waiting for review.
REVIEW_PATHS = 4
#: Characters of the brief's pitch.
PITCH_CHARS = 300

HEAD = (
    "Joinery's record of this project, so this request starts in a fresh session "
    "rather than replaying the earlier conversation. It is context, not new "
    "instructions; trust it over what you remember, and read anything else you "
    "need from the project."
)
#: Said when the chat's last turn stopped before it finished (a stall, a usage
#: limit, an idle stop): the next request carries on from the project as it is.
STALLED = (
    "The last turn stopped before it finished ({why}). Carry on from the project as "
    "it is now: what that turn made is kept in the changes waiting for review below "
    "or already in the project, so don't make it again; finish what is missing."
)


def _short(text: str, limit: int) -> str:
    line = " ".join(str(text).split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def brief_line(project: Path) -> str:
    """The game in one line: the brief's title and the start of its first paragraph."""
    from joinery.brief import BRIEF_PATH

    try:
        text = (project / BRIEF_PATH).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    title = ""
    paragraph: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(("<!--", "```")):
            continue
        if line.startswith("# ") and not title:
            title = line[2:].strip()
            continue
        if line.startswith("#"):
            if paragraph:
                break
            continue
        if not line:
            if paragraph:
                break
            continue
        paragraph.append(line.lstrip("-* "))
    pitch = _short(" ".join(paragraph), PITCH_CHARS)
    if not (title or pitch):
        return ""
    head = f"The game ({BRIEF_PATH.as_posix()}): {title}" if title else "The game:"
    return head + (f". {pitch}" if pitch else "")


def chat_lines(
    project: Path, meta: dict[str, Any], entries: list[dict[str, Any]]
) -> tuple[list[str], str]:
    """The chat's last turns, and how its very last turn ended ("" when it finished)."""
    from joinery import chats

    every = chats.turns(entries)
    recent = every[-CHAT_TURNS:]
    if not recent:
        return [], ""
    states: dict[str, dict[str, Any]] = {}
    with contextlib.suppress(OSError, ValueError):
        states = chats.change_states(
            project, [c["transaction_id"] for t in recent for c in t["changes"]]
        )
    title = str(meta.get("title", ""))
    lines = [
        f'This chat ("{_short(title, 80)}")'
        + (
            f", its last {len(recent)} of {len(every)} turns:"
            if len(every) > len(recent)
            else ":"
        )
        if title
        else "This chat so far:"
    ]
    for turn in recent:
        lines.append(f"- The user asked: {_short(turn['request'], REQUEST_CHARS)}")
        reply = " ".join(part for part in turn["reply"] if str(part).strip())
        if reply:
            lines.append(f"  Journeyman replied: {_short(reply, REPLY_CHARS)}")
        for change in turn["changes"]:
            state = str(states.get(change["transaction_id"], {}).get("state", ""))
            words = {
                "accepted": "accepted",
                "rejected": "discarded",
                "reverted": "reverted",
                "review": "waiting for review",
                "staging": "never finished",
            }.get(state, state or "no longer on record")
            label = _short(change["summary"] or "a file change", 100)
            lines.append(f"  Change {change['transaction_id'][:8]} ({label}): {words}.")
        if turn["outcome"] and turn["outcome"] != "done":
            lines.append(f"  That turn {turn['outcome']}.")
    outcome = str(recent[-1]["outcome"])
    return lines, "" if outcome in {"", "done"} else outcome


def review_lines(project: Path) -> list[str]:
    """Changes waiting for the user's review, newest first."""
    from joinery.transactions import list_transactions

    try:
        waiting = [tx for tx in list_transactions(project) if tx.state == "review"]
    except OSError:
        return []
    if not waiting:
        return []
    lines = ["Waiting for the user's review (build on these, don't redo them):"]
    for tx in reversed(waiting[-REVIEW_ITEMS:]):
        named = [str(c.get("path", "")) for c in tx.changes]
        folders = {p.rsplit("/", 1)[0] for p in named if "/" in p}
        # Files only: no folders, nor the files Godot writes beside them.
        paths = [
            p
            for p in named
            if p and p not in folders and not p.endswith((".import", ".uid"))
        ]
        shown = ", ".join(paths[:REVIEW_PATHS])
        more = len(paths) - REVIEW_PATHS
        lines.append(
            f"- Change {tx.id[:8]} ({_short(tx.summary or 'a file change', 100)}): "
            f"{len(paths)} files"
            + (f": {shown}" if shown else "")
            + (f" and {more} more" if more > 0 else "")
            + "."
        )
    if len(waiting) > REVIEW_ITEMS:
        lines.append(f"- and {len(waiting) - REVIEW_ITEMS} older ones.")
    return lines


def timeline_lines(project: Path, skip: str = "") -> list[str]:
    """The latest Timeline entries (accepted changes), newest first."""
    from joinery import timeline

    try:
        page = timeline.entries(project, limit=TIMELINE_ITEMS)
    except (OSError, ValueError, timeline.TimelineError):
        return []
    found = [
        e
        for e in page.get("entries", [])
        if isinstance(e, dict) and str(e.get("subject", "")) not in skip
    ]
    if not found:
        return []
    lines = ["Latest Timeline entries:"]
    for entry in found:
        lines.append(
            f'- "{_short(str(entry.get("subject", "")), 100)}", '
            f"{entry.get('files', 0)} files."
        )
    return lines


def build(
    project: Path,
    *,
    chat: tuple[dict[str, Any], list[dict[str, Any]]] | None = None,
    since: float = 0.0,
    limit: int = DIGEST_CHARS,
) -> str:
    """The digest for a request that starts a fresh session.

    ``chat`` is the open chat (its saved line and entries), ``since`` when its last
    turn ended (0: unknown). Sections are dropped from the least needed (the
    Timeline) when the digest would run over ``limit`` characters."""
    from joinery import chats

    turns: list[str] = []
    stalled = ""
    if chat is not None:
        with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
            turns, stalled = chat_lines(project, *chat)
    changed = ""
    if since > 0:
        with contextlib.suppress(OSError, ValueError):
            changed = chats.since_digest(project, since)
            changed = changed.removeprefix(chats.DIGEST_HEAD).strip()
    sections: list[list[str]] = [
        [line] if (line := brief_line(project)) else [],
        turns,
        [STALLED.format(why=stalled)] if stalled else [],
        review_lines(project),
        ["Since the chat's last turn:", changed] if changed else [],
        timeline_lines(project, skip=changed),
    ]
    # Least needed last: drop from the end until it fits.
    while sections and len(_join(sections)) > limit:
        sections.pop()
    if not any(sections):
        return ""
    return _join(sections)


def _join(sections: list[list[str]]) -> str:
    return "\n".join([HEAD, *(line for section in sections for line in section)])


def for_new_chat(project: Path) -> str:
    """The digest a new chat's first request carries: what changed since the
    project's most recent chat last ran, and the rest of the digest."""
    from joinery import chats

    since = 0.0
    with contextlib.suppress(OSError, ValueError, KeyError, IndexError):
        latest = chats.listing(project)[:1]
        if latest:
            since = chats.turn_ended(chats.read(project, str(latest[0]["id"]))[0])
    return build(project, since=since)


# --- When a request starts fresh ------------------------------------------------------

#: ``[agents] fresh_at``: a session whose context holds at least this many tokens
#: is not continued for a new request; the request starts fresh from the digest.
#: 0 always continues (the behaviour before digests).
FRESH_AT = 60_000


def fresh_at(project: Path) -> int:
    """``[agents] fresh_at`` from joinery.toml (read each time), or ``FRESH_AT``."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return FRESH_AT
    agents = raw.get("agents", {})
    value = agents.get("fresh_at", FRESH_AT) if isinstance(agents, dict) else FRESH_AT
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return FRESH_AT
    return value


def starts_fresh(
    context: int, limit: int, *, follow_up: bool, stalled: bool, build: bool = False
) -> bool:
    """Whether a request starts a fresh session instead of continuing one whose
    context holds ``context`` tokens.

    A small session is continued: replaying it costs less than the digest saves. A
    big one starts fresh from the digest, unless the user marked the request a
    follow-up (``joinery run --chat``) of a turn that finished. A turn that stopped
    (a stalled worker, a usage limit, an idle stop) is never replayed at size: the
    next request carries on from the digest, which says what that turn left. The
    ``build`` of a planned to-do (Go) always starts fresh: the to-do is the plan."""
    if limit <= 0:
        return False
    if build:
        return True
    if context < limit:
        return False
    return stalled or not follow_up


def last_outcome(entries: list[dict[str, Any]]) -> str:
    """How the chat's last turn ended ("" when unknown or it has none)."""
    from joinery import chats

    found = chats.turns(entries)
    return str(found[-1]["outcome"]) if found else ""


def is_stalled(outcome: str) -> bool:
    """A turn that ended without finishing: stopped, failed or cancelled."""
    return outcome not in {"", "done"}
