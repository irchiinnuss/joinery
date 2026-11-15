"""The brief's features, each marked built, missing, broken or not checked from evidence.

Every line of the brief's Features section, and every milestone whose "done when" is
written, can end with what checks it (``joinery.brief`` parses it):

- ``(reach: Lighthouse)``: the playtest bot heads for the place in a seeded run;
- ``(no errors: 60 s)``: that run has no crash, freeze or script error;
- ``(test: res://tests/test_lamp.gd)``: the test runs headless and passes;
- ``(replay: first_storm)``: the replay in ``tests/replays/`` plays back the same.

The first two are the done-when checker's own (ADR 0060, ``joinery.done_when``): the
same seeded run, the same pass / fail / inconclusive and the same evidence words. A
pass is **built**, a fail **broken**, a place nothing in the game is named like or a
test or replay that isn't there **missing**; anything else, and every line that names
nothing to check, is **not checked**, with why. Nothing is ever guessed from a line's
words, and an agent's word never marks a line.

``marks`` reads what is already known and runs nothing: the last check of each line
(``.joinery/brief-status.json``) and any newer playtest run that reached the place or
played long enough. ``check`` gathers fresh evidence (plays the game, runs the tests
and replays) and keeps it. A line whose words change is a new line: not checked.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from joinery import brief, done_when, playtest, replays
from joinery.brief import FeatureLine

Status = Literal["built", "missing", "broken", "not_checked"]
STATUSES: tuple[Status, ...] = ("built", "broken", "missing", "not_checked")
WORDS: dict[str, str] = {
    "built": "built",
    "broken": "broken",
    "missing": "missing",
    "not_checked": "not checked",
}
STORE = Path(".joinery") / "brief-status.json"
NOTHING_TO_CHECK = (
    "Nothing checks this line yet. End it with what does: (reach: a place), "
    "(no errors: 60 s), (test: res://tests/…) or (replay: a name)."
)
#: Seconds one test may run before it counts as not finishing.
TEST_SECONDS = 120


@dataclass
class Mark:
    """One brief line and its status, with the evidence and when it was found."""

    id: str
    text: str
    where: str
    kind: str
    target: str
    status: Status
    evidence: str
    #: ISO time the evidence was found; "" when never checked.
    checked: str = ""
    #: Where the evidence came from: "playtest", "test", "replay"; "" for none.
    source: str = ""
    #: 1-based line in design/brief.md.
    line: int = 0

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["word"] = WORDS[self.status]
        data["when"] = when_words(self.checked)
        return data


def when_words(stamp: str) -> str:
    """'15 Nov 14:02' for an ISO time; '' for none."""
    if not stamp:
        return ""
    try:
        moment = datetime.fromisoformat(stamp).astimezone()
    except ValueError:
        return ""
    return f"{moment.day} {moment:%b %H:%M}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="seconds")


def _criterion(line: FeatureLine) -> done_when.Criterion:
    kind: Any = line.kind if line.kind in ("reach", "no_errors", "test") else "look"
    return done_when.Criterion(
        id=line.id, text=line.text, kind=kind, target=line.target, seconds=line.seconds
    )


def _from_result(line: FeatureLine, result: done_when.Result) -> tuple[Status, str]:
    """A done-when result as a brief mark: no new judgement, only new words."""
    if result.status == "pass":
        return "built", result.evidence
    if result.status == "fail":
        if "isn't in the change" in result.evidence:
            return "missing", f"{line.target} isn't in the project."
        return "broken", result.evidence
    if result.evidence.startswith("Nothing in the game is named like"):
        return "missing", result.evidence.replace(
            "the bot couldn't head for it", "so it isn't built"
        )
    return "not_checked", result.evidence


def _mark(line: FeatureLine, status: Status, evidence: str, **more: Any) -> Mark:
    return Mark(
        id=line.id,
        text=line.text,
        where=line.where,
        kind=line.kind,
        target=line.target,
        status=status,
        evidence=evidence,
        line=line.line,
        **more,
    )


# --- what is already known ------------------------------------------------------------------


def _store(project: Path) -> dict[str, Any]:
    try:
        data = json.loads((project / STORE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, dict) else {}


def save(project: Path, marks: list[Mark]) -> Path:
    """Keep each checked line's evidence; lines never checked are left out."""
    path = project / STORE
    path.parent.mkdir(parents=True, exist_ok=True)
    known = _store(project)
    for mark in marks:
        if mark.checked:
            known[mark.id] = {
                "status": mark.status,
                "evidence": mark.evidence,
                "checked": mark.checked,
                "source": mark.source,
                "text": mark.text,
            }
    path.write_text(json.dumps({"items": known}, indent=1), encoding="utf-8")
    return path


def _reports(project: Path) -> list[tuple[float, Path, dict[str, Any]]]:
    """Playtest reports, newest first: ``(finished, folder, report)``."""
    root = project / playtest.FOLDER
    found: list[tuple[float, Path, dict[str, Any]]] = []
    if not root.is_dir():
        return found
    for run in root.iterdir():
        path = run / "report.json"
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            when = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        if isinstance(report, dict):
            found.append((when, run, report))
    found.sort(key=lambda item: item[0], reverse=True)
    return found


def _covers(line: FeatureLine, report: dict[str, Any]) -> bool:
    """Whether a playtest run says anything about this line."""
    if line.kind == "reach":
        word = playtest.reach_words([line.target])
        return bool(word) and any(
            isinstance(p, dict) and p.get("target") == word[0]
            for p in report.get("reach", [])
        )
    played = float(report.get("played_seconds", 0) or 0)
    return line.kind == "no_errors" and played + 0.5 >= line.seconds


def _from_playtest(project: Path, line: FeatureLine, after: str) -> Mark | None:
    """The newest playtest run, newer than ``after``, that covers the line."""
    for when, run, report in _reports(project):
        stamp = _stamp(when)
        if after and stamp <= after:
            return None
        if not _covers(line, report):
            continue
        status, evidence = _from_result(
            line, done_when._played(_criterion(line), report, project, "")
        )
        return _mark(
            line,
            status,
            f"{evidence} (playtest {run.name})",
            checked=stamp,
            source="playtest",
        )
    return None


def _present(project: Path, line: FeatureLine) -> Mark | None:
    """A test or replay the line names that isn't in the project: missing, for sure."""
    if line.kind == "test":
        rel = line.target.removeprefix("res://")
        if rel and not (project / rel).is_file():
            return _mark(
                line,
                "missing",
                f"{line.target} isn't in the project.",
                checked=_now(),
                source="test",
            )
    if line.kind == "replay" and not _replay_path(project, line.target).is_file():
        return _mark(
            line,
            "missing",
            f"No replay named {line.target} in {replays.FOLDER.as_posix()}/.",
            checked=_now(),
            source="replay",
        )
    return None


def _replay_path(project: Path, name: str) -> Path:
    stem = name.removeprefix("res://").removesuffix(replays.SUFFIX)
    if "/" in stem:
        return project / (stem + replays.SUFFIX)
    return replays.folder(project) / (stem + replays.SUFFIX)


def marks(project: Path, parsed: brief.Brief | None = None) -> list[Mark]:
    """Every feature line with what is already known about it; runs nothing."""
    if parsed is None:
        try:
            parsed = brief.load_brief(project)
        except FileNotFoundError:
            return []
    known = _store(project)
    found: list[Mark] = []
    for line in parsed.features:
        if not line.kind:
            found.append(_mark(line, "not_checked", NOTHING_TO_CHECK))
            continue
        absent = _present(project, line)
        if absent is not None:
            found.append(absent)
            continue
        stored = known.get(line.id)
        after = str(stored.get("checked", "")) if isinstance(stored, dict) else ""
        fresher = (
            _from_playtest(project, line, after)
            if line.kind in ("reach", "no_errors")
            else None
        )
        if fresher is not None:
            found.append(fresher)
        elif isinstance(stored, dict) and stored.get("status") in WORDS:
            found.append(
                _mark(
                    line,
                    stored["status"],
                    str(stored.get("evidence", "")),
                    checked=after,
                    source=str(stored.get("source", "")),
                )
            )
        else:
            found.append(
                _mark(
                    line,
                    "not_checked",
                    "Not checked yet: Check now gathers the evidence.",
                )
            )
    return found


# --- fresh evidence ------------------------------------------------------------------------

Player = Callable[[playtest.Options], Awaitable[dict[str, Any]]]
TestRunner = Callable[[Path, str], tuple[bool, str]]
ReplayRunner = Callable[[Path, Path], replays.Outcome]


def _test_command(project: Path, rel: str, godot: Path) -> list[str]:
    from joinery.submission import GUT_RUNNER, gut_dirs, scene_test_args

    if any(rel.startswith(d.rstrip("/") + "/") for d in gut_dirs(project)):
        return [
            str(godot), "--headless", "--path", str(project),
            "-s", "res://" + GUT_RUNNER, "-gtest=res://" + rel, "-gexit",
        ]  # fmt: skip
    args = scene_test_args(project, rel) or []
    settings = str(project / ".joinery" / f"brief-test-{os.getpid()}.cfg")
    filled = [a.replace("<file>", settings) for a in args]
    at = filled.index("--") if "--" in filled else len(filled)
    return [
        str(godot), "--headless", "--path", str(project), *filled[:at],
        "-s", "res://" + rel, *filled[at:],
    ]  # fmt: skip


def run_test(project: Path, rel: str) -> tuple[bool, str]:
    """Run one test headless on this machine: passed, and the last of its output."""
    from joinery.submission import is_scene_tree_test

    path = project / rel
    gut = (project / "addons/gut/gut_cmdln.gd").is_file()
    if not is_scene_tree_test(path) and not gut:
        raise ValueError(
            f"res://{rel} isn't a test Joinery runs: a GUT test, or a script that "
            "extends SceneTree and quits with 0 on pass."
        )
    godot = playtest.godot_binary()
    try:
        done = subprocess.run(
            _test_command(project, rel, godot),
            capture_output=True,
            text=True,
            timeout=TEST_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"res://{rel} didn't finish in {TEST_SECONDS} s."
    output = done.stdout + done.stderr
    failed = (
        done.returncode != 0
        or "SCRIPT ERROR:" in output
        or re.search(r"^\s*FAIL(?:ED)?\b", output, re.MULTILINE) is not None
    )
    return not failed, output.strip()[-300:]


async def check(
    project: Path,
    *,
    play: Player | None = None,
    test: TestRunner | None = None,
    replay: ReplayRunner | None = None,
) -> list[Mark]:
    """Gather fresh evidence for every line that names a check, and keep it."""
    parsed = brief.load_brief(project)
    lines = [line for line in parsed.features if line.kind]
    todo = done_when.Todo(
        id="brief",
        request="The brief's features",
        steps=[],
        done_when=[
            _criterion(line) for line in lines if line.kind in ("reach", "no_errors")
        ],
    )
    played: dict[str, done_when.Result] = {}
    if todo.done_when:
        # One seeded run of the game as it is, exactly as a done-when check plays it.
        results = await done_when.verify(
            todo, project=project, stage=project, ran=[], skipped=[], play=play
        )
        played = {r.id: r for r in results}
    stamp = _now()
    fresh: dict[str, Mark] = {}
    for line in lines:
        absent = _present(project, line)
        if absent is not None:
            fresh[line.id] = absent
            continue
        if line.id in played:
            status, evidence = _from_result(line, played[line.id])
            source = "playtest"
        elif line.kind == "test":
            rel = line.target.removeprefix("res://")
            try:
                ok, output = await asyncio.to_thread(test or run_test, project, rel)
            except (OSError, ValueError, RuntimeError) as exc:
                status, evidence = "not_checked", str(exc)
            else:
                status = "built" if ok else "broken"
                evidence = (
                    f"{line.target} ran and passed."
                    if ok
                    else f"{line.target} failed: {output}"
                )
            source = "test"
        else:
            outcome = await asyncio.to_thread(
                replay or replays.run, project, _replay_path(project, line.target)
            )
            source = "replay"
            if outcome.ok:
                status, evidence = "built", outcome.line()
            elif outcome.status == "failed":
                status, evidence = "not_checked", outcome.line()
            else:
                status, evidence = "broken", outcome.line()
        fresh[line.id] = _mark(line, status, evidence, checked=stamp, source=source)
    save(project, list(fresh.values()))
    return [fresh.get(m.id, m) for m in marks(project, parsed)]


# --- words ---------------------------------------------------------------------------------


def counts(found: list[Mark]) -> dict[str, int]:
    return {status: sum(1 for m in found if m.status == status) for status in STATUSES}


def headline(found: list[Mark]) -> str:
    """'2 built · 1 broken · 3 not checked'; 'No features in the brief yet'."""
    if not found:
        return "No features in the brief yet"
    tally = counts(found)
    return " · ".join(f"{n} {WORDS[s]}" for s, n in tally.items() if n)


def report(project: Path, found: list[Mark]) -> dict[str, Any]:
    stamps = [m.checked for m in found if m.checked]
    return {
        "project": str(project),
        "headline": headline(found),
        "counts": counts(found),
        "checked": max(stamps) if stamps else "",
        "when": when_words(max(stamps)) if stamps else "",
        "features": [m.as_dict() for m in found],
    }


def lines(found: list[Mark]) -> list[str]:
    """The plain report, one line per feature."""
    out = [f"Brief features: {headline(found)}"]
    for mark in found:
        check_words = check_label(mark)
        when = f"checked {when_words(mark.checked)}" if mark.checked else ""
        detail = " · ".join(p for p in (check_words, when) if p)
        out.append(
            f"  {WORDS[mark.status]:<12} {mark.text}"
            + (f"  ({detail})" if detail else "")
        )
        out.append(f"               {mark.evidence}")
    return out


def check_label(mark: Mark) -> str:
    """'reach: Lighthouse', 'no errors', 'test: res://…'; '' when nothing checks it."""
    if mark.kind == "no_errors":
        return "no errors"
    return f"{mark.kind}: {mark.target}" if mark.kind else ""
