"""The brief's Keep and Avoid lines, checked against a change at submit.

A Keep line says what must stay true ("Jump height 1.2 m"); a number in it pins a
value. An Avoid line says what the game must not have ("Guns (gun, rifle)"). Both are
the user's own lines in ``design/brief.md`` (``joinery.brief`` parses them).

At submit the staged change is compared with the project as it was:

- A pinned value is found where it lives. The line can name it (``res://path:name``
  or a project setting such as ``physics/2d/default_gravity``); otherwise an exported
  or plain ``var``/``const``, a scene or resource property, or a project setting
  whose name is the line's words (``jump_height``) holds it; failing that, a name
  that contains every word (``max_jump_height``, ``jumpHeight``), or the last words
  in a file whose path has the first ones (``speed`` in ``player/player.gd`` for
  "Player speed"). A change that moves such a value away from the pin is flagged.
- An Avoid line is looked for in what the change adds: added lines of text files,
  new file names, and the agent's own summary. Words the user listed in brackets
  match one at a time; otherwise every word of the line must appear.

A flag never fails the change and never sends it back: it is shown in the review with
the line it touched, and it holds an automatic accept (ADR 0085) so the user decides.
Nothing here knows which agent made the change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from joinery.brief import (
    BRIEF_PATH,
    AvoidLine,
    Brief,
    KeepLine,
    parse,
    singular,
)

#: Files read as text when looking at a change.
TEXT_SUFFIXES = frozenset(
    {".gd", ".cs", ".tscn", ".tres", ".godot", ".cfg", ".gdshader", ".json", ".md"}
)
#: Larger files are not read (generated data).
MAX_BYTES = 400_000
#: Flags reported for one change.
MAX_FLAGS = 8

_NUMBER = r"(-?\d+(?:\.\d+)?)"
_SCRIPT_VALUE = re.compile(
    r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:static\s+)?(?:var|const)\s+(\w+)"
    r"\s*(?::\s*[\w.]+)?\s*:?=\s*" + _NUMBER + r"\b",
    re.MULTILINE,
)
_PROPERTY_VALUE = re.compile(r"^([A-Za-z0-9_][\w/]*)\s*=\s*" + _NUMBER + r"\s*$")
_SECTION = re.compile(r"^\[([^\]\s]+)[^\]]*\]\s*$")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*")


@dataclass(frozen=True)
class Flag:
    """One Keep or Avoid line a change crosses, and where."""

    kind: Literal["keep", "avoid"]
    #: The brief line, as written.
    line: str
    #: Its line number in design/brief.md.
    number: int
    #: What the change does to it ("jump_height 1.2 → 1.5").
    what: str
    #: The project file it happens in ("player/player.gd"), "" for the summary.
    path: str = ""

    def text(self) -> str:
        """The review note."""
        head = "Keep" if self.kind == "keep" else "Avoid"
        where = f" ({self.path})" if self.path and self.path not in self.what else ""
        return f"Brief {head} line “{self.line}”: {self.what}{where}."

    def as_dict(self) -> dict[str, str | int]:
        return {
            "kind": self.kind,
            "line": self.line,
            "number": self.number,
            "what": self.what,
            "path": self.path,
            "text": self.text(),
        }

    def reason(self) -> str:
        """Short words for the held reasons ("Keep: Jump height 1.2 m")."""
        return ("Keep: " if self.kind == "keep" else "Avoid: ") + self.line[:60]


# --- reading values ---------------------------------------------------------------------


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def values(rel: str, text: str) -> dict[str, float]:
    """Every name given a number in ``text``: script ``var``/``const``, scene and
    resource properties (by node: ``Player/jump_height`` and ``jump_height``), and
    project settings by their full key (``physics/2d/default_gravity``)."""
    found: dict[str, float] = {}
    if rel.endswith((".gd", ".cs")):
        for match in _SCRIPT_VALUE.finditer(text):
            found.setdefault(match.group(1), float(match.group(2)))
        return found
    section = ""
    for raw in text.splitlines():
        header = _SECTION.match(raw.strip())
        if header is not None:
            section = header.group(1)
            node = re.search(r'\bname="([^"]+)"', raw)
            parent = re.search(r'\bparent="([^"]*)"', raw)
            if node is not None and section == "node":
                path = node.group(1)
                if parent is not None and parent.group(1) not in ("", "."):
                    path = parent.group(1) + "/" + path
                section = "node:" + path
            continue
        prop = _PROPERTY_VALUE.match(raw.strip())
        if prop is None:
            continue
        name, value = prop.group(1), float(prop.group(2))
        if rel == "project.godot" and section:
            found[f"{section}/{name}"] = value
        elif section.startswith("node:"):
            found[f"{section[5:]}/{name}"] = value
        found.setdefault(name, value)
    return found


def _parts(name: str) -> list[str]:
    """``jumpHeight`` / ``JUMP_HEIGHT`` / ``Player/jump_height`` -> words."""
    leaf = name.rsplit("/", 1)[-1]
    spaced = _CAMEL.sub("_", leaf)
    return [singular(p) for p in spaced.lower().split("_") if p]


def _name_words(name: str) -> list[str]:
    return [singular(w) for w in re.findall(r"[a-z0-9]+", name.lower())]


@dataclass(frozen=True)
class _Match:
    name: str
    exact: bool


def _holds(keep: KeepLine, rel: str, name: str) -> _Match | None:
    """Whether the value ``name`` in file ``rel`` is what ``keep`` pins."""
    pin = keep.pin
    if pin is None:
        return None
    if pin.target:
        target = pin.target
        if target.startswith("res://"):
            file, _, prop = target.removeprefix("res://").partition(":")
            if file != rel:
                return None
            return (
                _Match(name, True)
                if name == prop or name.endswith("/" + prop)
                else None
            )
        return _Match(name, True) if rel == "project.godot" and name == target else None
    words = _name_words(pin.name)
    if not words:
        return None
    parts = _parts(name)
    if parts == words:
        return _Match(name, True)
    if len(parts) > len(words) and all(w in parts for w in words):
        return _Match(name, False)
    # "Player speed": `speed` in a file whose path names the player.
    for cut in range(1, len(words)):
        subject, rest = words[:cut], words[cut:]
        place = _name_words(rel)
        if parts == rest and all(w in place for w in subject):
            return _Match(name, False)
    return None


def _number(value: float) -> str:
    return f"{value:g}"


def _keep_flags(
    keep: list[KeepLine], rel: str, before: str | None, after: str | None
) -> list[Flag]:
    if not any(k.pin is not None for k in keep):
        return []
    old = values(rel, before) if before is not None else {}
    new = values(rel, after) if after is not None else {}
    flags: list[Flag] = []
    for line in keep:
        if line.pin is None:
            continue
        for name in sorted(set(old) | set(new)):
            if (
                "/" in name
                and rel != "project.godot"
                and name.split("/")[-1] in old | new
            ):
                # A node's own copy is reported by its plain name.
                continue
            if _holds(line, rel, name) is None:
                continue
            was, now = old.get(name), new.get(name)
            if was == now:
                continue
            if now is not None and abs(now - line.pin.value) < 1e-6:
                continue  # back to the pinned value
            if now is None:
                what = f"{name} {_number(was or 0)} is removed"
            elif was is None:
                what = f"{name} set to {_number(now)} (the brief pins {line.pin.text})"
            else:
                what = (
                    f"{name} {_number(was)} → {_number(now)} "
                    f"(the brief pins {line.pin.text})"
                )
            flags.append(Flag("keep", line.text, line.line, what, rel))
            break
    return flags


# --- what a change adds -----------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    """Lowercase singular words of ``text``, identifiers split (``gun_shot`` ->
    gun, shot; ``RifleAmmo`` -> rifle, ammo)."""
    words: list[str] = []
    for token in _TOKEN.findall(text):
        for part in _CAMEL.sub("_", token).lower().split("_"):
            if part:
                words.append(singular(part))
    return words


def _phrase_in(phrase: str, tokens: list[str]) -> bool:
    wanted = [singular(w) for w in re.findall(r"[a-z0-9]+", phrase.lower())]
    if not wanted:
        return False
    size = len(wanted)
    return any(tokens[i : i + size] == wanted for i in range(len(tokens) - size + 1))


def _avoid_hit(line: AvoidLine, tokens: list[str]) -> str:
    """The word of ``line`` found in ``tokens``; "" when it isn't there."""
    if line.any_word:
        return next((w for w in line.words if _phrase_in(w, tokens)), "")
    present = set(tokens)
    return " ".join(line.words) if all(w in present for w in line.words) else ""


def _added(before: str | None, after: str | None) -> str:
    """The lines ``after`` has that ``before`` didn't."""
    if after is None:
        return ""
    if before is None:
        return after
    old = set(before.splitlines())
    return "\n".join(line for line in after.splitlines() if line not in old)


# --- the check --------------------------------------------------------------------------


def brief_of(root: Path) -> Brief | None:
    text = _read(root / BRIEF_PATH)
    return parse(text) if text is not None else None


def check(
    original: Path,
    stage: Path,
    changes: list[dict[str, str]],
    *,
    said: str = "",
) -> list[Flag]:
    """The Keep and Avoid lines the staged change crosses.

    ``original`` is the project as it was, ``stage`` the change as files,
    ``changes`` the staged diff (``{"path", "change"}``), ``said`` the agent's own
    summary and facts. The brief read is the project's own: an edit of the brief
    itself is the brief's change, judged by the brief's rules, not a crossing.
    """
    brief = brief_of(original)
    if brief is None or not (brief.keep or brief.avoid):
        return []
    flags: list[Flag] = []
    seen: set[tuple[str, int]] = set()

    def add(found: Flag) -> None:
        if (found.kind, found.number) not in seen:
            seen.add((found.kind, found.number))
            flags.append(found)

    for change in changes:
        rel = str(change.get("path", "")).removeprefix("res://")
        if not rel or rel == BRIEF_PATH.as_posix() or rel.startswith(".joinery/"):
            continue
        name_tokens = _tokens(rel) if change.get("change") == "added" else []
        if Path(rel).suffix not in TEXT_SUFFIXES and rel != "project.godot":
            text_before = text_after = None
        else:
            text_before = _read(original / rel)
            text_after = _read(stage / rel)
        if text_before is not None or text_after is not None:
            for found in _keep_flags(brief.keep, rel, text_before, text_after):
                add(found)
        tokens = name_tokens + _tokens(_added(text_before, text_after))
        for line in brief.avoid:
            hit = _avoid_hit(line, tokens)
            if hit:
                add(
                    Flag("avoid", line.text, line.line, f"the change adds “{hit}”", rel)
                )
    if said.strip():
        tokens = _tokens(said)
        for line in brief.avoid:
            hit = _avoid_hit(line, tokens)
            if hit:
                add(Flag("avoid", line.text, line.line, f"the change is about “{hit}”"))
    return flags[:MAX_FLAGS]


def agent_note(flags: list[Flag]) -> str:
    """What the agent is told: name the line and ask the user, don't argue it away."""
    lines = "; ".join(f.text() for f in flags[:3])
    return (
        "This change crosses the game brief's Keep/Avoid lines: "
        + lines
        + " It waits for the user. Tell them which line it crosses and ask whether "
        "they want it; don't change the brief to make it fit."
    )
