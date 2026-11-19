"""What a creative tool's failed run said, the error first and its log after.

Krita starts with a page of resource-database and Qt chatter, sclang with its
class-library compile, Blender with its own banner: the line that says what went
wrong comes last, if at all. A failure message leads with that line (a Python
traceback's exception and the line of the operation script it stopped at, sclang's
``ERROR:`` lines and Joinery's own ``JOINERY_ERROR`` verdict) and then carries the
log without the chatter. The gateway keeps a long log behind ``details`` (see
``joinery.compact``), so the agent reads the error first and pages the rest only
when it needs it.
"""

from __future__ import annotations

import re

#: Lines an app prints on every start, never about the agent's code. Krita: its
#: resource database, Qt warnings, PyKrita's path and unknown-action notes; sclang:
#: the class library compile and its welcome; Python: the SyntaxWarnings Krita's
#: own bundled scripts raise under Python 3.12 (a warning and its source line).
_NOISE = re.compile(
    r"^(?:"
    r"\S+\.py:\d+: SyntaxWarning: .*\n(?:[ \t]+.*\n?)?"
    r"|krita\.[\w.]+: .*\n?"
    r"|QObject::.*\n?"
    r"|\S+ added to PYTHONPATH\n?"
    r'|Warning: requested data for unknown action ".*"\n?'
    r"|compiling class library\.\.\.\n?"
    r"|[ \t]+(?:Found \d+ primitives\.|Compiling directory '.*'|numentries = .*"
    r"|\d+ method selectors, \d+ classes|method table size .*|Number of Symbols \d+"
    r"|Byte Code Size \d+|compiled \d+ files in .*)\n?"
    r"|Info: \d+ methods are currently overwritten by extensions\..*\n?"
    r"|MethodOverride\.printAll\n?"
    r"|compile done\n?"
    r"|(?:localhost|internal) : setting clientID to \d+\.\n?"
    r"|Class tree inited in .*\n?"
    r"|\*\*\* Welcome to SuperCollider .*\n?"
    r")",
    re.MULTILINE,
)
#: Characters of the log a failure message carries at most (its end): enough for any
#: traceback, and the gateway pages it behind ``details`` when it is long.
LOG_KEEP = 20000
#: Error lines a message leads with, at most.
MOST = 4

_TRACEBACK = "Traceback (most recent call last):"
_FRAME = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)')
_SCLANG_ERROR = re.compile(r"^ERROR: (?P<text>.+)$")
_SCLANG_WHERE = re.compile(r"^\s*line \d+ char \d+:\s*$")
_JOINERY = re.compile(r"^JOINERY_(?:ERROR|TIMEOUT): (?P<text>.+)$")
_GENERIC = re.compile(
    r"^(?:FATAL|Fatal error|Error|ERROR|Segmentation fault|[\w.]+(?:Error|Exception): )"
    r".*$"
)


def quiet(output: str) -> str:
    """``output`` without the apps' own start-up chatter, blank runs closed up."""
    text = _NOISE.sub("", output)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def error_lines(output: str) -> list[str]:
    """The lines that say what went wrong, most telling first: an sclang error and
    where it is, a Python traceback's exception and the operation script's line it
    stopped at, Joinery's own verdict; else the last lines that start with an error
    word."""
    lines = quiet(output).splitlines()
    found: list[str] = []
    for index, line in enumerate(lines):
        error = _SCLANG_ERROR.match(line)
        if error is not None:
            found.append(line.strip())
            # A parse error says where: "  line 3 char 5:".
            for follow in lines[index + 1 : index + 4]:
                if _SCLANG_WHERE.match(follow):
                    found.append(follow.strip().rstrip(":"))
                    break
    found += _traceback(lines)
    for line in lines:
        verdict = _JOINERY.match(line)
        if verdict is not None:
            found.append(verdict.group("text").strip())
    if not found:
        found = [line.strip() for line in lines if _GENERIC.match(line)][-2:]
    return list(dict.fromkeys(found))[:MOST]


def _traceback(lines: list[str]) -> list[str]:
    """The last Python traceback's exception line, then the operation script's line
    it stopped at (its last frame in recipes/), when it has one."""
    starts = [i for i, line in enumerate(lines) if line.strip() == _TRACEBACK]
    if not starts:
        return []
    frames: list[tuple[str, str, str]] = []
    exception = ""
    rest = lines[starts[-1] + 1 :]
    for index, line in enumerate(rest):
        frame = _FRAME.match(line)
        if frame is not None:
            code = rest[index + 1].strip() if index + 1 < len(rest) else ""
            if _FRAME.match(code) or set(code) <= set("~^ "):
                code = ""
            frames.append((frame.group("file"), frame.group("line"), code))
        elif line and not line[0].isspace():
            exception = line.strip()
            break
    if not exception:
        return []
    # Where in the operation script; a library's or the worker's own frame means
    # nothing to the agent.
    ours = [f for f in frames if "recipes/" in f[0]]
    if not ours:
        return [exception]
    file, number, code = ours[-1]
    where = f"at {file.split('/work/')[-1]} line {number}"
    return [exception, where + (f": {code}" if code else "")]


def failure(prefix: str, output: str, *, app: str = "") -> str:
    """``prefix`` (what didn't happen), then the error lines, then the log without
    its chatter. Without an error line it says the app printed none, and why when
    the log shows (sclang stopped while compiling its class library)."""
    found = error_lines(output)
    text = quiet(output)
    if found:
        lead = " ".join(_sentence(line) for line in found)
    elif "compiling class library" in output and "compile done" not in output:
        lead = (
            "sclang stopped while compiling its class library, before the patch ran, "
            "and printed no error."
        )
    else:
        last = text.splitlines()[-1].strip() if text else ""
        name = app or "The app"
        lead = (
            f"{name} printed no error; its last line was: {last}"
            if last
            else f"{name} printed nothing."
        )
    message = f"{prefix}: {lead}"
    if text:
        cut = "…" if len(text) > LOG_KEEP else ""
        message += f"\n\nLog:\n{cut}{text[-LOG_KEEP:]}"
    return message


def _sentence(line: str) -> str:
    """One error line as a sentence: a full stop when it has none."""
    return line if line.endswith((".", "!", "?", ":")) else line + "."
