"""Agent roles, the same for every provider (Codex, Claude Code, Gemini CLI, opencode).

Every agent Joinery runs has one role:

* ``lead``: the chat's own request. Changes the project, starts tasks.
* ``worker``: a parallel task, or the fix a debugger proposed. Changes staged files;
  can't start tasks.
* ``reviewer``: reads a submitted change and reports findings. Read-only.
* ``debugger``: reads errors and logs, reproduces offline and proposes a fix for a
  worker to make. Read-only.
* ``playtester``: plays the game headless with ``playtest`` and writes a report of
  bugs, softlocks and balance notes (ADR 0038). Read-only.
* ``accessibility`` and ``performance``: read their check (``access_check``,
  ``perf_check``) and the code it points at, and word the fix a worker makes for a
  finding that needs code (ADR 0049, ADR 0050). Read-only.
* ``designer``: reads the brief, design documents, scenes and scripts for what breaks
  the game or goes against its own brief, and reports each finding with evidence
  through ``report_design`` (the design check). Read-only.
* ``sound-designer``: listens to the level with ``sound_audit`` and writes a short
  report of what is missing and what the sound pass makes. Read-only; the sounds
  themselves are made by scripted tasks, each a reviewed change.

Every role may ask what the game never uses (``tidy_check``); taking it out
(``tidy_remove``) stages a change like any other write, so it is the lead's and the
workers', and the user reviews it (ADR 0074).

Every role may look into the game its gateway is running (``inspect_game``). Playing it
with timed input (``drive_game``) is the lead's and workers'; the playtester and the
debugger may too where the project says so (``[agents] game_input_roles``), and no other
read-only role ever does (ADR 0067).

The role reaches the provider's policy hook as ``JOINERY_AGENT_ROLE`` and the
gateway endpoint as its ``role``; both judge each call against the same tables
here, so no provider can widen what a role may do. No role can approve, accept or
discard a change, or approve or edit the art bible: approval is absent from the
model-facing API, and the read-only roles can't write anything at all.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from joinery.policy import Severity, Verdict
from joinery.read_access import read_only
from joinery.shell_words import simple_commands

Role = Literal[
    "lead",
    "worker",
    "reviewer",
    "debugger",
    "playtester",
    "accessibility",
    "performance",
    "designer",
    "sound-designer",
]
ROLES: tuple[Role, ...] = get_args(Role)
#: The policy hook reads the role from here; absent means ``lead``.
ROLE_ENV = "JOINERY_AGENT_ROLE"
READ_ONLY_ROLES: frozenset[str] = frozenset(
    {
        "reviewer",
        "debugger",
        "playtester",
        "accessibility",
        "performance",
        "designer",
        "sound-designer",
    }
)

#: Search the project's text files (read-only, so every role may).
SEARCH_PROJECT = "search_project"
#: The running game: look (any role) and play with timed input (``may_drive``).
INSPECT_GAME = "inspect_game"
DRIVE_GAME = "drive_game"
#: joinery-registry tools every read-only role may call.
REGISTRY_READS = frozenset(
    {
        "discover_tools",
        "tool_schema",
        "inspect_editor",
        "view_editor",
        "details",
        SEARCH_PROJECT,
        INSPECT_GAME,
        "tidy_check",
    }
)
#: Read-only roles a project may let drive the running game; the rest never may.
MAY_DRIVE_ROLES = frozenset({"playtester", "debugger"})
#: Tools that only one role reports through.
REVIEW_REPORT = "report_review"
DIAGNOSIS_REPORT = "report_diagnosis"
REPRODUCE = "reproduce_error"
DESIGN_REPORT = "report_design"
#: Play the game headless: read-only, so the lead, workers and the playtester may; the
#: gateway lets the lead and workers play only when the user asked (joinery.playtest_gate).
PLAYTEST = "playtest"
#: The accessibility and performance checks: read-only, so the lead and workers may too.
ACCESS_CHECK = "access_check"
PERF_CHECK = "perf_check"
#: What the level is missing to sound like a game: read-only, so any role may.
SOUND_AUDIT = "sound_audit"
#: What the game never uses (/tidy): reading it is open to every role; removing it
#: stages a change like any write, so only the lead and workers may.
TIDY_CHECK = "tidy_check"
TIDY_REMOVE = "tidy_remove"
#: Re-record a kept replay into the change (its input, the level as changed): a staged
#: write like any other, so only the lead and workers may.
RERECORD_REPLAY = "rerecord_replay"
#: Save a design document the user pasted, staged for their review: the lead's alone.
SAVE_DESIGN_DOC = "save_design_doc"
ROLE_ONLY = frozenset({REVIEW_REPORT, DIAGNOSIS_REPORT, REPRODUCE, DESIGN_REPORT})
#: A request's to-do, shown to the user before the work (ADR 0060): the lead's alone.
PROPOSE_TODO = "propose_todo"
#: A to-do's step started, done or skipped while it is built: the lead's alone too.
TODO_STEP = "todo_step"
#: Only the lead starts more agents (parallel tasks and variants) or shows a to-do.
LEAD_ONLY = frozenset(
    {"start_task", "start_variants", PROPOSE_TODO, TODO_STEP, SAVE_DESIGN_DOC}
)


@dataclass(frozen=True)
class RoleSpec:
    """What one role is: its turn and what it may call."""

    role: Role
    #: How Joinery runs its turn: the chat's own, a task, a timed review or a diagnosis.
    turn: Literal[
        "chat", "task", "review", "diagnosis", "playtest", "check", "design", "sound"
    ]
    read_only: bool
    #: joinery-registry tools beyond the shared ones ("*" for every tool).
    registry: frozenset[str]
    #: Seconds a turn of this role may take (0: no limit of its own).
    seconds: int
    #: What the step on its card says while it works.
    step: str


SPECS: dict[str, RoleSpec] = {
    "lead": RoleSpec(
        "lead", "chat", False, frozenset({"*"}) - ROLE_ONLY, 0, "Thinking…"
    ),
    "worker": RoleSpec(
        "worker",
        "task",
        False,
        frozenset({"*"}) - ROLE_ONLY - LEAD_ONLY,
        0,
        "Working…",
    ),
    "reviewer": RoleSpec(
        "reviewer",
        "review",
        True,
        REGISTRY_READS | {REVIEW_REPORT},
        90,
        "Reviewing the change…",
    ),
    "debugger": RoleSpec(
        "debugger",
        "diagnosis",
        True,
        REGISTRY_READS | {DIAGNOSIS_REPORT, REPRODUCE},
        240,
        "Reading the error…",
    ),
    "playtester": RoleSpec(
        "playtester",
        "playtest",
        True,
        REGISTRY_READS | {PLAYTEST},
        # A run of up to 10 minutes of game time, its screenshots and the write-up.
        1200,
        "Playing the game…",
    ),
    "accessibility": RoleSpec(
        "accessibility",
        "check",
        True,
        REGISTRY_READS | {ACCESS_CHECK},
        300,
        "Reading the accessibility check…",
    ),
    "performance": RoleSpec(
        "performance",
        "check",
        True,
        REGISTRY_READS | {PERF_CHECK, PLAYTEST},
        # A check may play the game twice (headless, then in a window) and read code.
        600,
        "Reading the performance profile…",
    ),
    "designer": RoleSpec(
        "designer",
        "design",
        True,
        REGISTRY_READS | {DESIGN_REPORT},
        300,
        "Reading the design…",
    ),
    "sound-designer": RoleSpec(
        "sound-designer",
        "sound",
        True,
        REGISTRY_READS | {SOUND_AUDIT},
        240,
        "Listening to the level…",
    ),
}


def spec(role: str) -> RoleSpec:
    """The role's spec; an unknown role is refused rather than read as the lead."""
    try:
        return SPECS[role or "lead"]
    except KeyError:
        raise ValueError(f"Unknown agent role: {role!r}") from None


def registry_allowed(role: str, tool: str) -> bool:
    """May ``role`` call the joinery-registry tool ``tool``?"""
    allowed = spec(role).registry
    if "*" in allowed:
        excluded = ROLE_ONLY | (LEAD_ONLY if role == "worker" else set())
        return tool not in excluded
    return tool in allowed


# --- What each role is offered --------------------------------------------------------

#: Every tool the joinery-registry MCP proxy serves (servers.joinery_registry.proxy).
REGISTRY_TOOLS: tuple[str, ...] = (
    "discover_tools",
    "tool_schema",
    "edit_scene",
    "inspect_editor",
    "view_editor",
    "details",
    SEARCH_PROJECT,
    "find_recipe",
    INSPECT_GAME,
    DRIVE_GAME,
    "inspect_in_parallel",
    "submit_for_review",
    "precheck",
    "start_task",
    "start_variants",
    "propose_todo",
    TODO_STEP,
    "task_status",
    "playtest",
    "access_check",
    "perf_check",
    "blockout",
    "grow_level",
    "make_variant",
    "sound_audit",
    TIDY_CHECK,
    TIDY_REMOVE,
    RERECORD_REPLAY,
    SAVE_DESIGN_DOC,
    "report_review",
    "report_design",
    "report_diagnosis",
    "reproduce_error",
    "read_tool",
    "call_tool",
)
#: Judged per call by what it calls: a read-only role reaches audited reads only.
CALL_TOOL = "call_tool"
#: call_tool for audited reads alone (joinery.read_access): marked read-only, so an
#: agent may run several side by side, and refused for anything else.
READ_TOOL = "read_tool"
#: The registry tools that call a project tool, judged by the tool they call.
CALL_TOOLS = frozenset({CALL_TOOL, READ_TOOL})


def offered_tools(role: str) -> frozenset[str]:
    """The joinery-registry tools listed to ``role``'s agent: what it may call, so a
    read-only role never sees a writer's schema. Listing is not the gate: the policy
    hook and the gateway still judge every call (``role_violation``, GatewayRPC)."""
    if spec(role).read_only:
        # drive_game is listed to the roles a project may let drive; the call is judged
        # against the project's own setting (may_drive).
        driving = {DRIVE_GAME} if role in MAY_DRIVE_ROLES else set()
        return frozenset(spec(role).registry | CALL_TOOLS | driving) & frozenset(
            REGISTRY_TOOLS
        )
    return frozenset(t for t in REGISTRY_TOOLS if registry_allowed(role, t))


def game_input_roles(project: Path) -> frozenset[str]:
    """``[agents] game_input_roles``: the read-only roles allowed to drive the game."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    table = raw.get("agents", {})
    value = table.get("game_input_roles", []) if isinstance(table, dict) else []
    if not isinstance(value, list):
        return frozenset()
    return frozenset(str(v) for v in value if isinstance(v, str)) & MAY_DRIVE_ROLES


def may_drive(role: str, project: Path | None) -> bool:
    """May ``role`` send input to the running game (``drive_game``)? Writers may; the
    playtester and debugger where the project lists them; other read-only roles never."""
    if not spec(role).read_only:
        return True
    return project is not None and role in game_input_roles(project)


# --- Provider built-in tools offered to read-only roles -------------------------------

#: Claude Code's built-in tools a read-only role is offered (``claude --tools``): its
#: reads, and Bash, which the hook holds to read-only commands.
CLAUDE_READ_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob", "Bash")


# --- Read-only shell ------------------------------------------------------------------

#: Programs that only read. Anything else (an editor, a package manager, a network
#: client, an interpreter) is refused for the read-only roles.
_READERS = frozenset(
    {
        "cat",
        "cd",
        "head",
        "tail",
        "nl",
        "wc",
        "ls",
        "grep",
        "egrep",
        "rg",
        "find",
        "sed",
        "stat",
        "file",
        "pwd",
        "echo",
        "sort",
        "uniq",
        "cut",
        "diff",
        "tree",
        "true",
        "which",
        "paste",
        "tr",
        "comm",
        "column",
        "basename",
        "dirname",
        "realpath",
        "date",
        "md5",
        "md5sum",
        "shasum",
        "sha1sum",
        "sha256sum",
        "xxd",
        "od",
        "strings",
    }
)
_GIT_READS = frozenset({"log", "show", "diff", "status", "blame", "ls-files"})
_FIND_WRITES = frozenset(
    {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprintf", "-fls"}
)
#: Flags that make a reader run a program or write a file.
_WRITE_FLAGS: dict[str, frozenset[str]] = {
    "rg": frozenset({"--pre", "--pre-glob"}),
    "sort": frozenset({"-o", "--output"}),
    "tree": frozenset({"-o"}),
    "git": frozenset({"--output", "-o", "--ext-diff", "--textconv"}),
    "diff": frozenset({"--to-file"}),
}
#: ``sed -n 1,80p``-style scripts: printing only.
_SED_PRINT = re.compile(r"^(?:\d+|\$|/[^/]*/)(?:,(?:\d+|\$|/[^/]*/))?p$")
_WRAPPERS = re.compile(r"^(?:/bin/|/usr/bin/)?(?:ba|z)?sh\s+-l?c\s+")


def _unwrap(command: str) -> str:
    """``bash -lc '…'`` (how Codex runs a command) -> the command inside."""
    text = command.strip()
    if _WRAPPERS.match(text):
        parts = simple_commands(text)
        if parts is not None and len(parts) == 1 and len(parts[0]) >= 3:
            return parts[0][2]
    return text


def _segments(command: str) -> list[list[str]] | None:
    """``command``'s simple commands as words (:func:`joinery.shell_words.simple_commands`,
    the one shell parser the hook uses too)."""
    return simple_commands(command)


#: Git options before the subcommand that only say where or how to read.
_GIT_GLOBAL_VALUE = frozenset({"-C", "--git-dir", "--work-tree", "--namespace"})
_GIT_GLOBAL_FLAGS = frozenset(
    {
        "--no-pager",
        "--bare",
        "--no-replace-objects",
        "--literal-pathspecs",
        "--no-optional-locks",
    }
)
#: ``git -c key=value``: only display settings. Others (core.pager, core.fsmonitor,
#: diff.external, a textconv driver…) can make a read run a program.
_GIT_SAFE_CONFIG = re.compile(
    r"^(?:color(?:\.[\w.-]+)?|core\.quotepath|core\.abbrev|diff\.noprefix|"
    r"diff\.renames|diff\.mnemonicprefix|log\.date|log\.decorate|i18n\.[\w.-]+)="
    r"[^\s]*$",
    re.IGNORECASE,
)
#: Environment assignments before a command that only change how it prints.
_SAFE_ENV = re.compile(
    r"^(?:LC_[A-Z]+|LANG|LANGUAGE|TZ|NO_COLOR|CLICOLOR|COLUMNS|TERM)=\S*$"
)


def git_subcommand(args: list[str]) -> str | None:
    """The subcommand of ``git <args>`` after its global options; None when an
    option isn't one known to leave reads alone (``-c core.pager=…``, ``--exec-path``)."""
    i = 0
    while i < len(args):
        arg = args[i]
        name, joined, _ = arg.partition("=")
        if arg in _GIT_GLOBAL_FLAGS or (
            joined and name.startswith("--") and name in _GIT_GLOBAL_VALUE
        ):
            i += 1  # --no-pager, --git-dir=x
        elif arg in _GIT_GLOBAL_VALUE:
            if i + 1 >= len(args):
                return None
            i += 2
        elif arg == "-c":
            if i + 1 >= len(args) or not _GIT_SAFE_CONFIG.match(args[i + 1]):
                return None
            i += 2
        elif arg.startswith("-"):
            return None
        else:
            return arg
    return None


#: ``xxd`` options that take a value (``-l 64``); its second file operand is written.
_XXD_VALUE = frozenset(
    {
        "-c",
        "-cols",
        "-g",
        "-groupsize",
        "-l",
        "-len",
        "-n",
        "-name",
        "-o",
        "-s",
        "-seek",
    }
)


def _xxd_reads(args: list[str]) -> bool:
    """``xxd`` reads unless it reverts (``-r``) or names an output file."""
    operands = 0
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("-") and "r" in arg and arg not in _XXD_VALUE:
            return False  # -r, -revert, -pr: write binary back out
        if arg in _XXD_VALUE:
            i += 2
            continue
        if not arg.startswith("-") or arg == "-":
            operands += 1
        i += 1
    return operands <= 1


_DATE_FLAGS = frozenset({"-u", "--utc", "-R", "--rfc-2822"})


def _date_reads(args: list[str]) -> bool:
    """``date``, ``date +%s``, ``date -u``, ``date -r file``: no setting the clock."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "-r":
            i += 2
            continue
        if arg.startswith(("+", "-I", "--iso-8601")) or arg in _DATE_FLAGS:
            i += 1
            continue
        return False  # date -s, date --set, date 0101…: set the clock
    return True


def _reads(tokens: list[str]) -> bool:
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", tokens[0]):
        if not _SAFE_ENV.match(tokens[0]):
            return False  # GIT_EXTERNAL_DIFF=…, PAGER=… make a read run a program
        tokens = tokens[1:]  # LC_ALL=C grep …
    if not tokens:
        return True
    program = Path(tokens[0]).name
    args = tokens[1:]
    if any(arg.split("=", 1)[0] in _WRITE_FLAGS.get(program, ()) for arg in args):
        return False  # rg --pre runs a program; sort -o and git diff --output write
    if program == "git":
        return git_subcommand(args) in _GIT_READS
    if program not in _READERS:
        return False
    if program == "find":
        return not any(arg in _FIND_WRITES for arg in args)
    if program == "sed":
        flags = [a for a in args if a.startswith("-")]
        if any(f not in {"-n", "-E", "-r", "-e"} for f in flags):
            return False
        scripts = [a for a in args if not a.startswith("-")]
        return bool(scripts) and bool(_SED_PRINT.match(scripts[0]))
    if program == "xxd":
        return _xxd_reads(args)
    if program == "date":
        return _date_reads(args)
    return True


#: Redirections that write nothing: error output dropped or joined to the output
#: (``ls x 2>/dev/null``, ``rg y 2>&1 | head``).
_HARMLESS_REDIRECT = re.compile(
    r"(?<![\w<>&|])(?:[12]?>\s*/dev/null|2>&1)(?=$|[\s;|)])"
)


def not_read_only(command: str) -> str:
    """Why ``command`` isn't known to only read: the first program that may write or
    run another, or "a redirection or substitution"; "" when every part only reads."""
    inner = _HARMLESS_REDIRECT.sub(" ", _unwrap(command))
    if re.search(r"[<>`]|\$\(|&(?!&)", inner.replace("&&", "")):
        return "a redirection or substitution"
    segments = _segments(inner)
    if segments is None:
        return "a command Joinery can't parse"
    for words in segments:
        if not _reads(words):
            program = next(
                (
                    w
                    for w in words
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", w)
                ),
                words[0] if words else "",
            )
            name = Path(program).name
            if name == "git":
                sub = git_subcommand(words[words.index(program) + 1 :])
                return f"`git {sub}`" if sub else "`git` with those options"
            return f"`{name}`"
    return ""


def read_only_command(command: str) -> bool:
    """True when every part of ``command`` only reads: no redirection (beyond
    dropping or joining error output), no substitution, nothing run in the
    background, only known readers."""
    return not not_read_only(command)


def unknown_reader(command: str) -> str:
    """The refusal's reason for a shell command a read-only role may not run."""
    why = not_read_only(command)
    return (
        f"This agent only reads: {why} isn't a command Joinery knows only reads "
        "(cat, ls, rg, stat, git log/diff/show and other plain readers are)."
        if why
        else ""
    )


# --- The policy -----------------------------------------------------------------------

#: Native tools of the four providers that write or delegate; refused for read-only roles.
_WRITERS = frozenset(
    {
        "write",
        "edit",
        "multiedit",
        "notebookedit",
        "apply_patch",
        "write_file",
        "replace",
        "patch",
        "task",
        "agent",
    }
)


def native_writer(native: str) -> bool:
    """A provider's own tool that writes or delegates (Write, apply_patch, Task…)."""
    return native.lower() in _WRITERS


NO_DRIVING = (
    "This agent may look at the running game (inspect_game) but not play it: the "
    "project doesn't let this role send input ([agents] game_input_roles)."
)


def refusal(role: str) -> str:
    if role == "reviewer":
        return (
            "The reviewer only reads. Report what you found with report_review; "
            "the author agent makes the fixes and the user decides."
        )
    if role == "debugger":
        return (
            "The debugger only reads and reproduces offline. Report the cause and the "
            "fix with report_diagnosis; a worker makes the change."
        )
    if role == "playtester":
        return (
            "The playtester only plays and reads. Call playtest, then write your "
            "report; Journeyman makes the fixes the user asks for."
        )
    if role == "designer":
        return (
            "The design check only reads. Report each finding with its evidence "
            "through report_design; Journeyman makes the fixes the user asks for."
        )
    if role in {"accessibility", "performance"}:
        return (
            f"The {role} agent only reads. Write the fix as your reply; a worker "
            "makes the change and the user reviews it."
        )
    if role == "sound-designer":
        return (
            "The sound designer only listens and reads. Call sound_audit, then write "
            "your report; Joinery makes the sounds as reviewed changes."
        )
    return "This agent's role can't make that call."


def role_violation(
    role: str,
    kind: str,
    server: str,
    tool: str,
    command: str = "",
    native: str = "",
    arguments: Mapping[str, Any] | None = None,
    project: Path | None = None,
) -> Verdict | None:
    """What ``role`` may not do, beyond Rule Zero (which the caller judges first).

    ``kind`` is the hook's canonical event kind: "mcp", "bash", "fetch" or "other";
    ``native`` is the provider's own tool name for "other".
    """
    role = role or "lead"
    if role not in SPECS:
        return Verdict(Severity.DENY, f"Unknown agent role {role!r}.")
    registry = server.replace("_", "-") == "joinery-registry"
    if not spec(role).read_only:
        if kind == "mcp" and registry and not registry_allowed(role, tool):
            return Verdict(
                Severity.DENY,
                "A task works without a to-do of its own; do the work."
                if tool in {PROPOSE_TODO, TODO_STEP}
                else "A task can't start further tasks; do this part yourself."
                if tool in LEAD_ONLY
                else "That tool belongs to another agent role.",
            )
        return None
    if kind == "mcp":
        if registry:
            if registry_allowed(role, tool):
                return None
            if tool == DRIVE_GAME:
                return (
                    None
                    if may_drive(role, project)
                    else Verdict(Severity.DENY, NO_DRIVING)
                )
            # call_tool and read_tool are judged by what they call: only audited
            # reads pass.
            values = arguments or {}
            if tool in CALL_TOOLS and read_only(
                str(values.get("server", "")), str(values.get("tool", ""))
            ):
                return None
            return Verdict(Severity.DENY, refusal(role))
        return Verdict(Severity.DENY, refusal(role))
    if kind == "bash":
        why = unknown_reader(command)
        return Verdict(Severity.DENY, f"{refusal(role)} {why}") if why else None
    if kind == "fetch":
        return Verdict(Severity.DENY, "Read-only agents work offline; no web access.")
    if native_writer(native):
        return Verdict(Severity.DENY, refusal(role))
    return None


def call_allowed(role: str, server: str, tool: str) -> bool:
    """A ``call_tool`` through the registry: read-only roles reach audited reads only."""
    return not spec(role).read_only or read_only(server, tool)


# --- Settings -------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewerSettings:
    """``[agents] reviewer`` and ``reviewer_model`` in joinery.toml."""

    enabled: bool = True
    #: "" = the same model as the agent whose change it reviews.
    model: str = ""
    #: Times the reviewer may send a change back before it opens with notes.
    rounds: int = 2
    seconds: int = 90


def reviewer_settings(project: Path) -> ReviewerSettings:
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ReviewerSettings()
    table = raw.get("agents", {})
    if not isinstance(table, dict):
        return ReviewerSettings()
    enabled = table.get("reviewer", True)
    model = table.get("reviewer_model", "")
    rounds = table.get("review_rounds", 2)
    return ReviewerSettings(
        enabled=enabled if isinstance(enabled, bool) else True,
        model=model.strip()[:120] if isinstance(model, str) else "",
        rounds=max(0, min(rounds, 3))
        if isinstance(rounds, int) and not isinstance(rounds, bool)
        else 2,
    )


_AGENTS_TABLE = re.compile(r"(?ms)^\[agents\][^\n]*\n(.*?)(?=^\[|\Z)")


def set_agents_values(project: Path, values: dict[str, str]) -> None:
    """Write ``key = value`` lines (TOML values as written) into ``[agents]`` of
    joinery.toml, keeping every other line. Raises ValueError when the result
    would not read back."""
    path = project / "joinery.toml"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    match = _AGENTS_TABLE.search(text)
    if match:
        body = match.group(1)
        for key, value in values.items():
            line = re.compile(rf"(?m)^{key}\s*=.*$")
            if line.search(body):
                body = line.sub(f"{key} = {value}", body, count=1)
            else:
                body = (
                    body.rstrip("\n")
                    + ("\n" if body.strip() else "")
                    + (f"{key} = {value}\n")
                )
        if not body.endswith("\n"):
            body += "\n"
        updated = text[: match.start(1)] + body + text[match.end(1) :]
    else:
        lead = text.rstrip("\n") + "\n\n" if text.strip() else ""
        updated = (
            lead + "[agents]\n" + "".join(f"{k} = {v}\n" for k, v in values.items())
        )
    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"joinery.toml can't be read: {exc}") from None
    path.write_text(updated, encoding="utf-8")


def set_reviewer(project: Path, *, enabled: bool | None, model: str | None) -> None:
    """Save the reviewer choices, keeping every other line of joinery.toml."""
    values: dict[str, str] = {}
    if enabled is not None:
        values["reviewer"] = "true" if enabled else "false"
    if model is not None:
        clean = model.strip()
        if len(clean) > 120 or any(c in clean for c in '"\\\n'):
            raise ValueError("Name the reviewer's model in one plain line.")
        values["reviewer_model"] = '"' + clean + '"'
    set_agents_values(project, values)
    saved = reviewer_settings(project)
    if (enabled is not None and saved.enabled is not enabled) or (
        model is not None and saved.model != model.strip()
    ):
        raise ValueError("joinery.toml keeps [agents] in a form Joinery can't edit.")
