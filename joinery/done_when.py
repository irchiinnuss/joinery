"""A request's to-do: a few steps and "done when" items Joinery checks after the change.

For a request with several steps the agent proposes a short to-do with ``propose_todo``
(ADR 0060): the steps it will take and what "done" means, each "done when" item written so
something other than the agent can check it. The sidebar shows the list before the work
starts; the user edits or removes items and presses Go when the project asks for that
(``[agents] todo_go``: ``"auto"``, the default, waits for Go when there are two steps or
more, unless nobody is there to press it: the project keeps changes automatically
(ADR 0085) or the request came from ``joinery run``; ``"multi"`` waits for two steps or
more whatever; ``"always"``; ``"never"``). After the change passes Joinery's checks every item is
checked and the review shows Pass, Fail or Inconclusive with the evidence. A Fail goes back
to the agent once before the user sees the change.

Each step is a short title the card shows (a few words, "Mower: pull-cord handle and fuel
cap") and its detail, the names, sizes and files the build needs, behind the title. A step
the agent wrote as one long line keeps it all as its detail, under its first clause as the
title. A step said twice (the same or nearly the same title) is kept once, both when the
agent proposes the list and when the user edits it.

While the to-do is built the agent says as it starts, finishes or skips each step
(``todo_step``), and steps run as parallel tasks are marked as their tasks finish, so the
card and ``joinery run`` show real progress ("4 of 8 done").

The agent may say which steps need which (``after``): a step that needs nothing before it
is independent. With ``[agents] run_steps = "together"`` the harness runs the independent
steps as parallel tasks (ADR 0036) and comes back to the agent only at the end or when a
step fails (ADR 0072); ``"one_by_one"``, the default, leaves every step to the agent.

Kinds of "done when" item and how each is checked:

- ``reach`` (target: a place, named like its node): the playtest bot heads there in a seeded
  run of the changed game and says whether, and when, the player got there.
- ``no_errors`` (seconds, 10 to 300, default 60): the same run has no crash, freeze, script or
  engine error.
- ``test`` (target: a res:// test the agent wrote with the change): it ran with the
  project's tests in Joinery's checks, which pass before anything here runs. Two kinds
  run: GUT tests in the project's GUT folders, and SceneTree tests (a script under
  ``tests/`` that extends SceneTree and quits with 0 on pass; ``submission.scene_tree_tests``).
- ``check`` (target: one of Joinery's checks: tests, Godot import, art bible, references,
  provenance, dependencies): it ran on this change.
- ``look``: nothing checks it automatically; the user looks. Always Inconclusive.

Nothing here knows which provider runs the agent: the tool, the checks and the events are
the same for Codex, Claude Code, Gemini CLI and opencode.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from joinery import playtest, playtest_report

Kind = Literal["reach", "no_errors", "test", "check", "look"]
KINDS: tuple[str, ...] = ("reach", "no_errors", "test", "check", "look")
Status = Literal["pass", "fail", "inconclusive"]
#: ``[agents] todo_go``: when the agent waits for the user's Go.
GO_MODES = ("auto", "multi", "always", "never")
DEFAULT_GO = "auto"
#: ``[agents] run_steps``: whether independent steps run as parallel tasks.
RUN_STEPS = ("one_by_one", "together")
MAX_STEPS = 8
#: A step's title (the card's line) and its detail (what the build needs), in characters.
TITLE_CHARS = 80
DETAIL_CHARS = 600
#: A step's progress while the to-do is built ("" before it starts).
STEP_STATES = ("started", "done", "skipped")
MAX_DONE_WHEN = 6
#: The seed every check run plays with, so a result replays exactly.
SEED = 4242
DEFAULT_SECONDS = 60.0
#: The playtest's items that count as errors for ``no_errors``.
ERROR_KINDS = frozenset(
    {"crash", "freeze", "boot", "script_error", "shader_error", "error", "quit"}
)
#: ``check`` targets in the words the submit checks record them under.
CHECK_NAMES = {
    "tests": "tests",
    "godot_import": "Godot import",
    "import": "Godot import",
    "art_bible": "art bible",
    "references": "references",
    "provenance": "provenance",
    "dependencies": "dependencies",
}


@dataclass
class Criterion:
    """One "done when" item: words for the user and what checks it."""

    id: str
    text: str
    kind: Kind = "look"
    target: str = ""
    seconds: float = DEFAULT_SECONDS

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "text": self.text, "kind": self.kind}
        if self.target:
            data["target"] = self.target
        if self.kind == "no_errors":
            data["seconds"] = self.seconds
        return data


@dataclass
class Todo:
    """A request's to-do: its steps and its "done when" items."""

    id: str
    request: str
    #: Each step's title: the short line the card shows.
    steps: list[str]
    done_when: list[Criterion] = field(default_factory=list)
    #: For each step, the numbers (1-based) of the earlier steps it needs done first. A
    #: step the agent said nothing about needs the one before it.
    after: list[list[int]] = field(default_factory=list)
    #: Each step's detail (names, sizes, files the build needs); "" for none.
    details: list[str] = field(default_factory=list)
    #: Each step's progress while it is built: "", started, done or skipped.
    states: list[str] = field(default_factory=list)
    #: Each step's last word from the agent (``todo_step``'s note); "" for none.
    notes: list[str] = field(default_factory=list)

    def needs(self, index: int) -> list[int]:
        """The step numbers step ``index`` (0-based) needs first."""
        if index < len(self.after):
            return list(self.after[index])
        return [index] if index > 0 else []

    def detail(self, index: int) -> str:
        return self.details[index] if index < len(self.details) else ""

    def state(self, index: int) -> str:
        return self.states[index] if index < len(self.states) else ""

    def step_line(self, index: int) -> str:
        """The step as the build hears it: its title, then its detail."""
        title, detail = self.steps[index], self.detail(index)
        if not detail or _key(detail) == _key(title):
            return title
        if detail.startswith(title):
            return detail
        return f"{title}: {detail}"

    def mark(self, index: int, state: str) -> None:
        """Set step ``index`` (0-based) to ``state`` ("", started, done or skipped)."""
        self.states = [self.state(i) for i in range(len(self.steps))]
        self.states[index] = state

    def note(self, index: int, text: str = "") -> str:
        """Step ``index``'s note; with ``text``, keep that as its note first."""
        if text:
            self.notes = [self.note(i) for i in range(len(self.steps))]
            self.notes[index] = text
        return self.notes[index] if index < len(self.notes) else ""

    def finish(self) -> None:
        """The change is made: every step not skipped is done."""
        self.states = [
            "skipped" if self.state(i) == "skipped" else "done"
            for i in range(len(self.steps))
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "todo_id": self.id,
            "steps": list(self.steps),
            "details": [self.detail(i) for i in range(len(self.steps))],
            "after": [self.needs(i) for i in range(len(self.steps))],
            "step_states": [self.state(i) for i in range(len(self.steps))],
            "done_when": [c.as_dict() for c in self.done_when],
        }


def step_event(todo: Todo, number: int, note: str = "") -> dict[str, Any]:
    """The shared event for one step's progress, the same for every agent and host: the
    card moves step ``number`` (1-based) on and counts "4 of 8 done"; ``joinery run``
    prints a line (:func:`step_progress`). It carries no ``state``: the to-do's own
    state (going, steps, checking…) stays as it was."""
    event: dict[str, Any] = {
        "kind": "todo",
        "todo_id": todo.id,
        "step": number,
        "step_state": todo.state(number - 1),
        "step_states": [todo.state(i) for i in range(len(todo.steps))],
        "title": todo.steps[number - 1],
    }
    if note:
        event["note"] = note
    return event


def step_progress(event: dict[str, Any]) -> str:
    """A step event in words: 'Step 2 of 8 done: Open the gate'."""
    states = event.get("step_states") or []
    words = {"started": "started", "done": "done", "skipped": "skipped"}.get(
        str(event.get("step_state", "")), "to do again"
    )
    line = (
        f"Step {event.get('step')} of {len(states)} {words}: {event.get('title', '')}"
    )
    note = str(event.get("note", "") or "")
    return f"{line} ({note})" if note else line


@dataclass
class Result:
    """How one "done when" item came out, with the evidence."""

    id: str
    text: str
    status: Status
    evidence: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "status": self.status,
            "evidence": self.evidence,
        }


def _line(value: Any, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Each {what} is a short line of words.")
    text = " ".join(value.split())
    if len(text) > limit:
        raise ValueError(f"Keep each {what} under {limit} characters.")
    return text


#: Where a long step's first clause ends: a colon, semicolon, dash or bracket, a comma
#: or a full stop.
_CLAUSE = re.compile(r":\s|;|\s[-–—]\s|\s\(|,\s|\.\s")
#: A title longer than this many words is cut there.
TITLE_WORDS = 8


def short_title(text: str) -> str:
    """A step's title from a long line: its first clause of three words or more, at most
    ``TITLE_WORDS`` words. A line short enough already is its own title."""
    words = " ".join(text.split())
    if len(words.split()) <= TITLE_WORDS + 2 and len(words) <= 60:
        return words
    head = words
    for match in _CLAUSE.finditer(words):
        if len(words[: match.start()].split()) >= 3:
            head = words[: match.start()]
            break
    cut = head.split()
    if len(cut) > TITLE_WORDS:
        head = " ".join(cut[:TITLE_WORDS]).rstrip(",;:") + "…"
    return head.rstrip(".,;: ")[:TITLE_CHARS]


#: Words that don't change what a step says: "by the gate" is "beside the gate".
_GLUE = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "beside",
        "next",
        "near",
        "for",
        "with",
        "its",
        "it",
    ]
)


def _key(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def same_step(first: str, second: str) -> bool:
    """Whether two step titles say the same thing: equal once case and punctuation
    are set aside, or nearly so ("Put a lever by the gate", "Put a lever beside the
    gate"). Titles that differ in a number ("Room 1", "Room 2") never are."""
    a, b = _key(first), _key(second)
    if not a or not b:
        return a == b
    if a == b:
        return True
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return False
    words_a, words_b = set(a.split()) - _GLUE, set(b.split()) - _GLUE
    if words_a and words_a == words_b:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.92


def _detail(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError("A step's detail is text.")
    lines = [" ".join(line.split()) for line in value.strip().splitlines()]
    text = "\n".join(line for line in lines if line)
    if len(text) > DETAIL_CHARS:
        raise ValueError(f"Keep each step's detail under {DETAIL_CHARS} characters.")
    return text


def _step(value: Any) -> tuple[str, str]:
    """A proposed step: {title, detail?}, or one line of words (its first clause becomes
    the title, the whole line the detail). A title that runs long is shortened the same
    way, with the whole of it kept in the detail."""
    if isinstance(value, dict):
        title = _line(value.get("title"), "step title", DETAIL_CHARS)
        detail = _detail(value.get("detail"))
    else:
        title, detail = _line(value, "step", DETAIL_CHARS), ""
    short = short_title(title)
    if short != title:
        detail = (
            f"{title}\n{detail}"
            if detail and _key(title) not in _key(detail)
            else (detail or title)
        )
    return short, detail


def _distinct(titles: list[str]) -> tuple[dict[int, int], list[int]]:
    """For each step (0-based), where it lands once steps said twice are kept once (a
    repeat lands on the first step that says the same), and the steps kept."""
    into: dict[int, int] = {}
    kept: list[int] = []
    for index, title in enumerate(titles):
        same = next((into[j] for j in kept if same_step(titles[j], title)), None)
        if same is None:
            into[index] = len(kept)
            kept.append(index)
        else:
            into[index] = same
    return into, kept


def _kept(
    titles: list[str], details: list[str], after: list[list[int]]
) -> tuple[list[str], list[str], list[list[int]]]:
    """The steps with repeats folded into the first that says the same (its detail kept,
    or the repeat's when it had none), and ``after`` renumbered to match."""
    into, first = _distinct(titles)
    kept_titles = [titles[i] for i in first]
    kept_details = [details[i] for i in first]
    for index in range(len(titles)):
        if index not in first and not kept_details[into[index]]:
            kept_details[into[index]] = details[index]
    kept_after = [
        sorted({into[n - 1] + 1 for n in after[i] if into[n - 1] < into[i]})
        for i in first
    ]
    return kept_titles, kept_details, kept_after


def proposal(params: dict[str, Any], request: str = "") -> Todo:
    """The agent's ``propose_todo`` arguments, checked: {steps, done_when, after?}. Each
    step is {title, detail?} or one line of words; a step said twice is kept once."""
    steps = params.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"steps is a list of 1 to {MAX_STEPS} steps.")
    items = params.get("done_when") or []
    if not isinstance(items, list) or len(items) > MAX_DONE_WHEN:
        raise ValueError(f"done_when is a list of up to {MAX_DONE_WHEN} items.")
    criteria: list[Criterion] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise TypeError("Each done_when item is {text, kind, target?, seconds?}.")
        kind = item.get("kind", "look")
        if kind not in KINDS:
            raise ValueError(f"A done_when kind is one of {', '.join(KINDS)}.")
        target = item.get("target") or ""
        if not isinstance(target, str) or len(target) > 200:
            raise ValueError("A done_when target is a short name or res:// path.")
        target = target.strip()
        if kind in {"reach", "test", "check"} and not target:
            raise ValueError(
                f"A {kind} item names its target (a place, a res:// test or a check)."
            )
        if kind == "test" and not target.startswith("res://"):
            raise ValueError("A test item's target is the test's res:// path.")
        if kind == "check" and target.lower().replace(" ", "_") not in CHECK_NAMES:
            raise ValueError(
                "A check item names one of: " + ", ".join(CHECK_NAMES) + "."
            )
        seconds = item.get("seconds", DEFAULT_SECONDS)
        if (
            not isinstance(seconds, (int, float))
            or isinstance(seconds, bool)
            or not 10 <= seconds <= 300
        ):
            raise ValueError("seconds is between 10 and 300.")
        criteria.append(
            Criterion(
                id=f"d{index + 1}",
                text=_line(item.get("text"), "done_when text", 160),
                kind=kind,
                target=target,
                seconds=float(seconds),
            )
        )
    shaped = [_step(step) for step in steps]
    titles, details, after = _kept(
        [title for title, _ in shaped],
        [detail for _, detail in shaped],
        _after(params.get("after"), len(steps)),
    )
    return Todo(
        id=uuid.uuid4().hex[:12],
        request=request.strip()[:4000],
        steps=titles,
        done_when=criteria,
        after=after,
        details=details,
    )


def _after(value: Any, count: int) -> list[list[int]]:
    """``after``: for each step, the earlier step numbers it needs (``[]`` for none).
    Omitted, each step needs the one before it. Only earlier steps may be named, so the
    order can never loop."""
    if value is None:
        return [[n] if n else [] for n in range(count)]
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(
            "after has one list per step: the earlier step numbers it needs."
        )
    after: list[list[int]] = []
    for number, needs in enumerate(value, 1):
        if not isinstance(needs, list) or any(
            not isinstance(n, int) or isinstance(n, bool) or not 1 <= n < number
            for n in needs
        ):
            raise ValueError(
                f"after[{number - 1}] names earlier step numbers only (1 to {number - 1})."
            )
        after.append(sorted(set(needs)))
    return after


def edited(todo: Todo, data: dict[str, Any]) -> Todo:
    """The to-do as the user left it on the card: steps reworded or removed, "done when"
    items reworded or removed by id, steps added or moved. Kinds and targets stay the
    agent's. A step is {title, detail?, from?} (``from``: the agent's step number it
    was, so what it needs follows it) or a line of words; a step said twice is kept
    once."""
    steps = data.get("steps")
    if steps is None:
        steps = [
            {"title": t, "detail": todo.detail(i), "from": i + 1}
            for i, t in enumerate(todo.steps)
        ]
    if not isinstance(steps, list):
        raise TypeError("The to-do's steps are a list.")
    titles, details, origins = _edited_steps(todo, steps)
    if not titles:
        raise ValueError("Keep at least one step, or cancel the to-do.")
    into, first = _distinct(titles)
    first = first[:MAX_STEPS]
    where = {origins[i]: p for p, i in enumerate(first) if origins[i]}
    after: list[list[int]] = []
    for p, i in enumerate(first):
        needs: set[int] = set()
        if not origins[i]:
            # A step the user added needs the one before it.
            needs = {p} if p else set()
        for n in todo.needs(origins[i] - 1) if origins[i] else []:
            if n in where and where[n] < p:
                needs.add(where[n] + 1)
            elif p:
                # What it needed went or moved below it: it needs the one before.
                needs.add(p)
        after.append(sorted(needs))
    kept_details = [details[i] for i in first]
    for index, lands in into.items():
        if lands < len(first) and not kept_details[lands]:
            kept_details[lands] = details[index]
    by_id = {c.id: c for c in todo.done_when}
    items = data.get("done_when", [c.as_dict() for c in todo.done_when])
    if not isinstance(items, list):
        raise TypeError("The to-do's done-when items are a list.")
    kept: list[Criterion] = []
    for item in items:
        if not isinstance(item, dict) or str(item.get("id", "")) not in by_id:
            continue
        text = " ".join(str(item.get("text", "")).split())[:160]
        if text:
            kept.append(replace(by_id[str(item["id"])], text=text))
    return replace(
        todo,
        steps=[titles[i] for i in first],
        details=kept_details,
        done_when=kept,
        after=after,
        states=[],
    )


def _edited_steps(
    todo: Todo, steps: list[Any]
) -> tuple[list[str], list[str], list[int]]:
    """The card's steps as titles, details and the agent's step number each was (0 for
    one the user added). A line of words that matches one of the agent's titles keeps
    that step's detail; when the lines are as many as the agent's steps, a reworded
    line is the step in its place."""
    titles: list[str] = []
    details: list[str] = []
    origins: list[int] = []
    used: set[int] = set()
    lines = [s for s in steps if not isinstance(s, dict) and str(s).strip()]
    in_place = len(lines) == len(steps) == len(todo.steps)
    for position, step in enumerate(steps):
        if isinstance(step, dict):
            text = " ".join(str(step.get("title", "") or "").split())
            detail = "\n".join(
                " ".join(line.split())
                for line in str(step.get("detail", "") or "").splitlines()
                if line.strip()
            )[:DETAIL_CHARS]
            origin = step.get("from")
            number = (
                origin
                if isinstance(origin, int)
                and not isinstance(origin, bool)
                and 1 <= origin <= len(todo.steps)
                and origin not in used
                else 0
            )
        else:
            text, detail = " ".join(str(step).split()), ""
            number = next(
                (
                    i + 1
                    for i, title in enumerate(todo.steps)
                    if i + 1 not in used and _key(title) == _key(text)
                ),
                0,
            )
            if not number and in_place and position + 1 not in used:
                number = position + 1
            detail = todo.detail(number - 1) if number else ""
        if not text:
            continue
        if len(text) > TITLE_CHARS:
            short = short_title(text)
            detail = detail or text
            text = short
        used.add(number)
        titles.append(text)
        details.append(detail)
        origins.append(number)
    return titles, details, origins


def go_mode(project: Path) -> str:
    """``[agents] todo_go``: "auto" (the default), "multi", "always" or "never"."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_GO
    agents = raw.get("agents", {})
    value = agents.get("todo_go", DEFAULT_GO) if isinstance(agents, dict) else ""
    return value if value in GO_MODES else DEFAULT_GO


#: ``joinery todo-go --set``: the modes, and "ask" for "multi".
GO_CHOICES = {mode: mode for mode in GO_MODES} | {"ask": "multi"}
GO_WORDS = {
    "auto": "A to-do of two steps or more waits for your Go, unless changes are kept "
    "automatically or the request came from joinery run (nobody there to say Go).",
    "multi": "A to-do of two steps or more waits for your Go.",
    "always": "Every to-do waits for your Go.",
    "never": "To-dos start at once; nobody needs to say Go.",
}


def set_go_mode(project: Path, choice: str) -> str:
    """Save ``[agents] todo_go`` (``auto``, ``multi`` or ``ask``, ``always``,
    ``never``), keeping every other line of joinery.toml. Returns the mode saved."""
    from joinery.roles import set_agents_values

    mode = GO_CHOICES.get(choice)
    if mode is None:
        raise ValueError("Choose auto, multi, always or never.")
    set_agents_values(project, {"todo_go": json.dumps(mode)})
    if go_mode(project) != mode:
        raise ValueError("joinery.toml keeps [agents] in a form Joinery can't edit.")
    return mode


def add_go_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--set", choices=sorted(GO_CHOICES), default=None)
    parser.add_argument("--json", action="store_true")


def go_main(args: argparse.Namespace) -> int:
    """``joinery todo-go``: show or change when a to-do waits for the user's Go."""
    project = Path(args.project).resolve()
    try:
        mode = set_go_mode(project, args.set) if args.set else go_mode(project)
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"todo_go": mode, "choices": list(GO_MODES)}))
    else:
        print(GO_WORDS[mode])
    return 0


def run_steps_mode(project: Path) -> str:
    """``[agents] run_steps``: "one_by_one" (the default) or "together"."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "one_by_one"
    agents = raw.get("agents", {})
    value = agents.get("run_steps", "one_by_one") if isinstance(agents, dict) else ""
    return value if value in RUN_STEPS else "one_by_one"


def independent(todo: Todo) -> list[int]:
    """The steps (0-based) that need nothing first, so they can start together."""
    return [i for i in range(len(todo.steps)) if not todo.needs(i)]


def runs_together(todo: Todo, mode: str) -> list[int]:
    """The steps the harness runs as parallel tasks: the independent ones, when the
    project asks for that and there are two or more; otherwise none."""
    ready = independent(todo)
    return ready if mode == "together" and len(ready) >= 2 else []


def step_words(numbers: list[int]) -> str:
    """'step 2', 'steps 1 and 3', 'steps 1, 2 and 4' (1-based numbers)."""
    if not numbers:
        return "no steps"
    if len(numbers) == 1:
        return f"step {numbers[0]}"
    head = ", ".join(str(n) for n in numbers[:-1])
    return f"steps {head} and {numbers[-1]}"


#: ``[agents] split_requests``: "big" (the default) builds a big request's to-do in a
#: fresh session after the planning one; "never" builds it where it was planned.
SPLIT_MODES = ("big", "never")
#: A to-do of at least this many steps is a big request.
SPLIT_STEPS = 3
#: Requests that are big whatever their step count: a level's whole layout.
_BIG_REQUEST = re.compile(
    r"\b(blockout|greybox|grey-box|gray-box|block out)\b", re.IGNORECASE
)


def split_mode(project: Path) -> str:
    """``[agents] split_requests``: "big" (the default) or "never"."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "big"
    agents = raw.get("agents", {})
    value = agents.get("split_requests", "big") if isinstance(agents, dict) else ""
    return value if value in SPLIT_MODES else "big"


def splits(todo: Todo, mode: str) -> bool:
    """Whether the to-do is built in a step of its own (ADR 0090): a fresh session
    starts from the plan and a short digest, so the reads and searches of the
    planning don't ride along on every call of the build. A blockout, or a to-do of
    ``SPLIT_STEPS`` steps or more."""
    if mode != "big":
        return False
    return len(todo.steps) >= SPLIT_STEPS or bool(_BIG_REQUEST.search(todo.request))


def waits_for_go(todo: Todo, mode: str, *, unattended: bool = False) -> bool:
    """Whether the agent waits for the user's Go before changing anything.
    ``unattended``: nobody waits to press Go (the project keeps changes automatically,
    or ``joinery run``), so "auto" carries on; the other modes are the project's own
    choice and hold either way."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    if mode == "auto" and unattended:
        return False
    return len(todo.steps) >= 2


#: The build's one line on progress: the card checks each step off as it is done.
PROGRESS_LINE = (
    "Call todo_step(step, 'started') as you begin each step and todo_step(step, 'done') "
    "(or 'skipped', with a note) when it is finished, so the user sees progress. "
)


def _numbered(todo: Todo, indexes: Iterable[int]) -> str:
    """The steps at ``indexes`` (0-based) as the build hears them, numbered."""
    return "\n".join(f"{i + 1}. {todo.step_line(i)}" for i in indexes)


def go_prompt(todo: Todo) -> str:
    """What the agent hears when the user presses Go: the to-do as the user left it."""
    steps = _numbered(todo, range(len(todo.steps)))
    done = "\n".join(f"- {c.text}" for c in todo.done_when) or "- (none)"
    return (
        "The user pressed Go on your to-do, as they left it (they may have edited or "
        "removed items):\n"
        f"Steps:\n{steps}\nDone when:\n{done}\n"
        "Work through the steps now as one change and submit it with submit_for_review. "
        f"{PROGRESS_LINE}"
        "Joinery checks each done-when item after the change passes its checks; don't "
        "propose the to-do again.\n\n"
        f"The request was: {todo.request}"
    )


def build_prompt(todo: Todo) -> str:
    """What the build step of a planned request hears (ADR 0090): its own plan, in a
    fresh session that didn't see the planning."""
    steps = _numbered(todo, range(len(todo.steps)))
    done = "\n".join(f"- {c.text}" for c in todo.done_when) or "- (none)"
    return (
        "You planned this request in an earlier session; build it now from the plan. "
        "Read only what a step needs.\n"
        f"Steps:\n{steps}\nDone when:\n{done}\n"
        "Work through the steps as one change and submit it with submit_for_review. "
        f"{PROGRESS_LINE}"
        "Joinery checks each done-when item after the change passes its checks; don't "
        "propose the to-do again.\n\n"
        f"The request was: {todo.request}"
    )


#: A to-do of at least this many steps, run one by one, is built a step per agent
#: prompt: each step after the first starts in a fresh session once the session
#: has grown past ``[agents] fresh_at`` (ADR 0090), so the reads and tool results
#: of the steps before it don't ride along on every call of the ones after.
STEPWISE_STEPS = 2


def stepwise(todo: Todo, mode: str) -> bool:
    """Whether the to-do is built a step per prompt (``run_steps = "one_by_one"``)."""
    return mode == "one_by_one" and len(todo.steps) >= STEPWISE_STEPS


#: What the agent hears about the step's end, when more steps follow.
_STEP_END = (
    "Do this step only, on top of the change staged so far; read only what it needs. "
    "Call todo_step({n}, 'started') as you begin and todo_step({n}, 'done') with a "
    "note when it is finished (or 'skipped', with why). The note is what the next "
    "steps hear of this one, so name what you made: files, nodes, functions. Don't "
    "submit yet and don't start the next step: end your turn with one short line on "
    "what you did, and Joinery hands you step {next}."
)
#: ... and when it is the last.
_LAST_END = (
    "Do this step on top of the change staged so far; read only what it needs. Call "
    "todo_step({n}, 'started') as you begin and todo_step({n}, 'done') when it is "
    "finished. Then submit the whole change (every step) with submit_for_review. "
    "Joinery checks each done-when item after the change passes its checks; don't "
    "propose the to-do again."
)


def done_lines(todo: Todo, upto: int) -> str:
    """The steps before ``upto`` (0-based) as the next step hears them: each step's
    title and what its note said was made."""
    lines = []
    for index in range(upto):
        state = todo.state(index) or "done"
        note = todo.note(index)
        lines.append(
            f"{index + 1}. {todo.steps[index]} ({state})"
            + (f": {note}" if note else "")
        )
    return "\n".join(lines)


def step_prompt(
    todo: Todo, index: int, *, first: bool = False, planned: bool = False
) -> str:
    """What the agent hears for step ``index`` (0-based) of a to-do built a step per
    prompt. ``first``: the user just pressed Go, or (``planned``) the build of a
    plan made in an earlier session starts."""
    number = index + 1
    count = len(todo.steps)
    last = number == count
    done = "\n".join(f"- {c.text}" for c in todo.done_when) or "- (none)"
    plan = "\n".join(f"{i + 1}. {todo.steps[i]}" for i in range(count))
    parts = [
        "You planned this request in an earlier session; build it now from the plan. "
        "Joinery hands you its steps one at a time."
        if first and planned
        else "The user pressed Go on your to-do, as they left it (they may have edited "
        "or removed items). Joinery hands you its steps one at a time."
        if first
        else f"Step {number} of {count} of your to-do, the change still staged.",
        f"The whole to-do, for context:\n{plan}\nDone when (checked at the end):\n{done}",
    ]
    if index:
        parts.append(f"Done so far:\n{done_lines(todo, index)}")
    parts.append(f"Now step {number} of {count}: {todo.step_line(index)}")
    parts.append(
        _LAST_END.format(n=number)
        if last
        else _STEP_END.format(n=number, next=number + 1)
    )
    parts.append(f"The request was: {todo.request}")
    return "\n\n".join(parts)


@dataclass
class StepOutcome:
    """How one step run as a parallel task came out (``run_steps = "together"``)."""

    number: int
    text: str
    #: The task's state: in_review, accepted, discarded, done, failed or stopped.
    state: str
    message: str = ""
    task_id: str = ""
    seconds: float = 0.0
    #: When the step's task started (Unix seconds; 0 when unknown).
    started: float = 0.0

    @property
    def made(self) -> bool:
        """The step's change exists (waiting or accepted), or nothing needed changing."""
        return self.state in {"in_review", "accepted", "done"}

    def line(self) -> str:
        words = {
            "in_review": "made, waiting for the user's review",
            "accepted": "made and accepted",
            "done": "nothing needed changing",
            "discarded": "made, but the user discarded it",
            "failed": "failed",
            "stopped": "stopped",
        }.get(self.state, self.state)
        why = f": {self.message}" if self.message and not self.made else ""
        return f"- Step {self.number} ({self.text}): {words}{why}"


def remaining(todo: Todo, outcomes: list[StepOutcome]) -> list[int]:
    """The steps (0-based) left for the agent: those no task made."""
    made = {o.number - 1 for o in outcomes if o.made}
    return [i for i in range(len(todo.steps)) if i not in made]


def steps_prompt(todo: Todo, outcomes: list[StepOutcome]) -> str:
    """What the agent hears after the independent steps ran as parallel tasks."""
    ran = step_words([o.number for o in outcomes])
    left = remaining(todo, outcomes)
    steps = _numbered(todo, left) or "(none)"
    done = "\n".join(f"- {c.text}" for c in todo.done_when) or "- (none)"
    return (
        f"Joinery ran {ran} of your to-do as parallel tasks, each its own change the "
        "user reviews on its own:\n"
        + "\n".join(o.line() for o in outcomes)
        + "\nDo the steps left now as one change, on top of what was accepted, and "
        "submit it with submit_for_review. Don't redo a step that was made. If a step "
        "failed, make it yourself if you can; otherwise tell the user plainly why not. "
        f"{PROGRESS_LINE}\n"
        f"Steps left:\n{steps}\nDone when:\n{done}\n\n"
        f"The request was: {todo.request}"
    )


Player = Callable[[playtest.Options], Awaitable[dict[str, Any]]]


async def _play(options: playtest.Options) -> dict[str, Any]:
    # Cancelling the awaiting task (a Stop) stops the game too.
    return await playtest.run_async(options)


async def verify(
    todo: Todo,
    *,
    project: Path,
    stage: Path | None,
    ran: list[str],
    skipped: list[str],
    play: Player | None = None,
    failing: list[str] | None = None,
) -> list[Result]:
    """Check every "done when" item of ``todo`` against the change.

    ``stage`` is the change as files (None for a live edit alone, which the playtest can't
    see until it is accepted); ``ran`` and ``skipped`` are the submit checks' lists,
    ``failing`` the tests they found already failing before the change. Every
    item that plays shares one seeded run of the stage, as long as the longest asks.
    """
    play = play or _play
    playing = [c for c in todo.done_when if c.kind in {"reach", "no_errors"}]
    report: dict[str, Any] | None = None
    failure = ""
    if playing and stage is not None and (stage / "project.godot").is_file():
        # As long as the longest no_errors asks; a place to reach gets a minute at least.
        seconds = max(
            [c.seconds for c in playing if c.kind == "no_errors"]
            + [DEFAULT_SECONDS for c in playing if c.kind == "reach"]
        )
        try:
            report = await play(
                playtest.Options(
                    project=stage,
                    seconds=seconds,
                    seed=SEED,
                    reach=[c.target for c in playing if c.kind == "reach"],
                    out=playtest_folder(project),
                )
            )
        except (OSError, ValueError, RuntimeError) as exc:
            failure = str(exc) or "the playtest didn't run"
    results: list[Result] = []
    for criterion in todo.done_when:
        if criterion.kind in {"reach", "no_errors"}:
            results.append(_played(criterion, report, stage, failure))
        elif criterion.kind == "test":
            results.append(_test(criterion, stage, ran, skipped, failing or []))
        elif criterion.kind == "check":
            results.append(_check(criterion, ran, skipped))
        else:
            results.append(
                Result(
                    criterion.id,
                    criterion.text,
                    "inconclusive",
                    "Nothing checks this by itself; look for yourself.",
                )
            )
    return results


def playtest_folder(project: Path) -> Path:
    """A playtest run's folder in the project's own ``.joinery/playtests``."""
    return playtest._run_dir(project)


def _replay(report: dict[str, Any]) -> str:
    return f"seed {report.get('seed', SEED)}"


def _played(
    criterion: Criterion,
    report: dict[str, Any] | None,
    stage: Path | None,
    failure: str,
) -> Result:
    def result(status: Status, evidence: str) -> Result:
        return Result(criterion.id, criterion.text, status, evidence)

    if stage is None:
        return result(
            "inconclusive",
            "The change is a live preview in the editor, so the game can't be played "
            "with it yet. Play it after you accept it.",
        )
    if report is None:
        return result("inconclusive", f"The game couldn't be played: {failure}.")
    played = float(report.get("played_seconds", 0) or 0)
    clock = playtest_report.stamp(played)
    if criterion.kind == "no_errors":
        errors = [
            item for item in report.get("items", []) if item.get("kind") in ERROR_KINDS
        ]
        if errors:
            first = errors[0]
            when = (
                f" at {playtest_report.stamp(float(first['t']))}"
                if first.get("t") is not None
                else ""
            )
            where = f" ({first['where']})" if first.get("where") else ""
            more = f", and {len(errors) - 1} more" if len(errors) > 1 else ""
            return result(
                "fail",
                f"{first.get('title', 'Error')}{when}: {first.get('detail', '')}{where}"
                f"{more}. Played {clock}, {_replay(report)}.",
            )
        if played + 0.5 < criterion.seconds:
            return result(
                "inconclusive",
                f"The game ran only {clock} of the {criterion.seconds:g} s asked for.",
            )
        return result("pass", f"No errors in {clock} of play, {_replay(report)}.")
    word = playtest.reach_words([criterion.target])
    place = next(
        (
            p
            for p in report.get("reach", [])
            if isinstance(p, dict) and word and p.get("target") == word[0]
        ),
        None,
    )
    if place is None or not place.get("found"):
        return result(
            "inconclusive",
            f"Nothing in the game is named like “{criterion.target}”, so the bot "
            "couldn't head for it.",
        )
    node = str(place.get("node", "") or criterion.target)
    if place.get("reached"):
        return result(
            "pass",
            f"The bot reached {node} at "
            f"{playtest_report.stamp(float(place.get('t') or 0))}, "
            f"{_replay(report)}.",
        )
    closest = place.get("closest")
    near = f" (closest {float(closest):.1f})" if closest is not None else ""
    return result(
        "fail",
        f"The bot didn't get to {node} in {clock} of play{near}, {_replay(report)}. "
        "A bot is no person: check the way is open.",
    )


def _test(
    criterion: Criterion,
    stage: Path | None,
    ran: list[str],
    skipped: list[str],
    failing: list[str] | None = None,
) -> Result:
    def result(status: Status, evidence: str) -> Result:
        return Result(criterion.id, criterion.text, status, evidence)

    path = criterion.target.removeprefix("res://")
    if stage is None:
        return result("inconclusive", "The change has no files, so no test ran.")
    if not (stage / path).is_file():
        return result("fail", f"{criterion.target} isn't in the change.")
    from joinery.submission import gut_dirs, scene_tree_tests

    in_gut = any(
        path.startswith(folder.rstrip("/") + "/") for folder in gut_dirs(stage)
    )
    if not in_gut and path not in scene_tree_tests(stage):
        return result(
            "inconclusive",
            f"{criterion.target} isn't a test Joinery runs: put it under tests/ as a "
            "GUT test, or as a script that extends SceneTree and quits with 0 on pass.",
        )
    if "tests" not in ran:
        why = (
            "the tests didn't apply to this change"
            if "tests" in skipped
            else "Joinery didn't run the tests for this change"
        )
        return result("inconclusive", f"{criterion.target} didn't run: {why}.")
    if path in (failing or []):
        return result(
            "fail",
            f"{criterion.target} fails; it failed before this change too, so it "
            "doesn't hold the change, but the item isn't done.",
        )
    return result(
        "pass", f"{criterion.target} ran with the project's tests and passed."
    )


def _check(criterion: Criterion, ran: list[str], skipped: list[str]) -> Result:
    name = CHECK_NAMES[criterion.target.lower().replace(" ", "_")]
    if name in ran:
        return Result(
            criterion.id,
            criterion.text,
            "pass",
            f"Joinery's {name} check passed on this change.",
        )
    reason = (
        "didn't apply to this change"
        if name in skipped
        else "didn't run on this change"
    )
    return Result(
        criterion.id, criterion.text, "inconclusive", f"The {name} check {reason}."
    )


def failed(results: list[Result]) -> list[Result]:
    return [r for r in results if r.status == "fail"]


def fix_request(results: list[Result]) -> str:
    """What the agent hears for the items that failed."""
    lines = "\n".join(f"- {r.text}: {r.evidence}" for r in failed(results))
    return (
        "These done-when items failed when Joinery checked your change:\n"
        + lines
        + "\n"
    )


def counts_line(results: list[Result]) -> str:
    """'2 of 3 done' (passes of all items)."""
    passed = sum(1 for r in results if r.status == "pass")
    return f"{passed} of {len(results)} done"
