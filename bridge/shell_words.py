"""A shell command an agent runs, as the few words a person reads on the work trail.

Agents wrap what they run in plumbing: ``cd /Users/me/game && grep -rln "Spawn" .
2>/dev/null | head``. The trail shows what the command is for ("Searching the code
for Spawn", "Reading level.gd", "Running the tests"), never its flags or a full
path, and "Running a command" when it can't tell.
"""

from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath

#: The longest search pattern the line repeats.
PATTERN_WIDTH = 40
#: Segments that only set the scene for the command after them.
SETUP = frozenset(
    {"cd", "pushd", "popd", "export", "set", "source", ".", "true", "unset", "ulimit"}
)
#: Programs that run the program after them (with these options taking a value).
WRAPPERS = {
    "rtk": set(),
    "time": set(),
    "env": set(),
    "nice": {"-n"},
    "nohup": set(),
    "sudo": {"-u"},
    "xvfb-run": {"-s", "--server-args", "-a"},
    "npx": set(),
    "uvx": {"--from", "--with", "-p", "--python"},
    "command": set(),
    "exec": set(),
}
#: Programs that run what follows ``run`` ("uv run pytest", "poetry run mypy").
RUNNERS = frozenset({"uv", "poetry", "pdm", "pipenv", "hatch"})
#: Joins between commands.
JOINS = frozenset({";", "&&", "||", "&", "\n"})
#: Redirections, which take the next word as their file.
REDIRECTS = frozenset({">", ">>", "<", "<<", ">&", "&>", "&>>", "<<<", "2>", "1>"})
SEARCHERS = frozenset({"grep", "rg", "ag", "ack", "egrep", "fgrep", "ggrep"})
READERS = frozenset({"cat", "head", "tail", "less", "more", "bat", "nl", "view"})
LISTERS = frozenset({"ls", "tree", "fd", "exa", "eza", "du", "stat", "file"})
LINTERS = frozenset(
    {"ruff", "mypy", "gdlint", "gdformat", "black", "flake8", "pylint", "eslint"}
)
TESTERS = frozenset({"pytest", "py.test", "unittest", "gut", "nox", "tox"})
#: Git subcommands by what they look at.
GIT = {
    "log": "Looking at the history",
    "show": "Looking at a past change",
    "diff": "Looking at the changes",
    "status": "Checking what changed",
    "blame": "Looking at who changed {file}",
    "grep": "",
    "ls-files": "Listing files",
    "branch": "Looking at the branches",
}
#: Exit code 1 of a search with nothing found, as agents' tools report it.
_NO_MATCH = re.compile(
    r"^\s*(?:error:?\s*)?(?:exit(?:ed with)? code:? ?1|process exited with code 1)?\s*$"
    r"|no (?:matches|files|results)(?: found)?",
    re.IGNORECASE,
)


def command_line(command: object) -> str:
    """``command`` (a string, or an argv list) as one short line."""
    words = _words(command)
    if not words:
        return "Running a command"
    for program, args in _commands(words):
        line = _say(program, args)
        if line:
            return _short(line)
    return "Running a command"


def searches(command: object) -> bool:
    """True when ``command`` is a search (grep, rg, find), whose exit code 1 means
    only that nothing matched."""
    words = _words(command)
    for program, _args in _commands(words):
        return program in SEARCHERS or program == "find" or program == "git-grep"
    return False


def nothing_found(result: object) -> bool:
    """True when a search's failed ``result`` says only that nothing matched (an
    empty output, "Exit code 1", "No matches found"), not a real error."""
    texts = _texts(result)
    text = "\n".join(texts).strip()
    if not text:
        return True
    lines = [line for line in text.splitlines() if line.strip()]
    return len(lines) <= 2 and all(_NO_MATCH.search(line) for line in lines)


def _words(command: object) -> list[str]:
    if isinstance(command, list):
        parts = [str(part) for part in command]
        if len(parts) == 3 and parts[1] in {"-c", "-lc"}:
            command = parts[2]
        else:
            return parts
    text = str(command or "").strip()
    wrapped = re.match(
        r"^(?:/bin/|/usr/bin/)?(?:ba|z)?sh\s+-l?c\s+(['\"])(.*)\1$", text, re.DOTALL
    )
    if wrapped:
        text = wrapped.group(2)
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return re.split(r"\s+|(?=[;|&])|(?<=[;|&])", text)


def _commands(words: list[str]) -> list[tuple[str, list[str]]]:
    """Each command in ``words`` worth naming, in order, as (program, arguments): the
    setup before it (``cd …``), wrappers, variables and redirections taken out, and
    only the first command of a pipeline."""
    found: list[tuple[str, list[str]]] = []
    segment: list[str] = []
    for word in [*words, ";"]:
        if word not in JOINS:
            segment.append(word)
            continue
        pipeline = segment[: segment.index("|")] if "|" in segment else segment
        segment = []
        command = _unwrap(_unredirect(pipeline))
        if command and command[0] not in SETUP:
            found.append((command[0], command[1:]))
    return found


def _unredirect(words: list[str]) -> list[str]:
    kept: list[str] = []
    skip = False
    for word in words:
        if skip:
            skip = False
            continue
        if word in REDIRECTS or re.fullmatch(r"\d?[<>]+&?", word):
            if kept and kept[-1].isdigit():
                kept.pop()
            skip = True
            continue
        kept.append(word)
    return kept


def _unwrap(words: list[str]) -> list[str]:
    rest = list(words)
    while rest:
        head = PurePosixPath(rest[0].replace("\\", "/")).name
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", rest[0]):
            rest = rest[1:]
        elif head in WRAPPERS:
            takes = WRAPPERS[head]
            rest = rest[1:]
            while rest and rest[0].startswith("-"):
                option = rest.pop(0)
                if option in takes and rest:
                    rest.pop(0)
            if head == "env":
                while rest and "=" in rest[0]:
                    rest.pop(0)
        elif head == "timeout":
            rest = rest[1:]
            while rest and (
                rest[0].startswith("-") or re.fullmatch(r"[\d.]+[smh]?", rest[0])
            ):
                rest.pop(0)
        elif head in RUNNERS and len(rest) > 1 and rest[1] == "run":
            rest = rest[2:]
            while rest and rest[0].startswith("-"):
                rest.pop(0)
        elif re.fullmatch(r"python[\d.]*", head) and len(rest) > 2 and rest[1] == "-m":
            rest = rest[2:]
        else:
            return [head.lower(), *rest[1:]]
    return []


def _say(program: str, args: list[str]) -> str:
    operands = [a for a in args if not a.startswith("-")]
    if program in SEARCHERS:
        return _search(args)
    if program == "git":
        return _git(args)
    if program == "find":
        named = _option(args, {"-name", "-iname", "-path"})
        return f"Finding {named} files" if named else "Listing files"
    if program in LISTERS:
        where = [o for o in operands if o not in {".", "./"}]
        return f"Listing files in {_name(where[0])}" if where else "Listing files"
    if program == "sed":
        if any(a == "-i" or a.startswith("-i") for a in args):
            return (
                f"Editing {_name(operands[-1])}"
                if len(operands) > 1
                else "Editing a file"
            )
        return (
            f"Reading {_name(operands[-1])}" if len(operands) > 1 else "Reading a file"
        )
    if program in READERS:
        files = [o for o in operands if not re.fullmatch(r"\+?\d+", o)]
        return f"Reading {_name(files[-1])}" if files else "Reading a file"
    if program == "wc":
        return (
            f"Counting lines in {_name(operands[-1])}" if operands else "Counting lines"
        )
    if program in TESTERS or "test" in program or _runs_tests(args):
        return "Running the tests"
    if program in LINTERS:
        return "Checking the code"
    if program == "joinery":
        if operands[:1] == ["check"]:
            return "Running Joinery's checks"
        return f"Running joinery {operands[0]}" if operands else "Running Joinery"
    if program.startswith("godot"):
        return "Running Godot"
    if program in {"blender", "krita", "sclang", "surge-xt-cli", "inkscape"}:
        app = {"sclang": "SuperCollider", "surge-xt-cli": "Surge XT"}.get(
            program, program.capitalize()
        )
        return f"Running {app}"
    if re.fullmatch(r"python[\d.]*", program):
        if "-c" in args:
            return "Running a Python snippet"
        return f"Running {_name(operands[0])}" if operands else "Running Python"
    if program in {"pip", "pip3"} or (
        program == "uv" and operands[:1] in (["sync"], ["add"])
    ):
        return "Installing packages"
    if program == "mkdir":
        return "Making a folder"
    if program in {"rm", "rmdir"}:
        return f"Removing {_name(operands[-1])}" if operands else "Removing files"
    if program in {"cp", "mv"} and len(operands) >= 2:
        verb = "Copying" if program == "cp" else "Moving"
        return f"{verb} {_name(operands[0])}"
    if program in {"touch"} and operands:
        return f"Making {_name(operands[-1])}"
    if program in {"curl", "wget"}:
        url = next((o for o in operands if "://" in o), "")
        host = re.sub(r"^\w+://", "", url).split("/", 1)[0]
        return f"Reading {host}" if host else "Reading a web page"
    if program in {"echo", "printf", "pwd", "which", "type", "date", "sleep"}:
        return ""
    return ""


def _search(args: list[str]) -> str:
    pattern = _option(args, {"-e", "--regexp"})
    if not pattern:
        takes = {"-A", "-B", "-C", "-m", "--max-count", "-g", "--glob", "-t", "--type"}
        skip = False
        for word in args:
            if skip:
                skip = False
                continue
            if word in takes:
                skip = True
                continue
            if word.startswith("-"):
                continue
            pattern = word
            break
    shown = pattern_words(pattern)
    return f"Searching the code for {shown}" if shown else "Searching the code"


def _git(args: list[str]) -> str:
    operands = [a for a in args if not a.startswith("-")]
    sub = operands[0] if operands else ""
    if sub == "grep":
        return _search(args[args.index("grep") + 1 :])
    said = GIT.get(sub)
    if said is None:
        return "Using git"
    files = operands[1:]
    if "{file}" in said:
        return said.format(file=_name(files[-1])) if files else "Looking at the history"
    return said


def _runs_tests(args: list[str]) -> bool:
    joined = " ".join(args).lower()
    return bool(
        re.search(
            r"\b(?:pytest|gut|run_widget_tests|run_scene_tests|scene.tests?)\b", joined
        )
    )


def _option(args: list[str], names: set[str]) -> str:
    for index, word in enumerate(args):
        if word in names and index + 1 < len(args):
            return args[index + 1]
        for name in names:
            if name.startswith("--") and word.startswith(name + "="):
                return word.split("=", 1)[1]
    return ""


def pattern_words(pattern: str) -> str:
    """A search pattern in plain words: alternatives joined with "or", the regex
    marks taken out ("name\\|spawn_at" -> "name or spawn_at")."""
    text = re.sub(r"\\?\|", " or ", pattern)
    text = re.sub(r"\\[bBwWsSdD]|[\^$]|\(\?[a-z]+\)", "", text)
    text = text.replace("\\", "").replace(".*", " ")
    text = " ".join(text.split())
    if len(text) > PATTERN_WIDTH:
        text = text[: PATTERN_WIDTH - 1].rstrip() + "…"
    return text


def _name(path: str) -> str:
    """A path as the trail shows it: never whole, only its last part."""
    text = str(path).strip().replace("\\", "/").rstrip("/")
    if not text or text in {".", "./"}:
        return "the project"
    return PurePosixPath(text.removeprefix("res://")).name or text


def _short(text: str, width: int = 96) -> str:
    line = " ".join(text.split())
    return line if len(line) <= width else line[: width - 1].rstrip() + "…"


def _texts(result: object) -> list[str]:
    if isinstance(result, str):
        return [result]
    if isinstance(result, dict):
        found: list[str] = []
        for key, value in result.items():
            if key in {"type", "tool_use_id", "is_error"}:
                continue
            found += _texts(value)
        return found
    if isinstance(result, list):
        found = []
        for value in result:
            found += _texts(value)
        return found
    return []
