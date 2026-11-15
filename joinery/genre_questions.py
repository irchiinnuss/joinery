"""A short, optional round of questions about how the game plays (ADR 0087).

Each starting point (``blueprints/<id>/questions.toml``) asks four to six short
questions about the parts of a game that shape everything after it: what the
player spends most of their time doing, what makes it hard, how it should feel,
how long one sitting lasts. Every question has a few choices, one of them the
suggested default, and can be skipped. The whole round can be skipped too: then
the brief is drafted exactly as before.

The answers go into the brief, in the section each question belongs to, as one
line each that says where it came from::

    - Most of the time: Working out switches and doors. _(your answer)_
    - One sitting: About 10 minutes. _(suggested default, not confirmed)_
    - What makes it hard: _Not decided yet._ _(skipped)_

So the brief always tells the user's own choices from the suggestions they let
stand. A suggested default is never a promise: the design check reads the brief
without those lines (``joinery.design.brief_section``), so a default never
becomes a rule the game is held to until the user confirms it, by picking it or
by changing the mark in a reviewed edit of the brief (ADR 0053).

This module never imports ``joinery.blueprints`` or ``joinery.brief``: it reads
its own data files and returns text for the brief's tokens.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from joinery.catalog import ROOT

BLUEPRINTS_DIR = ROOT / "blueprints"
FILE = "questions.toml"
UNSET = "_Not decided yet._"
#: What a value in an answers object may say besides a choice id or own words.
SKIP = "skip"
DEFAULT = "default"
#: Characters an answer in the user's own words may have.
OWN_WORDS_LIMIT = 200

Source = Literal["asked", "default", "skipped"]
#: How each source reads at the end of its line in the brief.
MARKS: dict[str, str] = {
    "asked": "your answer",
    "default": "suggested default, not confirmed",
    "skipped": "skipped",
}
_SOURCE_BY_MARK = {mark: source for source, mark in MARKS.items()}

#: The brief tokens a question may write to, and the section each one is in.
SECTIONS: dict[str, str] = {
    "genre": "Genre and references",
    "fantasy": "Player fantasy",
    "core_loop": "Core loop",
    "camera": "Camera",
    "progression": "Win, lose, progression",
    "session": "Session length",
}
#: Tokens the template writes inside a line ("- Genre: {{genre}}"): an answer there
#: is that line's text, so the question's label should be the line's own ("Genre").
INLINE = frozenset({"genre"})

_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_LINE = re.compile(
    r"^- (?P<label>[^:\n]{1,60}): (?P<text>.*?) _\((?P<mark>"
    + "|".join(re.escape(m) for m in MARKS.values())
    + r")\)_\s*$"
)
_UNCONFIRMED = re.compile(
    r"_\((?:"
    + re.escape(MARKS["default"])
    + "|"
    + re.escape(MARKS["skipped"])
    + r")\)_\s*$"
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Choice(_Strict):
    id: str = Field(pattern=_KEY.pattern)
    #: A few words for the picker.
    label: str = Field(min_length=1, max_length=48)
    #: The sentence written into the brief.
    brief: str = Field(min_length=1, max_length=200)


class Question(_Strict):
    key: str = Field(pattern=_KEY.pattern)
    #: The question as the wizard asks it.
    ask: str = Field(min_length=1, max_length=70)
    #: The line's name in the brief ("Most of the time").
    label: str = Field(min_length=1, max_length=32)
    #: The brief token the answer goes into.
    section: str
    default: str
    choices: list[Choice] = Field(min_length=2, max_length=5)

    @model_validator(mode="after")
    def _known(self) -> Question:
        if self.section not in SECTIONS:
            raise ValueError(
                f"{self.key}: section must be one of {', '.join(SECTIONS)}"
            )
        ids = [c.id for c in self.choices]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.key}: choice ids repeat")
        if SKIP in ids or DEFAULT in ids:
            raise ValueError(f"{self.key}: {SKIP!r} and {DEFAULT!r} are not choice ids")
        if self.default not in ids:
            raise ValueError(
                f"{self.key}: the default {self.default!r} is not a choice"
            )
        return self

    def choice(self, choice_id: str) -> Choice | None:
        return next((c for c in self.choices if c.id == choice_id), None)


class Sheet(_Strict):
    """One starting point's questions."""

    intro: str = Field(default="", max_length=120)
    question: list[Question] = Field(min_length=4, max_length=6)

    @model_validator(mode="after")
    def _unique(self) -> Sheet:
        keys = [q.key for q in self.question]
        if len(set(keys)) != len(keys):
            raise ValueError("question keys repeat")
        return self


class Answer(_Strict):
    """One question's outcome, as the brief records it."""

    key: str
    label: str
    section: str
    source: Source
    #: The sentence in the brief ("" when skipped).
    text: str
    #: The choice picked or suggested ("" for own words or a skip).
    choice: str = ""


# --- Data ------------------------------------------------------------------------


def path(blueprint_id: str) -> Path:
    return BLUEPRINTS_DIR / blueprint_id / FILE


@cache
def _load(file: Path) -> Sheet | None:
    if not file.is_file():
        return None
    return Sheet.model_validate(tomllib.loads(file.read_text(encoding="utf-8")))


def sheet(blueprint_id: str) -> Sheet | None:
    """The starting point's questions, or None when it has none."""
    if not blueprint_id or "/" in blueprint_id or blueprint_id.startswith("."):
        return None
    return _load(path(blueprint_id))


def as_json(blueprint_id: str) -> dict[str, Any]:
    """The round as the wizard shows it: ``{"intro", "questions": [...]}``, each
    question with its choices and default; empty questions without a sheet."""
    found = sheet(blueprint_id)
    if found is None:
        return {"intro": "", "questions": []}
    return {
        "intro": found.intro,
        "questions": [
            {
                "key": q.key,
                "ask": q.ask,
                "label": q.label,
                "section": SECTIONS[q.section],
                "default": q.default,
                "choices": [
                    {"id": c.id, "label": c.label, "brief": c.brief} for c in q.choices
                ],
            }
            for q in found.question
        ],
    }


def problems() -> list[str]:
    """Every sheet that does not read, as readable problems (empty = fine)."""
    found: list[str] = []
    for file in sorted(BLUEPRINTS_DIR.glob(f"*/{FILE}")):
        try:
            _load.__wrapped__(file)
        except (tomllib.TOMLDecodeError, ValidationError) as exc:
            found.append(f"{file.parent.name}/{FILE}: {exc}")
    return found


# --- Answers ---------------------------------------------------------------------


def _own_words(value: str) -> str:
    line = " ".join(value.split())
    if len(line) > OWN_WORDS_LIMIT:
        raise ValueError(
            f"Keep each answer to one short line ({OWN_WORDS_LIMIT} characters at most)."
        )
    if line and line[-1] not in ".!?":
        line += "."
    return line[:1].upper() + line[1:]


def resolve(blueprint_id: str, given: object) -> list[Answer]:
    """Each question's outcome from what the user gave.

    ``given`` is the ``questions`` object of the brief answers: per question key,
    a choice id (asked), ``"skip"`` (skipped), ``"default"`` or nothing (the
    suggested default stands, unconfirmed), or the user's own words (asked).
    ``None``, ``False`` or ``"skip"`` for the whole object skip the round: no
    answers. Raises ValueError for a key the starting point does not ask.
    """
    if given is None or given is False or given == SKIP:
        return []
    found = sheet(blueprint_id)
    if found is None:
        return []
    if not isinstance(given, Mapping):
        raise ValueError("questions: expected an object of answers, one per question")  # noqa: TRY004
    known = {q.key for q in found.question}
    unknown = sorted(str(k) for k in given if k not in known)
    if unknown:
        raise ValueError(
            f"questions: the starting point does not ask {', '.join(unknown)} "
            f"(it asks {', '.join(q.key for q in found.question)})"
        )
    answers: list[Answer] = []
    for question in found.question:
        raw = given.get(question.key)
        value = "" if raw is None else str(raw).strip()
        base = {
            "key": question.key,
            "label": question.label,
            "section": question.section,
        }
        if value.casefold() == SKIP:
            answers.append(Answer(**base, source="skipped", text=""))
            continue
        if not value or value.casefold() == DEFAULT:
            choice = question.choice(question.default)
            assert choice is not None
            answers.append(
                Answer(**base, source="default", text=choice.brief, choice=choice.id)
            )
            continue
        picked = question.choice(value)
        if picked is not None:
            answers.append(
                Answer(**base, source="asked", text=picked.brief, choice=picked.id)
            )
        else:
            answers.append(Answer(**base, source="asked", text=_own_words(value)))
    return answers


def check_file(answers_file: Path, blueprint_id: str, key: str = "questions") -> None:
    """Raise ValueError when the brief answers file's question round doesn't fit the
    starting point, so a bad answer is caught before anything is written. A file
    that isn't JSON is left to the brief step, which reports it."""
    try:
        raw = json.loads(answers_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(raw, Mapping):
        resolve(blueprint_id, raw.get(key))


def line(answer: Answer) -> str:
    """The answer's line in the brief, with its mark."""
    text = answer.text if answer.source != "skipped" else UNSET
    return f"- {answer.label}: {text} _({MARKS[answer.source]})_"


def into(values: dict[str, str], answers: list[Answer]) -> dict[str, str]:
    """``values`` (the brief's token texts) with each answer's line put first in its
    section, ahead of what the starting point wrote there."""
    lines: dict[str, list[str]] = {}
    merged = dict(values)
    for answer in answers:
        if answer.section in INLINE:
            # The template's "- Genre: {{genre}}" line: the answer joins it, so the
            # line still reads "- Genre: <text> _(mark)_".
            before = merged.get(answer.section, "").strip()
            text = answer.text if answer.source != "skipped" else UNSET
            mark = f"{text} _({MARKS[answer.source]})_"
            merged[answer.section] = f"{before}; {mark}" if before else mark
            continue
        lines.setdefault(answer.section, []).append(line(answer))
    for token, added in lines.items():
        before = merged.get(token, "").strip()
        merged[token] = "\n".join(added) + (f"\n\n{before}" if before else "")
    return merged


# --- Reading them back -----------------------------------------------------------


def unconfirmed(text: str) -> bool:
    """Whether a brief line is a suggestion the user has not confirmed (a default
    left standing, or a skipped question): never something the game must do."""
    return _UNCONFIRMED.search(text) is not None


def recorded(brief_text: str) -> list[Answer]:
    """The question lines in a brief, with where each came from. A line the user
    has since edited keeps its mark, so what they chose stays readable."""
    answers: list[Answer] = []
    section = ""
    headings = {heading: token for token, heading in SECTIONS.items()}
    for raw in brief_text.splitlines():
        if raw.startswith("## "):
            title = re.sub(r"^##\s+(?:\d+\.\s*)?", "", raw).strip()
            section = headings.get(title, "")
            continue
        match = _LINE.match(raw.strip())
        if match is None or not section:
            continue
        source = _SOURCE_BY_MARK[match.group("mark")]
        text = match.group("text").strip()
        answers.append(
            Answer(
                key="",
                label=match.group("label").strip(),
                section=section,
                source=source,  # type: ignore[arg-type]
                text="" if text == UNSET else text,
            )
        )
    return answers


def suggestion(brief_text: str) -> str | None:
    """One line for Journeyman when defaults still stand in the brief."""
    standing = [a.label for a in recorded(brief_text) if a.source == "default"]
    if not standing:
        return None
    names = ", ".join(standing[:4]) + (" and more" if len(standing) > 4 else "")
    return (
        f"The brief still has suggested defaults the user hasn't confirmed ({names}). "
        "Treat them as open: ask before building on one, and when the user agrees, "
        "propose a brief edit that marks it as their answer."
    )
