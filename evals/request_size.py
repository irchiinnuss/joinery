"""How many tokens a run's requests send, before and after the request-size cuts.

    uv run python evals/request_size.py [--transcripts DIR] [--out FILE]

Deterministic and offline: no agent runs. It reads the Claude Code transcripts of
a recorded run (every model call's context size and every tool result), then
replays the same calls under each cut and sums the context sent:

* ``fresh``: a new request in a big session starts fresh from the digest
  (joinery.request_digest), unless it is a follow-up the user marked;
* ``resume``: a request that carries on a stopped turn starts from the digest;
* ``split``: a big request (a blockout, a to-do of three steps or more) is built
  in a fresh session that starts from the plan;
* ``trim``: tool results get their short forms (joinery.short_results), and a
  token not sent is not sent again on any later call of the session;
* ``light``: routine fixes after Joinery's checks move to the light model. It
  sends the same tokens; what moves is reported as main-model tokens.

A call's context after a cut is its recorded context less what the cuts removed
before it in its session. That keeps the calls themselves as recorded (their
number, their reads), so the numbers say what the context assembly saves, not what
a different agent run would have done.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from joinery import request_digest, routing, short_results

#: Run 3 of the Ironworks test (the 0.7.0 baseline).
DEFAULT = Path.home() / ".claude/projects/-Users-ninjacornix-ironworks3"
CUTS = ("fresh", "resume", "split", "trim", "light")
#: Characters per token for text the transcripts give as characters.
CHARS_PER_TOKEN = 4
#: The digest's size at most, in tokens.
DIGEST_TOKENS = request_digest.DIGEST_CHARS // CHARS_PER_TOKEN
#: A fresh fix's prompt beyond the digest: the request, the staged files, the problems.
FIX_TOKENS = 1500
#: A session whose first call is this big is a reviewer's (it starts from the change).
REVIEWER_START = 60_000

_CONTINUATION = "Fix every problem, then call submit_for_review"
_RESUME = re.compile(
    r"\b(please finish|carry on|running again|the run stopped)\b", re.IGNORECASE
)
_FOLLOW_UP = re.compile(r"^(yes|no|ok|okay|sure)\b|\bgo ahead\b", re.IGNORECASE)
_BIG = re.compile(r"\b(blockout|greybox|grey boxes|grey-box)\b", re.IGNORECASE)
_PROBLEM = re.compile(r"^- ([a-z_]+): ", re.MULTILINE)


@dataclass
class Result:
    """One tool result: after which call it arrived, and its characters."""

    after: int
    tool: str
    chars: int
    short_chars: int


@dataclass
class Turn:
    session: str
    request: str
    calls: list[int] = field(default_factory=list)
    results: list[Result] = field(default_factory=list)
    #: Calls after which the plan was made (propose_todo with 3+ steps, or the
    #: first blockout call), or -1.
    plan_at: int = -1
    plan_chars: int = 0
    kind: str = "fresh"
    checks: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """The request's own words: they end its text (after the project map)."""
        if self.kind in {"continuation", "reviewer"}:
            return self.request[:50]
        return "…" + self.request[-50:] if len(self.request) > 50 else self.request


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _request(text: str) -> str:
    """The user's words: after the last "User: " of the turn's text."""
    head, mark, words = text.rpartition("User: ")
    if not mark and "Joinery checked the change" in text:
        words = text[text.index("Joinery checked the change") :]
    return " ".join((words if mark or words else head).split())


def short_chars(text: str) -> int:
    """The characters of a recorded tool result once given its short form."""
    body, _, tail = text.partition("\nJoinery: ")
    try:
        value = json.loads(body)
    except ValueError:
        return len(text)
    short, cuts = short_results.shorten(value)
    if not cuts:
        return len(text)
    shortened = json.dumps(short, ensure_ascii=False, separators=(",", ":"))
    return min(len(text), len(shortened) + (len(tail) + 10 if tail else 0))


def read_session(path: Path) -> list[Turn]:
    """The turns of one transcript, each with its calls and tool results."""
    turns: list[Turn] = []
    seen: set[str] = set()
    uses: dict[str, tuple[str, dict[str, Any]]] = {}
    current: Turn | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") or {}
        content = message.get("content")
        if entry.get("type") == "user":
            results = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            )
            text = _text(content)
            if text and not results and not entry.get("isMeta"):
                current = Turn(session=path.stem[:8], request=_request(text))
                if _CONTINUATION in text:
                    current.kind = "continuation"
                    current.checks = _PROBLEM.findall(text)
                turns.append(current)
                continue
            if current is None or not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                name, _ = uses.get(str(block.get("tool_use_id", "")), ("?", {}))
                body = _text(block.get("content"))
                current.results.append(
                    Result(len(current.calls), name, len(body), short_chars(body))
                )
        elif entry.get("type") == "assistant" and current is not None:
            usage = message.get("usage")
            ident = str(message.get("id", ""))
            if usage and ident not in seen:
                seen.add(ident)
                current.calls.append(
                    int(usage.get("input_tokens", 0))
                    + int(usage.get("cache_read_input_tokens", 0))
                    + int(usage.get("cache_creation_input_tokens", 0))
                )
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = str(block.get("name", "")).split("__")[-1]
                given = block.get("input") or {}
                uses[str(block.get("id", ""))] = (name, given)
                if current.plan_at >= 0:
                    continue
                steps = given.get("steps") if isinstance(given, dict) else None
                if (
                    (
                        name == "propose_todo"
                        and isinstance(steps, list)
                        and len(steps) >= 3
                    )
                    or name == "blockout"
                    and _BIG.search(current.request)
                ):
                    current.plan_at = len(current.calls)
                    current.plan_chars = len(json.dumps(given))
    return [t for t in turns if t.calls]


def classify(sessions: list[list[Turn]]) -> None:
    """Each turn's kind: fresh, continued (a new request in an old session),
    follow_up, resume (carries on a stopped turn), continuation, reviewer."""
    for turns in sessions:
        if turns and turns[0].calls[0] >= REVIEWER_START:
            for turn in turns:
                turn.kind = "reviewer"
            continue
        for index, turn in enumerate(turns):
            if turn.kind == "continuation" or index == 0:
                continue
            earlier = turns[index - 1].request
            if _RESUME.search(turn.request) or turn.request == earlier:
                turn.kind = "resume"
            elif _FOLLOW_UP.search(turn.request):
                turn.kind = "follow_up"
            else:
                turn.kind = "continued"


def prefix(sessions: list[list[Turn]]) -> int:
    """A fresh request's first context: the stable prefix and the request."""
    starts = [t.calls[0] for s in sessions for t in s if t.kind == "fresh"]
    return int(statistics.median(starts)) if starts else 24_000


@dataclass
class Totals:
    tokens: list[int] = field(default_factory=list)
    light: list[int] = field(default_factory=list)
    first: list[int] = field(default_factory=list)


def replay(sessions: list[list[Turn]], cuts: set[str]) -> list[Totals]:
    """Per session, per turn: the context sent under ``cuts``."""
    start = prefix(sessions) + DIGEST_TOKENS
    out: list[Totals] = []
    for turns in sessions:
        totals = Totals()
        removed = 0  # Tokens of the recorded context the cuts took out so far.
        for turn in turns:
            # The same rule as the chat host: only a session at fresh_at or more.
            big = turn.calls[0] - removed >= request_digest.FRESH_AT
            fresh = big and (
                (turn.kind == "continued" and "fresh" in cuts)
                or (turn.kind == "resume" and "resume" in cuts)
            )
            if fresh:
                removed = max(0, turn.calls[0] - start)
            elif big and turn.kind == "continuation" and "fresh" in cuts:
                # The fix starts fresh with the staged files and the problems.
                removed = max(0, turn.calls[0] - (start + FIX_TOKENS))
            light = (
                "light" in cuts
                and turn.kind == "continuation"
                and routing.step_tier("fix", checks=turn.checks) == "light"
            )
            sent: list[int] = []
            for index, recorded in enumerate(turn.calls):
                if "split" in cuts and index == turn.plan_at and turn.plan_at > 0:
                    # The build starts fresh from the plan and the digest.
                    plan = turn.plan_chars // CHARS_PER_TOKEN
                    removed = max(removed, recorded - (start + plan))
                if "trim" in cuts:
                    removed += sum(
                        (r.chars - r.short_chars) // CHARS_PER_TOKEN
                        for r in turn.results
                        if r.after == index
                    )
                sent.append(max(start // 2, recorded - removed))
            total = sum(sent)
            totals.tokens.append(total)
            totals.light.append(total if light else 0)
            totals.first.append(sent[0])
        out.append(totals)
    return out


def report(directory: Path) -> str:
    files = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    sessions = [s for s in (read_session(p) for p in files) if s]
    classify(sessions)
    runs = {name: replay(sessions, set(cuts)) for name, cuts in _COLUMNS.items()}
    lines = [
        "# Request size: before and after the request-size cuts",
        "",
        (
            f"Source: {len(files)} Claude Code transcripts in `{directory}` "
            f"({sum(len(s) for s in sessions)} turns). Context per model call, summed "
            f"(cache reads included); fresh start = {prefix(sessions) // 1000} k prefix "
            f"+ {DIGEST_TOKENS} digest tokens (its ceiling). Light: tokens moved to the "
            "light model, not saved."
        ),
        "",
        "| Session | Turn | Kind | Calls | First k (before → all) | "
        + " | ".join(f"{name} M" for name in _COLUMNS)
        + " |",
        "|---|---|---|---|---|" + "---|" * len(_COLUMNS),
    ]
    sums = dict.fromkeys(_COLUMNS, 0)
    light = 0
    biggest = dict.fromkeys(_COLUMNS, 0)
    for s_index, turns in enumerate(sessions):
        for t_index, turn in enumerate(turns):
            cells = []
            for name in _COLUMNS:
                value = runs[name][s_index].tokens[t_index]
                sums[name] += value
                biggest[name] = max(biggest[name], value)
                cells.append(f"{value / 1e6:.2f}")
            light += runs["all"][s_index].light[t_index]
            first = (
                f"{turn.calls[0] // 1000} → "
                f"{runs['all'][s_index].first[t_index] // 1000}"
            )
            lines.append(
                f"| {turn.session} | {_cell(turn.label)} | {turn.kind} | "
                f"{len(turn.calls)} | {first} | " + " | ".join(cells) + " |"
            )
    lines.append(
        "| **all** | | | | | "
        + " | ".join(f"**{sums[name] / 1e6:.2f}**" for name in _COLUMNS)
        + " |"
    )
    lines += [
        "",
        "| Cut | Total M | Saved M | Saved % | Largest turn M |",
        "|---|---|---|---|---|",
    ]
    base = sums["before"]
    for name in _COLUMNS:
        saved = base - sums[name]
        lines.append(
            f"| {name} | {sums[name] / 1e6:.2f} | {saved / 1e6:.2f} | "
            f"{100 * saved / base:.0f}% | {biggest[name] / 1e6:.2f} |"
        )
    lines += [
        "",
        (
            f"Light model (routine fixes after checks, with `light_model` set): "
            f"{light / 1e6:.2f} M of the remaining {sums['all'] / 1e6:.2f} M move off "
            "the main model."
        ),
    ]
    return "\n".join(lines) + "\n"


_COLUMNS: dict[str, tuple[str, ...]] = {
    "before": (),
    "fresh": ("fresh",),
    "resume": ("resume",),
    "split": ("split",),
    "trim": ("trim",),
    "all": ("fresh", "resume", "split", "trim", "light"),
}


def _cell(text: str) -> str:
    return text.replace("|", "/")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--transcripts", type=Path, default=DEFAULT)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.transcripts.is_dir():
        print(f"No transcripts at {args.transcripts}.", file=sys.stderr)
        return 2
    text = report(args.transcripts)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
