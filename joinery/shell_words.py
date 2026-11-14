"""The commands a shell line actually runs, as argument lists.

The hook's command guards judge invocations, not text: a heredoc that writes a log,
an ``echo`` or a commit message that merely names a guarded command runs nothing,
so it is not refused. What does run is found wherever it hides: after ``&&``, ``;``
and pipes, inside ``$(…)``, backticks and ``<(…)``, behind ``sudo``/``env``/``uv
run``-style wrappers, in ``bash -c '…'``, ``eval``, ``xargs`` and ``find -exec``, and
in a heredoc or here-string fed to a shell. A line that doesn't parse raises
:class:`ShellParseError`, and the caller falls back to judging the raw text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath

_MAX_DEPTH = 8

#: Programs that run their arguments (after their own options) as a command.
_WRAPPERS = frozenset(
    {
        "sudo",
        "doas",
        "env",
        "nohup",
        "time",
        "command",
        "builtin",
        "exec",
        "nice",
        "ionice",
        "timeout",
        "gtimeout",
        "setsid",
        "stdbuf",
        "caffeinate",
        "chronic",
        "watch",
        "xargs",
        "flock",
        "unbuffer",
        "arch",
    }
)
#: Wrappers whose first positional argument is a value, not the command.
_VALUE_FIRST = frozenset({"timeout", "gtimeout", "flock"})
#: Runners: ``uv run X``, ``poetry run X``, ``pipx run X``, ``pdm run X``…
_RUNNERS = frozenset({"uv", "poetry", "pipx", "pdm", "hatch", "rye", "pixi", "conda"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "ash"})
_KEYWORDS = frozenset(
    {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "!", "{", "}"}
    | {"esac", "time", "coproc"}
)
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?\+?=")
_REDIRECT = re.compile(
    r"(?:\d+|\{[A-Za-z_]\w*\})?(?:<<<|<<-?|>>|>\||<>|<&|>&|&>>?|<|>)"
)


class ShellParseError(ValueError):
    """The line doesn't parse as shell (an unterminated quote, say)."""


@dataclass
class _Command:
    words: list[str] = field(default_factory=list)
    #: Heredoc bodies and here-strings: data, unless the command is a shell.
    stdin: list[str] = field(default_factory=list)
    #: Commands joined by pipes share a number.
    pipeline: int = 0


def program_name(word: str) -> str:
    """``/usr/bin/Butler.exe`` -> ``butler``: the bare, lower-cased program name."""
    name = PureWindowsPath(PurePosixPath(word).name).name.lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def invocations(command: str) -> list[tuple[str, ...]]:
    """Every argument list the shell line would run (see the module docstring).

    A wrapped command appears both whole and unwrapped (``sudo pip install x`` and
    ``pip install x``), so a guard need only look at ``argv[0]``.
    """
    found: list[tuple[str, ...]] = []
    _collect(command, 0, found)
    return found


#: Operators that only join commands one after another or into a pipe.
_JOINS = frozenset({"|", "&&", "||", ";"})


def simple_commands(command: str) -> list[list[str]] | None:
    """``command``'s commands as words when it is only simple commands joined by
    ``&&``, ``||``, ``;``, ``|`` or newlines (quotes respected: ``rg 'a|b'`` is one
    command). None when it doesn't parse, or uses anything else: redirection,
    substitution, a heredoc, a subshell or running in the background."""
    try:
        parser = _Parser(command)
        commands, nested = parser.parse()
    except ShellParseError:
        return None
    if nested or parser.operators - _JOINS or any(c.stdin for c in commands):
        return None
    return [list(c.words) for c in commands]


def _collect(source: str, depth: int, found: list[tuple[str, ...]]) -> None:
    if depth > _MAX_DEPTH:
        raise ShellParseError("nested too deeply")
    commands, nested = _Parser(source).parse()
    for text in nested:
        _collect(text, depth + 1, found)
    for index, cmd in enumerate(commands):
        words = _strip_prefix(cmd.words)
        if words:
            _views(words, depth, found)
        if not _reads_script(words):
            continue
        # ``bash <<EOF … EOF``, ``bash <<< '…'`` and ``… | sh`` run what they are fed:
        # the shell's own stdin and everything earlier in its pipeline.
        for body in cmd.stdin:
            _collect(body, depth + 1, found)
        for earlier in commands[:index]:
            if earlier.pipeline != cmd.pipeline:
                continue
            for body in earlier.stdin:
                _collect(body, depth + 1, found)
            upstream = _strip_prefix(earlier.words)[1:]
            if upstream:
                _collect(" ".join(upstream), depth + 1, found)


def _reads_script(words: list[str]) -> bool:
    """A shell that reads its script from stdin (no ``-c``, no script file)."""
    if not words or program_name(words[0]) not in _SHELLS:
        return False
    if _shell_script(words) is not None:
        return False
    rest = [w for w in words[1:] if not w.startswith(("-", "+"))]
    return not rest


def _strip_prefix(words: list[str]) -> list[str]:
    """Drop keywords (``if``, ``then``, ``!``…) and ``VAR=value`` assignments."""
    i = 0
    while i < len(words) and (words[i] in _KEYWORDS or _ASSIGNMENT.match(words[i])):
        i += 1
    return words[i:]


def _shell_script(words: list[str]) -> str | None:
    """The script of ``bash -c '…'`` / ``bash -lc '…'``, if that's the form."""
    for i, word in enumerate(words[1:], start=1):
        if word == "--":
            return None
        if word.startswith("-") and not word.startswith("--") and "c" in word[1:]:
            return words[i + 1] if i + 1 < len(words) else ""
        if not word.startswith(("-", "+")):
            return None
    return None


def _views(words: list[str], depth: int, found: list[tuple[str, ...]]) -> None:
    found.append(tuple(words))
    name = program_name(words[0])
    args = words[1:]
    if name in _SHELLS:
        script = _shell_script(words)
        if script:
            _collect(script, depth + 1, found)
        return
    if name == "eval":
        _collect(" ".join(args), depth + 1, found)
        return
    if name in {"find", "fd"}:
        for i, arg in enumerate(args):
            if arg in {"-exec", "-execdir", "-ok", "-okdir", "-x", "-X", "--exec"}:
                rest = args[i + 1 :]
                end = next(
                    (j for j, w in enumerate(rest) if w in {";", "\\;", "+"}), len(rest)
                )
                if rest[:end]:
                    _views(_strip_prefix(rest[:end]), depth + 1, found)
        return
    inner: list[str] | None = None
    if name in _WRAPPERS:
        inner = _after_options(args, _VALUED.get(name, frozenset()), env=name == "env")
        if inner and name in _VALUE_FIRST:
            inner = inner[1:]
    elif name in _RUNNERS:
        rest = _after_options(args, _RUNNER_VALUED)
        if rest[:1] in (["run"], ["exec"], ["x"]):
            inner = _after_options(rest[1:], _RUNNER_VALUED)
        elif rest[:2] == ["tool", "run"]:
            inner = _after_options(rest[2:], _RUNNER_VALUED)
    elif name == "rtk":  # rtk git status, rtk proxy <command>
        inner = args[1:] if args[:1] == ["proxy"] else args
    elif name in {"uvx", "npx", "pnpx", "bunx"}:
        inner = _after_options(args, _RUNNER_VALUED)
    elif re.fullmatch(r"python(?:\d+(?:\.\d+)?)?|py", name):
        rest = _after_options(args, frozenset({"-W", "-X", "-c"}))
        if "-m" in args and args.index("-m") + 1 < len(args):
            inner = args[args.index("-m") + 1 :]
        elif "-c" not in args:
            inner = rest
    if inner and len(inner) == 1 and any(c in inner[0] for c in " \t;&|"):
        _collect(inner[0], depth + 1, found)  # watch 'joinery …' runs a string
    elif inner:
        _views(_strip_prefix(inner), depth + 1, found)


#: Wrapper options that take a separate value (``sudo -u root``, ``nice -n 5``…).
_VALUED: dict[str, frozenset[str]] = {
    "sudo": frozenset({"-u", "-g", "-C", "-p", "-h", "-U", "-r", "-t", "-D", "-R"}),
    "doas": frozenset({"-u", "-C"}),
    "env": frozenset({"-u", "-C", "-P", "--unset", "--chdir"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "ionice": frozenset({"-c", "-n", "-p", "-P", "-u"}),
    "timeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
    "gtimeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
    "xargs": frozenset({"-I", "-L", "-n", "-P", "-d", "-s", "-E", "-a", "-J", "-R"}),
    "flock": frozenset({"-w", "-E", "--timeout", "--conflict-exit-code"}),
    "watch": frozenset({"-n", "-d", "--interval", "--differences"}),
    "arch": frozenset({"-arch"}),
}
_RUNNER_VALUED = frozenset(
    {"--with", "--from", "--python", "-p", "--project", "--directory", "--env-file"}
    | {"--package", "-w", "--with-requirements", "--group", "--extra", "--index"}
    | {"-n", "--name", "-e", "--environment", "--spec"}
)


def _after_options(
    args: list[str], valued: frozenset[str], *, env: bool = False
) -> list[str]:
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if env and _ASSIGNMENT.match(arg):
            i += 1
            continue
        if arg.startswith("-") and len(arg) > 1:
            i += 2 if arg in valued else 1
            continue
        break
    return args[i:]


class _Parser:
    """A small POSIX-shell tokenizer: words, operators, quotes, substitutions and
    heredocs; enough to tell commands from the data they are given."""

    def __init__(self, source: str) -> None:
        self.src = source.replace("\\\r\n", "").replace("\\\n", "")
        self.i = 0
        self.commands: list[_Command] = []
        self.nested: list[str] = []
        self.pipeline = 0
        #: Every operator the line uses (``|``, ``&&``, ``&``, ``redirect``…).
        self.operators: set[str] = set()
        self.current = _Command()
        self.pending: list[tuple[str, bool, bool, _Command]] = []

    def parse(self) -> tuple[list[_Command], list[str]]:
        src = self.src
        while self.i < len(src):
            ch = src[self.i]
            if ch == "\n":
                self.i += 1
                self._end_command()
                self._read_heredocs()
            elif ch in " \t\r":
                self.i += 1
            elif ch == "#":
                end = src.find("\n", self.i)
                self.i = len(src) if end < 0 else end
            elif src.startswith("|&", self.i) or (
                ch == "|" and src[self.i + 1 : self.i + 2] != "|"
            ):
                op = "|&" if src.startswith("|&", self.i) else "|"
                self.operators.add(op)
                self.i += len(op)
                self._end_command(piped=True)
            elif src.startswith(("&&", "||", ";;"), self.i):
                self.operators.add(src[self.i : self.i + 2])
                self.i += 2
                self._end_command()
            elif ch in ";()" or (ch == "&" and not src.startswith("&>", self.i)):
                self.operators.add(ch)
                self.i += 1
                self._end_command()
            elif src.startswith(("<(", ">("), self.i):
                self.operators.add(src[self.i : self.i + 2])
                self.current.words.append(self._word())
            elif m := _REDIRECT.match(src, self.i):
                self.operators.add("redirect")
                self._redirect(m.group(0))
            else:
                start = self.i
                self.current.words.append(self._word())
                if self.i == start:
                    raise ShellParseError(f"unexpected {ch!r}")
        self._end_command()
        if self.pending:
            # An unterminated heredoc reads to the end, as bash does (with a warning).
            self.pending.clear()
        return self.commands, self.nested

    def _end_command(self, *, piped: bool = False) -> None:
        if self.current.words or self.current.stdin:
            self.commands.append(self.current)
        self.current = _Command(pipeline=self.pipeline if piped else self.pipeline + 1)
        if not piped:
            self.pipeline += 1

    def _redirect(self, op: str) -> None:
        self.i += len(op)
        bare = op.lstrip("0123456789")
        if bare.startswith("{"):
            bare = bare[bare.index("}") + 1 :]
        if bare in {">&", "<&"}:
            m = re.compile(r"\d+-?|-").match(self.src, self.i)
            if m:
                self.i = m.end()
                return
        while self.i < len(self.src) and self.src[self.i] in " \t":
            self.i += 1
        if self.i >= len(self.src) or self.src[self.i] in "\n;|&()":
            raise ShellParseError(f"{op} has no target")
        start = self.i
        target = self._word()
        if bare in {"<<", "<<-"}:
            raw = self.src[start : self.i]
            quoted = any(q in raw for q in "'\"\\")
            self.pending.append((target, bare == "<<-", quoted, self.current))
        elif bare == "<<<":
            self.current.stdin.append(target)

    def _read_heredocs(self) -> None:
        for delimiter, strip_tabs, quoted, owner in self.pending:
            lines: list[str] = []
            while self.i < len(self.src):
                end = self.src.find("\n", self.i)
                end = len(self.src) if end < 0 else end
                line = self.src[self.i : end]
                self.i = min(end + 1, len(self.src))
                check = line.lstrip("\t") if strip_tabs else line
                if check == delimiter:
                    break
                lines.append(check)
            body = "\n".join(lines)
            owner.stdin.append(body)
            if not quoted:
                self._substitutions(body)
        self.pending.clear()

    def _substitutions(self, text: str) -> None:
        """``$(…)`` and backticks inside an unquoted heredoc body still run."""
        j = 0
        while j < len(text):
            if text[j] == "\\":
                j += 2
            elif text.startswith("$(", j):
                end = _match_paren(text, j + 2)
                self.nested.append(text[j + 2 : end])
                j = end + 1
            elif text[j] == "`":
                end = _backtick_end(text, j + 1)
                self.nested.append(_unescape_backticks(text[j + 1 : end]))
                j = end + 1
            else:
                j += 1

    def _word(self) -> str:
        src = self.src
        out: list[str] = []
        while self.i < len(src):
            ch = src[self.i]
            if ch in " \t\r\n;|&()":
                break
            if ch in "<>":
                if src.startswith(("<(", ">("), self.i):
                    end = _match_paren(src, self.i + 2)
                    self.nested.append(src[self.i + 2 : end])
                    out.append(src[self.i : end + 1])
                    self.i = end + 1
                    continue
                break
            if ch == "\\":
                out.append(src[self.i + 1 : self.i + 2])
                self.i += 2
            elif ch == "'":
                end = src.find("'", self.i + 1)
                if end < 0:
                    raise ShellParseError("unterminated single quote")
                out.append(src[self.i + 1 : end])
                self.i = end + 1
            elif ch == '"':
                out.append(self._double_quoted())
            elif src.startswith("$'", self.i):
                end = self.i + 2
                while end < len(src) and src[end] != "'":
                    end += 2 if src[end] == "\\" else 1
                if end >= len(src):
                    raise ShellParseError("unterminated $'' quote")
                out.append(src[self.i + 2 : end])
                self.i = end + 1
            elif src.startswith("$(", self.i):
                out.append(self._dollar_paren())
            elif src.startswith("${", self.i):
                end = _match_brace(src, self.i + 2)
                out.append(src[self.i : end + 1])
                self.i = end + 1
            elif ch == "`":
                end = _backtick_end(src, self.i + 1)
                self.nested.append(_unescape_backticks(src[self.i + 1 : end]))
                out.append(src[self.i : end + 1])
                self.i = end + 1
            else:
                out.append(ch)
                self.i += 1
        return "".join(out)

    def _dollar_paren(self) -> str:
        start = self.i
        end = _match_paren(self.src, self.i + 2)
        self.nested.append(self.src[self.i + 2 : end])
        self.i = end + 1
        return self.src[start : self.i]

    def _double_quoted(self) -> str:
        src = self.src
        self.i += 1
        out: list[str] = []
        while self.i < len(src):
            ch = src[self.i]
            if ch == '"':
                self.i += 1
                return "".join(out)
            if ch == "\\" and self.i + 1 < len(src):
                nxt = src[self.i + 1]
                out.append(nxt if nxt in '$`"\\' else ch + nxt)
                self.i += 2
            elif src.startswith("$(", self.i):
                out.append(self._dollar_paren())
            elif src.startswith("${", self.i):
                end = _match_brace(src, self.i + 2)
                out.append(src[self.i : end + 1])
                self.i = end + 1
            elif ch == "`":
                end = _backtick_end(src, self.i + 1)
                self.nested.append(_unescape_backticks(src[self.i + 1 : end]))
                out.append(src[self.i : end + 1])
                self.i = end + 1
            else:
                out.append(ch)
                self.i += 1
        raise ShellParseError("unterminated double quote")


def _match_paren(src: str, i: int) -> int:
    """Index of the ``)`` closing a ``$(`` whose body starts at ``i``."""
    depth = 1
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "'":
            end = src.find("'", i + 1)
            if end < 0:
                raise ShellParseError("unterminated single quote")
            i = end + 1
            continue
        if ch == '"':
            i = _skip_double(src, i + 1)
            continue
        if ch == "`":
            i = _backtick_end(src, i + 1) + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ShellParseError("unterminated $(")


def _skip_double(src: str, i: int) -> int:
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            i += 2
        elif ch == '"':
            return i + 1
        elif src.startswith("$(", i):
            i = _match_paren(src, i + 2) + 1
        elif ch == "`":
            i = _backtick_end(src, i + 1) + 1
        else:
            i += 1
    raise ShellParseError("unterminated double quote")


def _match_brace(src: str, i: int) -> int:
    depth = 1
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ShellParseError("unterminated ${")


def _backtick_end(src: str, i: int) -> int:
    while i < len(src):
        if src[i] == "\\":
            i += 2
            continue
        if src[i] == "`":
            return i
        i += 1
    raise ShellParseError("unterminated backtick")


def _unescape_backticks(text: str) -> str:
    return re.sub(r"\\([$`\\])", r"\1", text)
