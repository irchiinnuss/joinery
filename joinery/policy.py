"""Creation-only rules applied to tool calls, commands and URLs.

A refusal carries a severity, because the two cases deserve different handling:

* ``STOP`` — a Rule Zero violation: something tried to *generate* an asset. The
  whole turn halts and the user is told, rather than the agent quietly probing
  for another route. Everything in ``policy/denylist.toml`` is of this kind:
  generative services and asset sites.
* ``DENY`` — not available here, which is a configuration state, not a
  violation: an MCP server the project did not enable, or a narrow escape-hatch
  tool on a server that is otherwise fine. The call is refused and the agent is
  free to pick another path.

No tool is disabled or removed; everything stays callable and is judged per call.
"""

from __future__ import annotations

import itertools
import re
import shlex
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

from joinery.catalog import ROOT
from joinery.shell_words import ShellParseError, invocations, program_name

DENYLIST_PATH = ROOT / "policy" / "denylist.toml"


class Severity(StrEnum):
    """How hard a refusal lands."""

    STOP = "stop"  # Rule Zero violation: halt the turn and tell the user.
    DENY = "deny"  # Not available here: refuse the call, the turn continues.


@dataclass(frozen=True)
class Verdict:
    """A refusal: why, and how hard."""

    severity: Severity
    reason: str

    def __str__(self) -> str:
        return self.reason


_SEGMENTS = re.compile(r"&&|\|\||[;|\n]")
_INSTALLERS = re.compile(
    r"^(?:\S+=\S+\s+)*(?:sudo\s+)?"
    r"(?:(?:python3?\s+-m\s+)?pip3?\s+install"
    r"|uv\s+(?:add|pip\s+install|tool\s+(?:install|run))|uvx"
    r"|npm\s+(?:i|install|add)|pnpm\s+(?:i|install|add)|yarn\s+add|npx"
    r"|pipx\s+(?:install|run)|brew\s+install|conda\s+install)\b"
)
_VERSION_SUFFIX = re.compile(r"(?<=.)(?:[=<>!~]=?|@).*$")


def normalise(package: str) -> str:
    return re.sub(r"[-_.]+", "-", package).lower()


@dataclass(frozen=True)
class Denylist:
    packages: frozenset[str]
    hosts: tuple[str, ...]


def load_denylist(path: Path = DENYLIST_PATH) -> Denylist:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return Denylist(
        packages=frozenset(normalise(p) for p in raw["packages"]),
        hosts=tuple(h.lower() for h in raw["hosts"]),
    )


def _host_matches(host: str, denied: str) -> bool:
    return host == denied or host.endswith("." + denied)


def generative_names(denylist: Denylist) -> frozenset[str]:
    """Names that identify a generative service, for judging MCP server names.

    Built from the denylist: package names as written, and the leading label of
    each denied host (``meshy.ai`` -> ``meshy``). Matching is exact, so an
    unrecognised name reads as an ordinary ``DENY`` rather than a false alarm;
    the sandbox and the CI asset gate are the backstop for that.
    """
    names = set(denylist.packages)
    for host in denylist.hosts:
        names.add(normalise(host.split(".", 1)[0]))
    return frozenset(names)


#: `joinery timeline restore` and `start` are the user's own choices in the
#: Timeline tab: an agent asks for a change and the user reviews it instead.
_USER_ONLY = re.compile(r"\bjoinery\b(?:\s+\S+)*?\s+timeline\s+(?:restore|start)\b")


#: Adding reference images and design documents records them as the user's own
#: import (`human-import`), so only the user does it: in the new-project wizard
#: or Project apps (/setup), never an agent's shell command.
_USER_IMPORT = re.compile(
    r"\bjoinery\b(?:\s+\S+)*?\s+(?:references\s+add\b|.*\s--(?:reference|design-doc)\b)"
)


#: Publishing a game is the user's own act: the Share page's Publish button, or
#: their terminal. An agent never runs `joinery share itch`, nor butler itself.
_USER_PUBLISH = re.compile(
    r"\bjoinery\b(?:\s+\S+)*?\s+share\s+itch\b"
    r"|(?:^|[\s/\\\"'])butler(?:\.exe)?[\"']?\s+(?:\S+\s+)*?(?:push|login|logout|upgrade)\b",
    re.IGNORECASE,
)


#: Sharing kits between projects is the user's own act: Export kit… and Import
#: kit… in the Kits section, or their terminal. Agents use imported kits only.
_USER_KITS = re.compile(
    r"\bjoinery(?:[\\/]cli\.py|\.cli)?\b(?:\s+\S+)*?\s+kit\s+(?:import|export|remove)\b"
)


#: `joinery tidy` reads freely; removing (or accepting a removal at once), the keep
#: list and clearing caches are the user's. An agent removes with tidy_remove, which
#: stages a reviewed change.
_USER_TIDY = re.compile(
    r"\bjoinery\b(?:\s+\S+)*?\s+tidy\b(?:\s+\S+)*?\s+"
    r"--(?:remove|yes|keep|unkeep|clear-caches)\b"
)


#: The wizard's answers given at creation draft the look, which is then approved as
#: the user's own (art_approval): only the user passes them, never an agent.
_USER_ANSWERS = re.compile(r"\bjoinery\b(?:\s+\S+)*?\s+new\b.*\s--answers\b")


_TIDY_FLAGS = ("--remove", "--yes", "--keep", "--unkeep", "--clear-caches")
_IMPORT_FLAGS = ("--reference", "--design-doc")


def _flag(args: tuple[str, ...], flags: tuple[str, ...]) -> bool:
    """A flag given as ``--flag``, ``--flag=value`` (or a longer ``--flag-…``)."""
    return any(
        arg == flag or arg.startswith((flag + "=", flag + "-"))
        for arg in args
        for flag in flags
    )


def _follows(args: tuple[str, ...], word: str, then: frozenset[str]) -> bool:
    return any(a == word and b in then for a, b in itertools.pairwise(args))


def _is_joinery(word: str) -> bool:
    name = program_name(word)
    return name in {"joinery", "joinery.cli"} or word.replace("\\", "/").endswith(
        "joinery/cli.py"
    )


#: Each guard's message, in the order they are judged.
_MESSAGES = {
    "tidy": (
        "Removing unused content is a reviewed change: call tidy_remove with "
        "the findings' ids (tidy_check lists them), and the user reviews it. "
        "The keep list and Joinery's caches are the user's, in /tidy."
    ),
    "publish": (
        "Publishing to itch.io is the user's own choice: they press Publish on "
        "the Share page. Tell the user the builds are ready instead."
    ),
    "timeline": (
        "Restoring a file from the Timeline, or starting one, is the user's "
        "choice in the Timeline tab. Ask the user, or make the change as a "
        "reviewed edit."
    ),
    "kits": (
        "Importing, exporting and removing shared kits is the user's choice: "
        "Export kit… and Import kit… in the asset workspace's Kits section. "
        "Use the kits already in the project (kit_list) instead."
    ),
    "import": (
        "Only the user adds reference images and design documents, from "
        "Project apps (/setup): they are recorded as the user's own import. "
        "Ask the user to add the file there, or to paste the document into the "
        "chat and ask you to save it: save_design_doc stages it for their review."
    ),
    "answers": (
        "Answers given when a project is made count as the user's own approval of "
        "its look, so only the user passes them: in the new-project wizard, or "
        "`joinery new --answers` in their own terminal. Ask the user to run it."
    ),
}


def _guard(argv: tuple[str, ...]) -> str | None:
    """Which user-only guard an actual invocation trips, if any."""
    if program_name(argv[0]) == "butler":
        verbs = {"push", "login", "logout", "upgrade"}
        return "publish" if any(a.lower() in verbs for a in argv[1:]) else None
    if not _is_joinery(argv[0]):
        return None
    args = argv[1:]
    if "tidy" in args and _flag(args[args.index("tidy") :], _TIDY_FLAGS):
        return "tidy"
    if _follows(args, "share", frozenset({"itch"})):
        return "publish"
    if _follows(args, "timeline", frozenset({"restore", "start"})):
        return "timeline"
    if _follows(args, "kit", frozenset({"import", "export", "remove"})):
        return "kits"
    if _follows(args, "references", frozenset({"add"})) or _flag(args, _IMPORT_FLAGS):
        return "import"
    if "new" in args and _flag(args[args.index("new") :], ("--answers",)):
        return "answers"
    return None


def _guard_text(segment: str) -> str | None:
    """The raw-text form, kept for a line that doesn't parse as shell."""
    for name, pattern in (
        ("tidy", _USER_TIDY),
        ("publish", _USER_PUBLISH),
        ("timeline", _USER_ONLY),
        ("kits", _USER_KITS),
        ("import", _USER_IMPORT),
        ("answers", _USER_ANSWERS),
    ):
        if pattern.search(segment):
            return name
    return None


def user_only_violation(command: str) -> Verdict | None:
    """Refuse commands only the user runs, from the editor.

    Judged on what the line actually runs (:mod:`joinery.shell_words`), so a heredoc,
    an ``echo`` or a file's content that merely names one of these commands passes.
    A line that doesn't parse is judged on its raw text, as before.
    """
    try:
        names = [_guard(argv) for argv in invocations(command)]
    except ShellParseError:
        names = [_guard_text(segment) for segment in _SEGMENTS.split(command)]
    order = list(_MESSAGES)
    hits = sorted((n for n in names if n), key=order.index)
    return Verdict(Severity.DENY, _MESSAGES[hits[0]]) if hits else None


def url_violation(url: str, denylist: Denylist) -> Verdict | None:
    host = (urlparse(url).hostname or "").lower()
    for denied in denylist.hosts:
        if _host_matches(host, denied):
            return Verdict(Severity.STOP, f"{denied} is a blocked generative host")
    return None


def command_violation(command: str, denylist: Denylist) -> Verdict | None:
    lowered = command.lower()
    for denied in denylist.hosts:
        if re.search(rf"(?<![\w-]){re.escape(denied)}(?![\w-])", lowered):
            return Verdict(Severity.STOP, f"{denied} is a blocked generative host")
    try:
        segments = [shlex.join(argv) for argv in invocations(command)]
    except ShellParseError:
        segments = [segment.strip() for segment in _SEGMENTS.split(command)]
    for segment in segments:
        if not _INSTALLERS.match(segment):
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        for token in tokens:
            name = normalise(_VERSION_SUFFIX.sub("", token))
            if name in denylist.packages:
                return Verdict(Severity.STOP, f"{name} is a denied generative package")
    return None
