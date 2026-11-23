"""Whole-file reads of long files, refused for one call with the way to read less.

A file read once stays in the agent's conversation for every later call of its
session, so a 40k-character script read whole is paid for again on each of them.
In a session Joinery started (``JOINERY_STAGE`` set), the shared policy hook refuses
a provider's own read of a long file that names no line range: Claude Code's
``Read``, Gemini CLI's ``read_file``, opencode's ``read``, and a shell ``cat`` or a
``head``/``tail`` of more lines than a page, the same for every agent. The refusal
denies that one call (the turn carries on) and says how long the file is and how to
read it: ``search_project`` for the lines, or a line range. Small files and explicit
ranges pass untouched.

A read of ``recipes/<id>.<ext>`` that doesn't exist is refused with the recipe files
that do (the script beside its record, a superseded record in ``recipes/history/``,
or the copy in the session's staged change), so the agent doesn't guess again.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from joinery.policy import Severity, Verdict
from joinery.shell_words import pipelines, program_name
from joinery.stage_access import STAGE_ENV, stage_id

#: A file longer than either is read in parts.
MAX_LINES = 300
MAX_CHARS = 12_000
#: Bytes read to count a file's lines; anything longer is long.
_SCAN_BYTES = 4_000_000
#: Files the read tools show as pictures or pages, not text.
_NOT_TEXT = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".svg",
        ".pdf",
        ".ipynb",
        ".glb",
        ".blend",
        ".kra",
        ".wav",
        ".ogg",
        ".mp3",
        ".exr",
    }
)
#: Native read tools (lower-cased) and the argument naming their file.
_READ_TOOLS = frozenset({"read", "read_file"})
_PATH_KEYS = ("file_path", "absolute_path", "filePath", "path")
#: Native search tools whose ``path`` may name a recipe file.
_SEARCH_TOOLS = frozenset({"grep", "search_file_content", "glob"})
#: Shell readers that print a whole file, and those that print its first or last lines.
_WHOLE = frozenset({"cat", "nl", "tac", "less", "more", "bat"})
_ENDS = frozenset({"head", "tail"})
#: Other shell readers whose missing recipe file is worth naming the right one for.
_NAMERS = frozenset({"sed", "grep", "rg", "wc"})
#: A count meaning "to the end of the file".
_ALL = 1 << 30
_RECIPE = re.compile(r"(?:^|[/\\])recipes[/\\]([0-9a-f]{32})\.[A-Za-z0-9]+$")


def _size(path: Path) -> tuple[int, int] | None:
    """(lines, characters) of a text file, or None when it is not a readable text file."""
    if path.suffix.lower() in _NOT_TEXT:
        return None
    try:
        if not path.is_file():
            return None
        with path.open("rb") as handle:
            data = handle.read(_SCAN_BYTES)
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1), len(
        data.decode("utf-8", "replace")
    )


def _long(size: tuple[int, int] | None) -> bool:
    return size is not None and (size[0] > MAX_LINES or size[1] > MAX_CHARS)


def _shown(path: Path, project: Path) -> str:
    try:
        return path.resolve().relative_to(project.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def _recipe_id(path: str) -> str:
    match = _RECIPE.search(path)
    return match.group(1) if match else ""


def refusal(shown: str, size: tuple[int, int]) -> str:
    """What the agent is told about a long file it tried to read whole."""
    lines, chars = size
    message = (
        f"{shown} has {lines} lines ({round(chars / 1000)}k characters), too long to "
        f"read whole. Find the lines with search_project, then read a range (at most "
        f"{MAX_LINES} lines: offset and limit, or sed -n 'a,bp')."
    )
    recipe = _recipe_id(shown)
    if recipe:
        message += (
            f" For a recipe's operations, find_recipe(recipe='{recipe}') gives a "
            "short summary."
        )
    return message


def _stage(project: Path) -> Path | None:
    allowed = stage_id(os.environ.get(STAGE_ENV, ""))
    if not allowed:
        return None
    return project / ".joinery" / "transactions" / allowed / "stage"


def missing_recipe(path: Path, project: Path) -> Verdict | None:
    """A refusal naming the recipe files that exist, when ``path`` is a recipe file
    that doesn't."""
    recipe = _recipe_id(path.as_posix())
    if not recipe or path.exists():
        return None
    found: list[str] = []
    roots = [project]
    stage = _stage(project)
    if stage is not None:
        roots.append(stage)
    for root in roots:
        for folder in ("recipes", "recipes/history"):
            for match in sorted((root / folder).glob(f"{recipe}.*")):
                found.append(_shown(match, project))
    shown = _shown(path, project)
    if not found:
        return Verdict(
            Severity.DENY,
            f"{shown} doesn't exist, and no recipe file has that id here. "
            "find_recipe finds a recipe by what it made.",
        )
    return Verdict(
        Severity.DENY,
        f"{shown} doesn't exist. Recipe {recipe} is: {', '.join(found[:4])} "
        f"(recipes/history/ holds superseded records). find_recipe(recipe='{recipe}') "
        "summarises it.",
    )


def _judge(path: Path, project: Path) -> Verdict | None:
    """The verdict on reading all of ``path``."""
    missing = missing_recipe(path, project)
    if missing is not None:
        return missing
    size = _size(path)
    if _long(size):
        assert size is not None
        return Verdict(Severity.DENY, refusal(_shown(path, project), size))
    return None


def _ranged(values: Mapping[str, Any]) -> bool:
    limit = values.get("limit")
    return isinstance(limit, int) and not isinstance(limit, bool) and limit > 0


def native_read(
    native: str, values: Mapping[str, Any], cwd: Path, project: Path
) -> Verdict | None:
    """A provider's own read or search tool: a whole read of a long file, or a
    recipe file that doesn't exist."""
    name = native.lower()
    if name not in _READ_TOOLS and name not in _SEARCH_TOOLS:
        return None
    raw = next(
        (str(values[k]) for k in _PATH_KEYS if isinstance(values.get(k), str)), ""
    )
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    if name in _SEARCH_TOOLS:
        return missing_recipe(path, project)
    if _ranged(values):
        return missing_recipe(path, project)
    return _judge(path, project)


def _count(words: list[str]) -> tuple[int | None, list[str]]:
    """(lines asked for, the files) of ``head``/``tail`` words; None is the default
    ten lines. ``tail -n +N`` and ``head -n -N`` print up to the whole file."""
    count: int | None = None
    files: list[str] = []
    index = 1
    while index < len(words):
        word = words[index]
        value = ""
        if word in {"-n", "-c", "--lines", "--bytes"} and index + 1 < len(words):
            value = words[index + 1]
            index += 1
        elif word.startswith(("--lines=", "--bytes=")):
            value = word.split("=", 1)[1]
        elif re.fullmatch(r"-[nc]\S+", word):
            value = word[2:]
        elif re.fullmatch(r"-\d+", word):
            value = word[1:]
        elif word.startswith("-") and word != "-":
            pass
        else:
            files.append(word)
        if value:
            number = value.lstrip("+-")
            if number.isdigit():
                big = int(number)
                if value[0] in "+-" and len(value) > len(number):
                    big = _ALL
                elif "c" in word or "bytes" in word:
                    big = big // 40  # Bytes, as lines of 40 characters.
                count = big
        index += 1
    return count, files


def shell_read(command: str, cwd: Path, project: Path) -> Verdict | None:
    """A shell command that prints a long file whole (``cat``, ``head -n 2000``) to
    the agent, unpiped. A pipe into a filter, and ``sed -n`` ranges, pass."""
    found = pipelines(command)
    if not found:
        return None
    for pipeline in found:
        first = pipeline[0] if pipeline else []
        if not first:
            continue
        program = program_name(first[0])
        if program == "cd" and len(first) == 2:
            target = Path(first[1]).expanduser()
            cwd = target if target.is_absolute() else cwd / target
            continue
        if program in _WHOLE | _ENDS | _NAMERS:
            paths = [w for w in first[1:] if not w.startswith("-")]
            for word in paths:
                path = Path(word) if Path(word).is_absolute() else cwd / word
                missing = missing_recipe(path, project)
                if missing is not None:
                    return missing
        if len(pipeline) > 1:
            continue  # Its output goes through a filter, not to the agent whole.
        if program in _WHOLE:
            files = [w for w in first[1:] if not w.startswith("-") or w == "-"]
        elif program in _ENDS:
            count, files = _count(first)
            if count is None or count <= MAX_LINES:
                continue
        else:
            continue
        for word in files:
            if word == "-":
                continue
            path = Path(word) if Path(word).is_absolute() else cwd / word
            verdict = _judge(path, project)
            if verdict is not None:
                return verdict
    return None


def judged() -> bool:
    """Only sessions Joinery started are held to the read budget."""
    return os.environ.get(STAGE_ENV) is not None
