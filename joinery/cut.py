"""`/cut`: one prioritised polish list, proposed cuts with reasons, and a ship estimate.

    joinery cut --project P                 # the polish list and the proposed cuts
    joinery cut --project P --json          # everything, for the sidebar's cut card
    joinery cut --project P --write [--cut ID ...] [--no-cuts] [--yes]
                                            # stage design/polish.md and the brief's
                                            # cuts as one change for review

It gathers what Joinery already knows about the game and asks nothing new of it:

- the design check (``joinery.design``, run now: it only reads the project);
- the newest playtest run (``.joinery/playtests``);
- the last accessibility and performance reports (``joinery.checks_core.last``);
- the console errors still open (``.joinery/errors.json``);
- what tidy finds unused (``joinery.tidy_report``, read-only);
- the brief's features and their marks (``joinery.brief_status``).

Every polish row says where it came from. Cuts are only ever *proposed*, each with its
reason: a feature that is broken, one promised but missing (scope), or one not built
yet that the worst findings point at (risk). A feature a Keep line covers is never
proposed. The ship estimate (a content freeze at 70% of the time left, and what is
still open) appears only when the brief's goal is to sell.

Writing makes one reviewed change and nothing else: ``design/polish.md``, and the chosen
cuts moved from the brief's Features to "Out of scope" with the day and the reason.
Nothing in the game is deleted. Removing content that is already built is its own
request with its own accept (/tidy, or asking Journeyman).
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from joinery import brief, brief_status, playtest_report
from joinery.brief import BRIEF_PATH, UNSET, KeepLine, words_of

POLISH_PATH = Path("design") / "polish.md"
Priority = Literal["high", "medium", "low"]
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
#: Within a priority, the sources a player meets first come first.
SOURCE_ORDER = ("errors", "playtest", "brief", "design", "access", "perf", "tidy")
SOURCE_WORDS = {
    "errors": "console errors",
    "playtest": "playtest",
    "brief": "brief features",
    "design": "design check",
    "access": "accessibility check",
    "perf": "performance check",
    "tidy": "tidy",
}
Reason = Literal["broken", "scope", "risk"]
REASON_WORDS = {
    "broken": "built but broken",
    "scope": "promised but not built",
    "risk": "not built yet, and the worst findings point at it",
}
#: Share of the time left before the content freeze (the rest is for polish).
FREEZE_SHARE = 0.7
#: Rows the plain report prints before "and N more".
SHOWN = 25
_CRITICAL = {"critical": "high", "high": "high", "medium": "medium", "low": "low"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Row(_Model):
    """One thing to polish, and where it came from."""

    id: str
    priority: Priority
    source: str
    title: str
    #: The place: a file and line, a node, a playtest moment.
    where: str = ""
    #: What the player meets, or the evidence.
    detail: str = ""


class Cut(_Model):
    """A feature the brief could drop, and why. Only ever proposed."""

    id: str
    text: str
    reason: Reason
    why: str
    #: Its line in design/brief.md.
    line: int = 0


class Ship(_Model):
    """The ship estimate: only for a game made to sell."""

    target: str = ""
    freeze: str = ""
    days_left: int = 0
    text: str


class Plan(_Model):
    project: str
    title: str
    headline: str
    rows: list[Row] = []
    cuts: list[Cut] = []
    #: Features a Keep line covers: never proposed for a cut.
    kept: list[str] = []
    ship: Ship | None = None
    #: What was read ("design check", "playtest 20261115-…").
    read: list[str] = []
    #: What couldn't be read, and why.
    skipped: list[str] = []
    #: The brief features' marks the plan used (`joinery brief --status`).
    features: list[dict[str, Any]] = []


# --- gathering ------------------------------------------------------------------------------


def _row(
    source: str, priority: str, title: str, where: str = "", detail: str = ""
) -> Row:
    level = _CRITICAL.get(priority, "medium")
    key = f"{source}\x00{title}\x00{where}"
    return Row(
        id=brief.feature_id(key),
        priority=level,  # type: ignore[arg-type]
        source=source,
        title=" ".join(title.split())[:160],
        where=where,
        detail=" ".join(detail.split())[:300],
    )


def _design(
    project: Path, rows: list[Row], read: list[str], skipped: list[str]
) -> None:
    from joinery import design

    try:
        report = design.check(project)
    except (OSError, ValueError) as exc:
        skipped.append(f"design check: {exc}")
        return
    read.append("design check")
    for finding in report.findings:
        where = finding.evidence[0].where() if finding.evidence else ""
        rows.append(
            _row("design", finding.severity, finding.title, where, finding.player)
        )


def _playtest(project: Path, rows: list[Row], read: list[str]) -> None:
    runs = brief_status._reports(project)
    if not runs:
        return
    _, run, report = runs[0]
    read.append(f"playtest {run.name}")
    for item in report.get("items", []):
        if not isinstance(item, dict):
            continue
        t = item.get("t")
        moment = (
            f" at {playtest_report.stamp(float(t))}"
            if isinstance(t, (int, float))
            else ""
        )
        where = str(item.get("where", "") or "")
        place = f"{where}{moment}".strip() or f"seed {report.get('seed', '')}"
        rows.append(
            _row(
                "playtest",
                str(item.get("severity", "medium")),
                str(item.get("title", "Problem")),
                place,
                str(item.get("detail", "")),
            )
        )


def _checks(project: Path, rows: list[Row], read: list[str]) -> None:
    from joinery import checks_core

    for kind in ("access", "perf"):
        report = checks_core.last(project, kind)
        if report is None:
            continue
        read.append(SOURCE_WORDS[kind])
        for finding in report.findings:
            where = finding.path + (f" ({finding.subject})" if finding.subject else "")
            rows.append(
                _row(kind, finding.severity, finding.message, where, finding.measured)
            )


def _errors(project: Path, rows: list[Row], read: list[str]) -> None:
    from joinery import console_errors as errors

    if not (project / errors.STATE).is_file():
        return
    try:
        book = errors.Book.load(project)
    except (OSError, ValueError):
        return
    read.append("console errors")
    open_states = {errors.OPEN, errors.QUEUED, errors.STOPPED, errors.FROM_CHANGE}
    for group in book.groups.values():
        if group.status not in open_states:
            continue
        times = f"seen {group.count} time{'s' if group.count != 1 else ''}"
        rows.append(
            _row(
                "errors", "high", group.label(), errors.short_where(group.where), times
            )
        )


def _tidy(project: Path, rows: list[Row], read: list[str], skipped: list[str]) -> None:
    from joinery import tidy_report

    if not (project / "project.godot").is_file():
        return
    try:
        report = tidy_report.check(project)
    except (OSError, ValueError) as exc:
        skipped.append(f"tidy: {exc}")
        return
    read.append("tidy")
    if report.count:
        rows.append(
            _row(
                "tidy",
                "low",
                f"Remove what the game never uses: {report.summary}",
                "",
                "/tidy takes it out as one reviewed change.",
            )
        )


def _features(marks: list[brief_status.Mark], rows: list[Row]) -> None:
    for mark in marks:
        if mark.status == "broken":
            rows.append(
                _row(
                    "brief",
                    "high",
                    f"Fix a broken feature: {mark.text}",
                    brief_status.check_label(mark),
                    mark.evidence,
                )
            )
        elif mark.status == "not_checked" and mark.where == "features":
            rows.append(
                _row(
                    "brief",
                    "low",
                    f"Nothing checks “{mark.text}”",
                    f"design/brief.md:{mark.line}",
                    "End the line with what checks it, so /cut can tell if it's built.",
                )
            )


def _sorted(rows: list[Row]) -> list[Row]:
    unique = list({row.id: row for row in rows}.values())
    return sorted(
        unique,
        key=lambda r: (
            PRIORITY_ORDER[r.priority],
            SOURCE_ORDER.index(r.source)
            if r.source in SOURCE_ORDER
            else len(SOURCE_ORDER),
        ),
    )


# --- cuts -----------------------------------------------------------------------------------


def covered_by_keep(text: str, keep: list[KeepLine]) -> KeepLine | None:
    """The Keep line that covers a feature: the two share a content word ("Jumping
    between roofs" and "Jump height 1.2 m"). Erring towards keeping is the point: a
    feature the user may love is never offered as a cut."""
    words = {_stem(w) for w in words_of(text)}
    if not words:
        return None
    for line in keep:
        if words & {
            _stem(w) for w in words_of(line.pin.name if line.pin else line.text)
        }:
            return line
    return None


def _stem(word: str) -> str:
    """'jumping' / 'jumped' / 'jumps' -> 'jump'."""
    for ending in ("ing", "ed", "es", "s"):
        if len(word) > len(ending) + 2 and word.endswith(ending):
            return word[: -len(ending)]
    return word


def _risky(mark: brief_status.Mark, rows: list[Row]) -> Row | None:
    """A high-priority finding whose words name every word of the feature."""
    words = set(words_of(mark.text))
    if not words:
        return None
    for row in rows:
        if row.priority != "high" or row.source == "brief":
            continue
        found = set(words_of(f"{row.title} {row.where} {row.detail}"))
        if words <= found:
            return row
    return None


def _cuts(
    marks: list[brief_status.Mark], keep: list[KeepLine], rows: list[Row]
) -> tuple[list[Cut], list[str]]:
    """Proposed cuts, and the features that would have been one but a Keep line
    covers (never proposed)."""
    cuts: list[Cut] = []
    kept: list[str] = []
    for mark in marks:
        if mark.where != "features" or mark.status == "built":
            continue
        reason: Reason | None = None
        why = mark.evidence
        if mark.status == "broken":
            reason = "broken"
        elif mark.status == "missing":
            reason = "scope"
        elif (row := _risky(mark, rows)) is not None:
            reason = "risk"
            why = f"{SOURCE_WORDS.get(row.source, row.source)}: {row.title}"
        if reason is None:
            continue
        if covered_by_keep(mark.text, keep) is not None:
            kept.append(mark.text)
            continue
        cuts.append(
            Cut(id=mark.id, text=mark.text, reason=reason, why=why, line=mark.line)
        )
    return cuts, kept


# --- the ship estimate ----------------------------------------------------------------------


def ship_estimate(
    facts: brief.BriefFacts | None, rows: list[Row], cuts: list[Cut], today: date
) -> Ship | None:
    """Only when the brief's goal is to sell: a freeze at 70% of the time left."""
    if facts is None or facts.goal != "sell":
        return None
    high = sum(1 for r in rows if r.priority == "high")
    broken = sum(1 for c in cuts if c.reason == "broken")
    still = f"{high} high-priority row{'s' if high != 1 else ''} and {broken} broken feature{'s' if broken != 1 else ''} are open."
    if not facts.target_date:
        return Ship(
            text="Set target_date in the brief's facts (YYYY-MM-DD) to get a freeze date. "
            + still
        )
    target = date.fromisoformat(facts.target_date)
    left = (target - today).days
    if left <= 0:
        return Ship(
            target=facts.target_date,
            days_left=left,
            text=f"The target date ({facts.target_date}) has passed. " + still,
        )
    freeze = today + timedelta(days=int(left * FREEZE_SHARE))
    return Ship(
        target=facts.target_date,
        freeze=freeze.isoformat(),
        days_left=left,
        text=(
            f"Freeze new content by {freeze.isoformat()} (70% of the {left} days left), "
            f"then polish until {facts.target_date}. " + still
        ),
    )


# --- the plan -------------------------------------------------------------------------------


def plan(project: Path, *, today: date | None = None) -> Plan:
    """Gather the findings, propose cuts and, for a game to sell, the ship estimate."""
    project = project.resolve()
    today = today or date.today()
    rows: list[Row] = []
    read: list[str] = []
    skipped: list[str] = []
    try:
        parsed: brief.Brief | None = brief.load_brief(project)
    except FileNotFoundError:
        parsed = None
        skipped.append(
            "brief: there is no design/brief.md, so no features or Keep lines"
        )
    marks = brief_status.marks(project, parsed) if parsed is not None else []
    if parsed is not None:
        read.append("brief features")
    _errors(project, rows, read)
    _playtest(project, rows, read)
    _features(marks, rows)
    _design(project, rows, read, skipped)
    _checks(project, rows, read)
    _tidy(project, rows, read, skipped)
    rows = _sorted(rows)
    cuts, kept = _cuts(marks, parsed.keep if parsed else [], rows)
    ship = ship_estimate(parsed.facts if parsed else None, rows, cuts, today)
    title = brief.project_name(project) or project.name
    return Plan(
        project=str(project),
        title=title,
        headline=headline(rows, cuts),
        rows=rows,
        cuts=cuts,
        kept=kept,
        ship=ship,
        read=read,
        skipped=skipped,
        features=[m.as_dict() for m in marks],
    )


def headline(rows: list[Row], cuts: list[Cut]) -> str:
    """'12 to polish · 3 high · 2 cuts proposed'; 'Nothing to polish'."""
    if not rows and not cuts:
        return "Nothing to polish"
    parts = [f"{len(rows)} to polish"] if rows else ["Nothing to polish"]
    high = sum(1 for r in rows if r.priority == "high")
    if high:
        parts.append(f"{high} high")
    if cuts:
        parts.append(f"{len(cuts)} cut{'s' if len(cuts) != 1 else ''} proposed")
    return " · ".join(parts)


# --- writing ---------------------------------------------------------------------------------


def polish_markdown(found: Plan, chosen: list[Cut], today: date) -> str:
    """design/polish.md: the rows by priority, each with its source, and the cuts."""
    lines = [
        f"# Polish list — {found.title}",
        "",
        f"Made with /cut on {today.isoformat()} from: "
        + (
            ", ".join(found.read)
            if found.read
            else "nothing (no findings were there to read)"
        )
        + ".",
        "Each row is a request you start; nothing here happens by itself.",
    ]
    for level in ("high", "medium", "low"):
        mine = [r for r in found.rows if r.priority == level]
        if not mine:
            continue
        lines += ["", f"## {level.capitalize()}", ""]
        for row in mine:
            where = f" — {row.where}" if row.where else ""
            detail = f": {row.detail}" if row.detail else ""
            lines.append(
                f"- [ ] {row.title}{where} ({SOURCE_WORDS.get(row.source, row.source)}){detail}"
            )
    if not found.rows:
        lines += ["", "Nothing to polish was found."]
    if chosen:
        lines += ["", "## Cut from the brief", ""]
        lines += [f"- {c.text}: {REASON_WORDS[c.reason]} ({c.why})" for c in chosen]
    if found.kept:
        lines += ["", "## Kept (never cut)", ""]
        lines += [f"- {text}: a Keep line covers it" for text in found.kept]
    if found.ship is not None:
        lines += ["", "## Ship estimate", "", found.ship.text]
    return "\n".join(lines).rstrip() + "\n"


_OUT_OF_SCOPE = re.compile(r"^(\s*[-*]\s+Out of scope:)[ \t]*(.*)$", re.MULTILINE)


def brief_with_cuts(text: str, chosen: list[Cut], today: date) -> str:
    """The brief with each chosen feature moved from Features to "Out of scope",
    with the day and the reason. Lines the brief no longer has are left alone."""
    if not chosen:
        return text
    parsed = brief.parse(text)
    by_id = {f.id: f for f in parsed.features if f.where == "features"}
    lines = text.split("\n")
    moved: list[str] = []
    for cut in chosen:
        feature = by_id.get(cut.id)
        if feature is None:
            continue
        lines[feature.line - 1] = "\x00"
        moved.append(
            f"  - {feature.text} (cut {today.isoformat()}: {REASON_WORDS[cut.reason]})"
        )
    if not moved:
        return text
    text = "\n".join(line for line in lines if line != "\x00")
    scope = re.search(
        r"^##\s+(?:\d+\.\s*)?Scope and milestones\s*$", text, re.MULTILINE
    )
    if scope is None:
        # No scope section: the moved lines go at the brief's end.
        return text.rstrip("\n") + "\n\n- Out of scope:\n" + "\n".join(moved) + "\n"
    following = re.compile(r"^##\s", re.MULTILINE).search(text, scope.end())
    end = following.start() if following else len(text)
    body = text[scope.end() : end]
    match = _OUT_OF_SCOPE.search(body)
    if match is not None:
        value = match.group(2).strip()
        head = match.group(1) + ("" if value in ("", UNSET) else " " + value)
        body = (
            body[: match.start()] + head + "\n" + "\n".join(moved) + body[match.end() :]
        )
    else:
        body = body.rstrip("\n") + "\n\n- Out of scope:\n" + "\n".join(moved) + "\n\n"
    return text[: scope.end()] + body + text[end:]


def choose(found: Plan, ids: list[str] | None) -> list[Cut]:
    """The cuts to write: all proposed (None), or those named. A cut that wasn't
    proposed (a Keep feature, a built one, a stale id) is refused in words."""
    if ids is None:
        return list(found.cuts)
    by_id = {c.id: c for c in found.cuts}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise ValueError(
            "Not a proposed cut (check again; Keep features are never cut): "
            + ", ".join(unknown)
        )
    return [by_id[i] for i in dict.fromkeys(ids)]


def apply(
    root: Path, found: Plan, chosen: list[Cut], today: date | None = None
) -> list[str]:
    """Write design/polish.md and the brief's cuts under ``root`` (a staged copy).
    Returns the paths written. Deletes nothing."""
    today = today or date.today()
    written = [POLISH_PATH.as_posix()]
    (root / POLISH_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / POLISH_PATH).write_text(
        polish_markdown(found, chosen, today), encoding="utf-8"
    )
    path = root / BRIEF_PATH
    if chosen and path.is_file():
        before = path.read_text(encoding="utf-8")
        after = brief_with_cuts(before, chosen, today)
        if after != before:
            path.write_text(after, encoding="utf-8")
            written.append(BRIEF_PATH.as_posix())
    return written


def summary(chosen: list[Cut]) -> str:
    if not chosen:
        return "Polish list"
    return f"Polish list and {len(chosen)} cut{'s' if len(chosen) != 1 else ''} from the brief"


def facts(found: Plan, chosen: list[Cut]) -> list[str]:
    """The review card's facts."""
    out = [f"{len(found.rows)} rows in design/polish.md"]
    if chosen:
        out.append("cuts " + ", ".join(c.text for c in chosen)[:60])
    return out


# --- `joinery cut` ---------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--write", action="store_true", help="stage the polish list and cuts for review"
    )
    parser.add_argument(
        "--cut",
        action="append",
        default=None,
        help="a proposed cut's id (default: all)",
    )
    parser.add_argument(
        "--no-cuts", action="store_true", help="write the polish list only"
    )
    parser.add_argument("--yes", action="store_true", help="accept the change at once")


def _out(args: argparse.Namespace, data: dict[str, Any], text: str) -> None:
    print(json.dumps(data) if args.json else text)


def text_report(found: Plan) -> str:
    lines = [f"{found.title}: {found.headline}."]
    for row in found.rows[:SHOWN]:
        where = f" — {row.where}" if row.where else ""
        lines.append(
            f"  {row.priority:<6} {row.title}{where} ({SOURCE_WORDS.get(row.source, row.source)})"
        )
    if len(found.rows) > SHOWN:
        lines.append(f"  … and {len(found.rows) - SHOWN} more (--json for all)")
    if found.cuts:
        lines.append("Proposed cuts (nothing is cut until you accept the change):")
        lines += [
            f"  {c.id}  {c.text}: {REASON_WORDS[c.reason]} ({c.why})"
            for c in found.cuts
        ]
    if found.kept:
        lines.append("Never cut (Keep): " + "; ".join(found.kept))
    if found.ship is not None:
        lines.append("Ship estimate: " + found.ship.text)
    if found.skipped:
        lines += [f"Skipped: {s}" for s in found.skipped]
    return "\n".join(lines)


def stage(project: Path, found: Plan, chosen: list[Cut]) -> Any:
    """The polish list and the cuts as one change waiting for review."""
    from joinery.transactions import FileTransaction

    tx = FileTransaction(project)
    tx.note_tool("joinery")
    tx.begin(summary(chosen))
    try:
        apply(tx.stage, found, chosen)
        tx.review()
    except BaseException:
        if tx.state in {"staging", "review"}:
            tx.reject()
        raise
    return tx


def main(args: argparse.Namespace, *, planner: Callable[[Path], Plan] = plan) -> int:
    project = args.project.resolve()
    if not (project / "project.godot").is_file():
        message = f"{project} is not a Godot project."
        _out(args, {"error": message}, message)
        return 1
    try:
        found = planner(project)
        if not args.write:
            _out(args, found.model_dump(), text_report(found))
            return 0
        chosen = [] if args.no_cuts else choose(found, args.cut)
        tx = stage(project, found, chosen)
        if args.yes:
            tx.accept()
    except (OSError, ValueError) as exc:
        _out(args, {"error": str(exc)}, f"cut: {exc}")
        return 1
    data = {
        "transaction_id": tx.id,
        "summary": tx.summary,
        "state": tx.state,
        "files": [c["path"] for c in tx.changes],
        "cuts": [c.id for c in chosen],
    }
    text = (
        f"{tx.summary}: staged design/polish.md"
        + (" and the brief's cuts" if chosen else "")
        + "."
    )
    if tx.state == "review":
        text += (
            "\nWaiting for your review. Accept with: joinery transactions accept "
            f"--project {project} --id {tx.id}"
        )
    _out(args, data, text)
    return 0
