"""Project rules for some files only, given to the agent when it touches them (ADR 0088).

Most of ``JOURNEYMAN.md`` holds for the whole project and sits in every turn's stable
prefix (ADR 0077). Some rules only matter in one corner of the game ("enemies use the
behaviour feature", "UI scenes anchor to the safe area"). Those go under a heading that
names the files, in ``JOURNEYMAN.md``::

    ## When working on game/ai/**, game/enemies/*.gd
    - Enemies use the behaviour feature, never a hand-rolled state machine.

or in a file of their own, ``.joinery/rules/<name>.md``, whose first line names them::

    paths: game/ui/**
    Every screen anchors to the safe area.

Journeyman gets a rule only when it is working on a matching file:

* in the turn, when the selection or the open scene is one (``turn_line``);
* in the result of any gateway call that names one, a read **or** a write or a new
  file (``ProjectGateway.call``), so a rule reaches the agent even for a file that
  does not exist yet. Each rule is said once per turn.

These sections are kept out of the stable prefix (``joinery.memory.block`` drops them),
so adding or editing a rule never changes the cached bytes every turn starts with
(ADR 0056). Nothing here is per-agent: every provider reaches the project through the
same turn text and the same gateway.

The user edits rules like the rest of the project: ``/remember for game/ai/**: …`` and
``joinery rules add`` stage the line as a change for Review; a hand edit of the file is
the user's own.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath

from joinery.transactions import FileTransaction

MEMORY_FILE = "JOURNEYMAN.md"
FOLDER = ".joinery/rules"
#: Characters of one rule Journeyman is given; a longer one is cut with a note.
RULE_LIMIT = 1500
#: Rules attached to one result or turn, and their characters together.
MAX_RULES = 4
TOTAL_LIMIT = 3000
#: Characters of one rule line added with /remember or `joinery rules add`.
ENTRY_LIMIT = 300
#: Patterns one heading may name.
MAX_GLOBS = 8
HEADING_WORDS = "When working on"
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_SCOPED = re.compile(r"^##\s+when working on\s+(.+?)\s*$", re.IGNORECASE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_PATHS = re.compile(r"^paths\s*:\s*(.+)$", re.IGNORECASE)
_REMEMBER = re.compile(r"^for\s+(\S.*?)\s*:\s+(.+)$", re.IGNORECASE | re.DOTALL)
_GLOB = re.compile(r"^[A-Za-z0-9_.*?\-/\[\]{}]+$")


@dataclass(frozen=True)
class Rule:
    globs: tuple[str, ...]
    text: str
    #: Where it is written: "JOURNEYMAN.md" or ".joinery/rules/<name>.md".
    source: str

    def matches(self, path: str) -> bool:
        return any(_compiled(glob).match(path) for glob in self.globs)


# --- Patterns --------------------------------------------------------------------


@cache
def _compiled(glob: str) -> re.Pattern[str]:
    """``**`` spans folders, ``*`` and ``?`` stay inside one; a pattern without a
    slash also matches that name in any folder, like a .gitignore line."""
    pattern = glob.strip().removeprefix("res://").removeprefix("./").lstrip("/")
    if pattern.endswith("/"):
        pattern += "**"
    out = ""
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out += "(?:.*/)?"
            index += 3
        elif pattern.startswith("**", index):
            out += ".*"
            index += 2
        elif pattern[index] == "*":
            out += "[^/]*"
            index += 1
        elif pattern[index] == "?":
            out += "[^/]"
            index += 1
        else:
            out += re.escape(pattern[index])
            index += 1
    if "/" not in pattern:
        out = "(?:.*/)?" + out
    return re.compile(out + r"\Z")


def globs_of(text: str) -> tuple[str, ...]:
    """The patterns a heading or a ``paths:`` line names: comma or space separated,
    with or without backticks. Raises ValueError for none or a strange one."""
    found = [
        part.strip().strip("`").strip()
        for part in re.split(r"[,\s]+", text.strip())
        if part.strip().strip("`").strip()
    ]
    if not found:
        raise ValueError("Name the files the rule is for, such as game/ai/**.")
    if len(found) > MAX_GLOBS:
        raise ValueError(f"A rule names at most {MAX_GLOBS} file patterns.")
    bad = [g for g in found if not _GLOB.match(g) or ".." in PurePosixPath(g).parts]
    if bad:
        raise ValueError(
            "File patterns are project paths such as game/ai/** or *.gdshader: "
            + ", ".join(bad)
        )
    return tuple(g.removeprefix("res://") for g in found)


def relative(path: str) -> str:
    """A project path in one spelling: no res://, no leading ./ or /."""
    return path.strip().replace("\\", "/").removeprefix("res://").removeprefix("./")


# --- Reading ---------------------------------------------------------------------


def _read(file: Path) -> str:
    if file.is_symlink() or not file.is_file():
        return ""
    try:
        return file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _body(lines: list[str]) -> str:
    text = _COMMENT.sub("", "\n".join(lines))
    kept = [line.rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def split(text: str) -> tuple[str, list[tuple[str, str]]]:
    """``JOURNEYMAN.md`` as (the text without its scoped sections, each scoped
    section's (patterns, body)). The scoped sections end at the next heading of
    level one or two; a deeper heading belongs to them."""
    general: list[str] = []
    scoped: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        heading = None if fenced else _HEADING.match(line)
        if heading and len(heading.group(1)) <= 2:
            match = _SCOPED.match(line)
            if match:
                current = []
                scoped.append((match.group(1), current))
                continue
            current = None
        if current is not None:
            current.append(line)
        else:
            general.append(line)
    rest = "\n".join(general)
    if text.endswith("\n"):
        rest += "\n"
    return rest, [(globs, _body(body)) for globs, body in scoped]


def strip(text: str) -> str:
    """``JOURNEYMAN.md`` without its scoped sections: what the stable prefix holds."""
    return split(text)[0]


def _from_memory(project: Path) -> list[Rule]:
    rules: list[Rule] = []
    for raw, body in split(_read(project / MEMORY_FILE))[1]:
        if not body:
            continue
        try:
            rules.append(Rule(globs_of(raw), body, MEMORY_FILE))
        except ValueError:
            continue  # `joinery rules` lists it as a problem.
    return rules


def _from_folder(project: Path) -> list[Rule]:
    folder = project / FOLDER
    if folder.is_symlink() or not folder.is_dir():
        return []
    rules: list[Rule] = []
    for file in sorted(folder.glob("*.md")):
        lines = _read(file).splitlines()
        first = next((i for i, line in enumerate(lines) if line.strip()), None)
        if first is None:
            continue
        match = _PATHS.match(lines[first].strip())
        body = _body(lines[first + 1 :])
        if match is None or not body:
            continue
        try:
            globs = globs_of(match.group(1).strip().strip("[]").replace('"', ""))
        except ValueError:
            continue
        rules.append(Rule(globs, body, f"{FOLDER}/{file.name}"))
    return rules


def load(project: Path) -> list[Rule]:
    """Every scoped rule of the project, JOURNEYMAN.md's first."""
    return _from_memory(project) + _from_folder(project)


def problems(project: Path) -> list[str]:
    """Scoped sections and rule files Journeyman can't use, and why."""
    found: list[str] = []
    for raw, body in split(_read(project / MEMORY_FILE))[1]:
        try:
            globs_of(raw)
        except ValueError as exc:
            found.append(f"{MEMORY_FILE}, '## {HEADING_WORDS} {raw}': {exc}")
            continue
        if not body:
            found.append(f"{MEMORY_FILE}, '## {HEADING_WORDS} {raw}': no rule under it")
    folder = project / FOLDER
    for file in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        lines = [line for line in _read(file).splitlines() if line.strip()]
        if not lines or _PATHS.match(lines[0].strip()) is None:
            found.append(f"{FOLDER}/{file.name}: the first line must be 'paths: …'")
    return found


def matching(rules: Iterable[Rule], paths: Iterable[str]) -> list[tuple[Rule, str]]:
    """Each rule that holds for one of ``paths``, with the first path it holds for."""
    wanted = [relative(p) for p in paths if relative(p)]
    found: list[tuple[Rule, str]] = []
    for rule in rules:
        hit = next((p for p in wanted if rule.matches(p)), None)
        if hit is not None:
            found.append((rule, hit))
    return found


def text(matched: list[tuple[Rule, str]], *, intro: str) -> str:
    """The rules as one block, within ``MAX_RULES`` and ``TOTAL_LIMIT``."""
    if not matched:
        return ""
    parts: list[str] = []
    used = 0
    for rule, path in matched[:MAX_RULES]:
        body = rule.text
        if len(body) > RULE_LIMIT:
            cut = body.rfind("\n", 0, RULE_LIMIT)
            body = body[: cut if cut > 0 else RULE_LIMIT].rstrip() + (
                f"\n[The rest of this rule is past the {RULE_LIMIT} characters "
                "Journeyman reads.]"
            )
        entry = (
            f"For {', '.join(rule.globs)} (from {rule.source}; {path} matches):\n{body}"
        )
        if used + len(entry) > TOTAL_LIMIT and parts:
            break
        parts.append(entry)
        used += len(entry)
    left = len(matched) - len(parts)
    if left > 0:
        parts.append(f"[{left} more rule(s) match; `joinery rules` lists them.]")
    return intro + "\n" + "\n\n".join(parts)


TURN_INTRO = (
    "Project rules for the files selected (from the user; follow them unless the user "
    "says otherwise in this chat):"
)
RESULT_INTRO = (
    "Project rules for the files this call touched (from the user; follow them in "
    "this change unless the user says otherwise):"
)


def turn_line(project: Path, paths: Iterable[str]) -> str:
    """The rules for the selection and the open scene, for the turn's own part."""
    return text(matching(load(project), paths), intro=TURN_INTRO)


class Shown:
    """Which rules this turn has already been given, so each is said once."""

    def __init__(self) -> None:
        self._turn: object = None
        self._seen: set[tuple[str, tuple[str, ...]]] = set()

    def note(self, project: Path, paths: Iterable[str], turn: object) -> str:
        """The block for a tool result naming ``paths``: the rules not yet given in
        ``turn``; "" when none."""
        if turn is not self._turn:
            self._turn, self._seen = turn, set()
        fresh = [
            (rule, path)
            for rule, path in matching(load(project), paths)
            if (rule.source, rule.globs) not in self._seen
        ]
        self._seen.update((rule.source, rule.globs) for rule, _ in fresh)
        return text(fresh, intro=RESULT_INTRO)


# --- Editing, as a reviewed change -----------------------------------------------


def parse_remember(text: str) -> tuple[tuple[str, ...], str] | None:
    """``for game/ai/**: enemies use …`` as (patterns, line); None for an ordinary
    /remember line."""
    match = _REMEMBER.match(" ".join(text.split()))
    if match is None:
        return None
    try:
        globs = globs_of(match.group(1))
    except ValueError:
        return None
    return globs, entry(match.group(2))


def entry(line: str) -> str:
    item = " ".join(str(line).split()).removeprefix("- ").strip()
    if not item:
        raise ValueError("Write the rule itself after the files it is for.")
    if len(item) > ENTRY_LIMIT:
        raise ValueError(
            f"Keep a rule to one short line ({ENTRY_LIMIT} characters at most); "
            f"this one has {len(item)}."
        )
    return item


def added(text: str, globs: tuple[str, ...], line: str) -> str:
    """``text`` with ``line`` under the section for exactly ``globs`` (a new section
    at the end when there is none)."""
    item = entry(line)
    lines = text.splitlines()
    start = -1
    for index, raw in enumerate(lines):
        match = _SCOPED.match(raw)
        if match is not None:
            try:
                if globs_of(match.group(1)) == globs:
                    start = index
                    break
            except ValueError:
                continue
    if start < 0:
        body = text.rstrip("\n")
        heading = f"## {HEADING_WORDS} {', '.join(globs)}"
        return (body + "\n\n" if body else "") + f"{heading}\n- {item}\n"
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if (h := _HEADING.match(lines[i])) and len(h.group(1)) <= 2
        ),
        len(lines),
    )
    existing = [
        x.strip().removeprefix("- ").strip().casefold() for x in lines[start + 1 : end]
    ]
    if item.casefold() in existing:
        raise ValueError("JOURNEYMAN.md already has that rule.")
    at = max(
        [i + 1 for i in range(start + 1, end) if lines[i].strip()], default=start + 1
    )
    lines.insert(at, f"- {item}")
    return "\n".join(lines) + "\n"


def append(root: Path, globs: tuple[str, ...], line: str) -> str:
    """Add the rule to ``root``'s JOURNEYMAN.md (a staged copy); returns the line."""
    target = root / MEMORY_FILE
    if target.is_symlink():
        raise ValueError("JOURNEYMAN.md is a link; Journeyman won't write through it.")
    current = target.read_text(encoding="utf-8") if target.is_file() else ""
    target.write_text(added(current, globs, line), encoding="utf-8")
    return entry(line)


def summary(globs: tuple[str, ...], line: str) -> str:
    item = entry(line)
    short = item if len(item) <= 48 else item[:47].rstrip() + "…"
    return f"Rule for {', '.join(globs)}: {short}"


def stage(project: Path, globs: tuple[str, ...], line: str) -> FileTransaction:
    """The rule as a change waiting for review, kept across sessions."""
    item = entry(line)
    added(_read(project / MEMORY_FILE), globs, item)  # refuse before staging
    tx = FileTransaction(project)
    tx.note_tool("joinery")
    tx.begin(summary(globs, item))
    try:
        append(tx.stage, globs, item)
        tx.review()
        tx.hold("joinery rules")
    except BaseException:
        if tx.state in {"staging", "review"}:
            tx.reject()
        raise
    return tx


# --- CLI -------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``joinery rules --project P [list | match PATH... | add --path GLOB TEXT]``."""
    parser = subparsers.add_parser(
        "rules",
        help="project rules for some files only: list them, see which hold for a "
        "path, or add one for review",
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "action", nargs="?", default="list", choices=("list", "match", "add")
    )
    parser.add_argument("words", nargs="*", help="paths (match) or the rule (add)")
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        metavar="GLOB",
        help="add: the files the rule is for, such as game/ai/** (repeatable)",
    )
    parser.add_argument("--json", action="store_true", help="print JSON")


def _rule_json(rule: Rule) -> dict[str, object]:
    return {"paths": list(rule.globs), "text": rule.text, "source": rule.source}


def run(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    if not (project / "project.godot").is_file():
        message = f"No Godot project at {project}."
        print(json.dumps({"error": message}) if args.json else message)
        return 1
    if args.action == "add":
        try:
            globs = globs_of(" ".join(args.path))
            tx = stage(project, globs, " ".join(args.words))
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}) if args.json else str(exc))
            return 2
        data = {"transaction_id": tx.id, "summary": tx.summary, "state": tx.state}
        if args.json:
            print(json.dumps(data))
        else:
            print(
                f"{tx.summary}: waiting for your review in the Review tab, or "
                f"joinery transactions accept --project {project} --id {tx.id}"
            )
        return 0
    rules = load(project)
    if args.action == "match":
        matched = matching(rules, args.words)
        if args.json:
            print(json.dumps([{**_rule_json(r), "match": p} for r, p in matched]))
        else:
            print(
                text(matched, intro="Rules that hold:")
                or "No rule holds for those files."
            )
        return 0
    issues = problems(project)
    if args.json:
        print(json.dumps({"rules": [_rule_json(r) for r in rules], "problems": issues}))
        return 0
    if not rules and not issues:
        print(
            f"No rules for particular files yet. Add one under '## {HEADING_WORDS} "
            f"<files>' in {MEMORY_FILE}, or with /remember for <files>: <rule>."
        )
    for rule in rules:
        print(f"{', '.join(rule.globs)} ({rule.source}):")
        for line in rule.text.splitlines():
            print(f"  {line}")
    for issue in issues:
        print(f"Can't use: {issue}")
    return 0
