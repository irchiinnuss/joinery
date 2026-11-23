"""Claude Code's documented print/stream-json transport, retaining its session id."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from bridge import trail
from bridge.connect_errors import ConnectError, reason_of, tail
from bridge.models import ModelChoice
from bridge.protocol import (
    AgentEvent,
    Done,
    Message,
    MessageDelta,
    Prompt,
    ToolActivity,
    Usage,
)
from bridge.provider_process import (
    INSTRUCTIONS,
    offered_role,
    proxy_config,
    readonly_command,
    require_provider,
    stable_text,
    stop_process,
    turn_text,
)

#: Per session, a hash of the rules and map its last turn carried (under the project).
SESSIONS_DIR = ".joinery/sessions"
from joinery import machine_health
from joinery.catalog import ROOT
from joinery.chat_attachments import encoded
from joinery.hook import shell_search
from joinery.platform_support import launch_argv
from joinery.roles import CLAUDE_READ_TOOLS, ROLE_ENV, spec
from joinery.stage_access import NO_STAGE, STAGE_ENV
from joinery.tool_names import for_agent

#: The tools a Joinery-run Claude session's policy hook judges: the project's own
#: gate, plus Claude's file tools, so the staged-change folders hold for them too.
SESSION_MATCHER = (
    "mcp__.*|Bash|WebFetch|Read|Grep|Glob|LS|NotebookRead|Write|Edit|MultiEdit|"
    "NotebookEdit"
)
from joinery.wiring import _command_str

#: Claude Code's flag that keeps its system prompt the same bytes on every machine and
#: in every working tree: the per-machine sections (cwd, env, repository state) move
#: into the first user message, so the cached prefix is shared (ADR 0056).
DYNAMIC_SECTIONS_FLAG = "--exclude-dynamic-system-prompt-sections"


#: Claude Code's built-in tools a writing role is offered: the ones that can run in a
#: session with no permission prompts (PERMITTED_NATIVE, Bash for a judged read-only
#: search, WebFetch for the hook to judge). Write, Edit, NotebookEdit and the rest
#: would only ever be refused here, and their schemas cost about 7k tokens on every
#: model call of the turn; files change through the registry's call_tool.
CLAUDE_SESSION_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Bash",
    "ToolSearch",
    "Agent",
    "TodoWrite",
    "WebFetch",
)

#: Environment for every Joinery-run Claude session. Claude's own auto memory keeps
#: notes outside the project, unreviewed, and adds its instructions to every call;
#: the project's memory is JOURNEYMAN.md, already in the prompt (ADR 0077). The
#: user's claude.ai connectors (Gmail, Drive, Calendar, generative services) never
#: load: the game's agent has no business in the user's mail, and their ~80 tool
#: names rode on every call (STRICT_MCP_FLAG says the same on the command line).
SESSION_ENV: dict[str, str] = {
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
}
#: Only the MCP servers Joinery passes with --mcp-config (its own registry proxy):
#: none of the user's, the project's or the claude.ai account's.
STRICT_MCP_FLAG = "--strict-mcp-config"
#: A Joinery session is never reachable through Claude's own Remote Control, even
#: with the user's "Enable Remote Control for all sessions": the phone would steer
#: the agent around the sidebar's review. Joinery Remote is the way in (ADR 0093).
NO_VENDOR_REMOTE: dict[str, bool] = {
    "disableRemoteControl": True,
    "remoteControlAtStartup": False,
}


def cache_flags(role: str, stable_system_prompt: bool, tools_flag: bool) -> list[str]:
    """The flags that keep a session's prefix stable and small, where this CLI has
    them: the per-machine system-prompt sections move to the first user message, and
    each role is offered only the built-in tools it can use: a read-only role Claude's
    read tools, every other role CLAUDE_SESSION_TOOLS (the hook still judges every
    call)."""
    flags = [DYNAMIC_SECTIONS_FLAG] if stable_system_prompt else []
    if tools_flag:
        offered = (
            CLAUDE_READ_TOOLS if role and spec(role).read_only else CLAUDE_SESSION_TOOLS
        )
        flags += ["--tools", ",".join(offered)]
    return flags


#: Claude Code's own tools a Joinery session may use without a permission prompt:
#: the file readers, delegation and tool loading (Bash only for a read-only search,
#: which the hook lets through).
PERMITTED_NATIVE = frozenset(
    {
        "Read",
        "Grep",
        "Glob",
        "LS",
        "NotebookRead",
        "Agent",
        "Task",
        "ToolSearch",
        "TodoWrite",
    }
)
NOT_PERMITTED = (
    "{tool} needs a permission prompt, which this session can't show: use the "
    "joinery-registry tools (search_project to search, call_tool to change files)."
)


def permitted(tool: str, arguments: object, inspection_only: bool = False) -> str:
    """Why Claude Code will refuse ``tool`` in a Joinery session ("" when it runs):
    the session passes ``--allowedTools`` for the registry and shows no prompts, so
    only the registry, the read tools and a read-only shell search get through."""
    if tool.startswith(("mcp__joinery-registry__", "mcp__joinery_registry__")):
        return ""
    delegates = tool in {"Agent", "Task"}
    if tool in PERMITTED_NATIVE and not (inspection_only and delegates):
        return ""
    if tool == "Bash" and shell_search(arguments):
        return ""
    return NOT_PERMITTED.format(tool=tool)


def refusal(part: dict[str, Any]) -> str:
    """The refusal text of a ``tool_result`` Claude Code wrote for a call it or the
    policy hook refused ("" for a call that ran, even one that failed on its own)."""
    if not part.get("is_error"):
        return ""
    content = part.get("content", "")
    if isinstance(content, list):
        content = " ".join(
            str(c.get("text", "")) for c in content if isinstance(c, dict)
        )
    text = str(content).strip()
    lowered = text.lower()
    markers = ("permission", "blocked by joinery", "hook", "denied", "not allowed")
    return text[:300] if any(m in lowered for m in markers) else ""


def user_input(prompt: Prompt, known: str = "") -> bytes:
    """What goes to Claude's stdin: plain text, or with attached images one stream-json
    user message whose content is the text and each image as a base64 block. Joinery's
    instructions are the session's system prompt (``--append-system-prompt``), so the
    turn carries only its own text (ADR 0056)."""
    if not prompt.attachments:
        return turn_text(prompt, known=known, agent="claude").encode()
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": turn_text(prompt, images_inline=True, known=known, agent="claude"),
        }
    ]
    for path in prompt.attachments:
        media, data = encoded(Path(path))
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media, "data": data},
            }
        )
    message = {"type": "user", "message": {"role": "user", "content": content}}
    return (json.dumps(message) + "\n").encode()


def _count(source: dict[str, Any], key: str) -> int:
    value = source.get(key)
    return value if isinstance(value, int) and value > 0 else 0


class ClaudeUsage:
    """This session's token use, from Claude Code's stream-json events: the last
    assistant message's usage is what the context holds; each result adds its turn's
    usage to the totals and names the model's context window."""

    def __init__(self) -> None:
        self.model = ""
        self.context = 0
        self.window = 0
        self.input = 0
        self.output = 0
        self.cached = 0

    def read(self, event: dict[str, Any]) -> Usage | None:
        """Take one stream-json event; returns a Usage when a turn's result arrives."""
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self.model = str(event.get("model") or self.model)
        elif kind == "assistant":
            message = event.get("message")
            if isinstance(message, dict):
                self.model = str(message.get("model") or self.model)
                usage = message.get("usage")
                if isinstance(usage, dict):
                    self.context = sum(
                        _count(usage, key)
                        for key in (
                            "input_tokens",
                            "cache_creation_input_tokens",
                            "cache_read_input_tokens",
                            "output_tokens",
                        )
                    )
        elif kind == "result":
            usage = event.get("usage")
            if isinstance(usage, dict):
                cached = _count(usage, "cache_read_input_tokens")
                self.input += (
                    _count(usage, "input_tokens")
                    + _count(usage, "cache_creation_input_tokens")
                    + cached
                )
                self.output += _count(usage, "output_tokens")
                self.cached += cached
            models = event.get("modelUsage")
            if isinstance(models, dict):
                entries = {k: v for k, v in models.items() if isinstance(v, dict)}
                main = entries.get(self.model) or max(
                    entries.values(),
                    key=lambda entry: _count(entry, "outputTokens"),
                    default={},
                )
                self.window = _count(main, "contextWindow") or self.window
            if self.context or self.input or self.output:
                return Usage(
                    model=self.model,
                    context_used=self.context,
                    context_window=self.window,
                    input_tokens=self.input,
                    output_tokens=self.output,
                    cached_tokens=self.cached,
                )
        return None


#: The model aliases every Claude Code takes for ``--model``, strongest first.
CLAUDE_ALIASES = (("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku"))


def claude_configured_model() -> str:
    """The model Claude Code uses when none is passed: ``ANTHROPIC_MODEL``, else the
    ``model`` in its user settings. "" when neither says (Claude Code's own default)."""
    if model := os.environ.get("ANTHROPIC_MODEL", "").strip():
        return model
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        settings = json.loads((config / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    named = settings.get("model") if isinstance(settings, dict) else None
    return named.strip() if isinstance(named, str) else ""


#: The flag that loads only the project's own Claude settings, not the user's.
SETTING_SOURCES_FLAG = "--setting-sources"
#: User settings a Joinery session still needs: how Claude Code signs in and reaches
#: its API (env, apiKeyHelper), and the model it uses by default.
KEPT_USER_SETTINGS = ("env", "apiKeyHelper", "model")


def user_settings_kept() -> dict[str, Any]:
    """From the user's Claude settings, only what signs in and picks the model. A
    Joinery session skips the rest (user hooks, plugins, status line, personal
    instructions): about 24k tokens on every call, and user hooks that rewrite
    commands would fight the project gate."""
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        settings = json.loads((config / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(settings, dict):
        return {}
    return {k: settings[k] for k in KEPT_USER_SETTINGS if k in settings}


def claude_model_choices(
    configured: str, efforts: tuple[str, ...]
) -> list[ModelChoice]:
    """Opus, Sonnet and Haiku by alias. The one Claude Code is set to is the default; a
    configured model that is none of them comes first as its own choice, and with nothing
    configured the default passes no ``--model`` at all (id "")."""
    effort = "medium" if efforts else ""
    lowered = configured.lower()
    choices = [
        ModelChoice(
            id=alias,
            name=name,
            efforts=efforts,
            default_effort=effort,
            is_default=bool(lowered) and alias in lowered,
        )
        for alias, name in CLAUDE_ALIASES
    ]
    if any(choice.is_default for choice in choices):
        return choices
    first = ModelChoice(
        id=configured,
        name=configured or "Claude Code's default",
        efforts=efforts,
        default_effort=effort,
        is_default=True,
    )
    return [first, *choices]


#: Flags every message passes Claude Code; a CLI without one fails on the first message.
REQUIRED_FLAGS = (
    "--output-format",
    "--verbose",
    "--include-partial-messages",
    "--permission-prompts",
    "--allowedTools",
    "--append-system-prompt",
    "--settings",
    "--mcp-config",
    STRICT_MCP_FLAG,
    "--resume",
    "--session-id",
    "--model",
)


def require_flags(help_text: bytes) -> None:
    """Refuse a Claude Code too old for the flags Joinery passes, at connect rather than
    on the first message. An empty help (the probe failed) proves nothing."""
    if not help_text.strip():
        return
    missing = [
        flag
        for flag in REQUIRED_FLAGS
        if re.search(rb"(?<![\w-])" + re.escape(flag.encode()) + rb"\b", help_text)
        is None
    ]
    if missing:
        raise ConnectError(
            f"Claude Code is out of date (it has no {missing[0]}). "
            "Run claude update, then Reconnect.",
            "outdated",
        )


def claude_exit_text(details: str) -> str:
    """What to say when Claude Code exits with an error: why, when its stderr says."""
    reason = reason_of(ValueError(details))
    if reason == "signed_out":
        return "Claude Code needs you to sign in again (claude auth login)."
    if reason == "quota":
        return "Claude Code is out of usage for now. Details are in Output."
    if reason == "outdated":
        return "Claude Code is out of date. Run claude update, then Reconnect."
    # No space left, Docker gone: the machine's, said plainly.
    machine = machine_health.explain(details)
    if machine:
        return machine
    last = details.strip().splitlines()[-1] if details.strip() else ""
    return "Claude Code stopped with an error" + (f": {last}" if last else ".")


def claude_transcript_exists(project: Path, session_id: str) -> bool:
    """Whether Claude Code keeps session [session_id] for [project]: its transcript is
    <config>/projects/<project path, non-alphanumerics as "-">/<id>.jsonl. When the
    config folder is not where Claude Code keeps it by default, trust the id."""
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    projects = config / "projects"
    if not projects.is_dir():
        return True
    folder = re.sub(r"[^A-Za-z0-9]", "-", str(project.resolve()))
    return (projects / folder / f"{session_id}.jsonl").is_file()


class ClaudeSession:
    def __init__(self) -> None:
        self.usage = ClaudeUsage()
        self.gateway_socket: Path | None = None
        self.project = Path()
        self.executable = ""
        self.session_id = str(uuid.uuid4())
        self.started = False
        self.process: asyncio.subprocess.Process | None = None
        self.inspection_only = False
        #: The agent role the policy hook judges this provider's calls by ("" = lead).
        self.role = ""
        #: The one staged change its own shell and file tools may reach ("" = none).
        self.stage = ""
        self.effort_supported = False
        #: This CLI moves its per-machine system-prompt sections into the first user
        #: message (DYNAMIC_SECTIONS_FLAG).
        self.stable_system_prompt = False
        #: This CLI takes ``--tools``, the built-in tools a session is offered.
        self.tools_flag = False
        #: This CLI takes ``--setting-sources`` (load project settings, not the user's).
        self.setting_sources = False

    async def connect(self, project: Path, profile_id: str = "") -> None:
        if profile_id:
            raise ValueError(
                "This API profile uses Codex or opencode; select its configured driver."
            )
        self.executable = await require_provider("claude", project)
        self.project = project.resolve()
        if self.gateway_socket is None:
            raise ValueError("Connect the project gateway before starting Claude.")
        # Every message runs inside the read-only project boundary: say now if it's missing.
        readonly_command(["true"], self.project)
        probe = await asyncio.create_subprocess_exec(
            *launch_argv(self.executable, ["--help"]),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(probe.communicate(), 5)
            self.effort_supported = b"--effort" in output
            self.stable_system_prompt = DYNAMIC_SECTIONS_FLAG.encode() in output
            self.tools_flag = re.search(rb"(?m)^\s*--tools\b", output) is not None
            self.setting_sources = SETTING_SOURCES_FLAG.encode() in output
        finally:
            await stop_process(probe)
        require_flags(output)

    @property
    def handle(self) -> str:
        """The session id to resume this conversation with, once a turn has run."""
        return self.session_id if self.started else ""

    async def resume(self, handle: str) -> bool:
        """Continue a saved conversation: later turns pass `--resume <id>`, which works
        across processes. Only when Claude Code still has that session for this project."""
        try:
            identifier = str(uuid.UUID(handle))
        except ValueError:
            return False
        if not claude_transcript_exists(self.project, identifier):
            return False
        self.session_id = identifier
        self.started = True
        self.usage = ClaudeUsage()
        return True

    def _stable_record(self) -> Path:
        return self.project / SESSIONS_DIR / f"{self.session_id}.stable"

    def _holds(self, stable: str) -> bool:
        """This session (perhaps resumed by another run) was last sent ``stable``."""
        try:
            saved = self._stable_record().read_text(encoding="utf-8").strip()
        except OSError:
            return False
        return saved == hashlib.sha256(stable.encode()).hexdigest()

    def _remember(self, stable: str) -> None:
        record = self._stable_record()
        try:
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(hashlib.sha256(stable.encode()).hexdigest())
        except OSError:
            pass  # only a saving: the next turn sends the map again

    async def new_session(self) -> None:
        self.session_id = str(uuid.uuid4())
        self.started = False
        self.usage = ClaudeUsage()

    async def models(self) -> list[ModelChoice]:
        # Print mode has no account model inventory; every Claude Code takes the
        # aliases. Effort only when this CLI advertises it.
        efforts = ("low", "medium", "high") if self.effort_supported else ()
        return claude_model_choices(claude_configured_model(), efforts)

    async def send(self, prompt: Prompt) -> AsyncIterator[AgentEvent]:
        if not self.executable or self.gateway_socket is None:
            raise ValueError("Claude is not connected.")
        hook = _command_str(str(ROOT), "claude")
        settings: dict[str, Any] = {
            **(user_settings_kept() if self.setting_sources else {}),
            **NO_VENDOR_REMOTE,
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": SESSION_MATCHER,
                        "hooks": [{"type": "command", "command": hook, "timeout": 30}],
                    }
                ]
            },
        }
        args = [
            self.executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-prompts",
            "none",
            "--allowedTools",
            "mcp__joinery-registry__*",
            "mcp__joinery_registry__*",
            "--append-system-prompt",
            for_agent(INSTRUCTIONS, "claude"),
            "--settings",
            json.dumps(settings),
            "--mcp-config",
            json.dumps(
                {
                    "mcpServers": {
                        "joinery-registry": proxy_config(
                            self.gateway_socket,
                            offered_role(self.role, self.inspection_only),
                        )
                    }
                }
            ),
            STRICT_MCP_FLAG,
            "--resume" if self.started else "--session-id",
            self.session_id,
        ]
        args += cache_flags(self.role, self.stable_system_prompt, self.tools_flag)
        if self.setting_sources:
            # The project's settings (its joinery hook) and Joinery's own; the user's
            # only for signing in and the model (user_settings_kept).
            args += [SETTING_SOURCES_FLAG, "project,local"]
        env = dict(
            os.environ,
            DISABLE_TELEMETRY="1",
            DISABLE_ERROR_REPORTING="1",
            **SESSION_ENV,
        )
        if prompt.model:
            args += ["--model", prompt.model]
        if prompt.effort:
            args += ["--effort", prompt.effort]
        if prompt.attachments:
            # Images go in as content blocks, which only the stream-json input takes.
            args += ["--input-format", "stream-json"]
        if self.gateway_socket is not None:
            # The policy hook reports a Rule Zero stop to the chat host here.
            env["JOINERY_POLICY_ENDPOINT"] = str(self.gateway_socket)
        if self.inspection_only:
            env["JOINERY_INSPECTION_ONLY"] = "1"
            args += ["--disallowedTools", "Task", "Agent"]
        if self.role:
            env[ROLE_ENV] = self.role
        env[STAGE_ENV] = self.stage or NO_STAGE
        # The turn's text first: Claude Code gives up on stdin after 3 s, and a cold
        # project map can take longer than that to build.
        # A resumed session holds the map and rules from its last turn already.
        stable = await asyncio.to_thread(stable_text, prompt, "claude")
        known = stable if self.started and self._holds(stable) else ""
        data = await asyncio.to_thread(user_input, prompt, known)
        self.process = await asyncio.create_subprocess_exec(
            *readonly_command(launch_argv(args[0], args[1:]), self.project),
            cwd=self.project,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
            limit=16_000_000,
        )
        assert self.process.stdin and self.process.stdout and self.process.stderr
        self.process.stdin.write(data)
        self._remember(stable)
        await self.process.stdin.drain()
        self.process.stdin.close()
        stderr = asyncio.create_task(self.process.stderr.read())
        streamed = False
        ok = False
        #: Each call's name and the gate's guess (its refusal, "" to run), by id,
        #: until its result shows what really happened: one activity per call,
        #: with the outcome it really had.
        calls: dict[str, tuple[str, str]] = {}
        #: The same calls on the work trail, with what they were doing.
        trail_calls = trail.Calls(self.project)
        try:
            while line := await self.process.stdout.readline():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("session_id"):
                    self.session_id = str(event["session_id"])
                    self.started = True
                if isinstance(event, dict) and (usage := self.usage.read(event)):
                    yield usage
                kind = event.get("type")
                if kind == "stream_event":
                    delta = event.get("event", {}).get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield MessageDelta(
                            text=str(delta.get("text", "")), new_message=not streamed
                        )
                        streamed = True
                elif kind == "assistant":
                    for part in event.get("message", {}).get("content", []):
                        if part.get("type") == "text" and not streamed:
                            yield Message(text=str(part["text"]))
                        elif part.get("type") == "thinking":
                            if thought := trail.thinking(str(part.get("thinking", ""))):
                                yield thought
                        elif part.get("type") == "tool_use":
                            name = str(part.get("name", ""))
                            call_id = str(part.get("id", ""))
                            yield trail_calls.start(call_id, name, part.get("input"))
                            reason = permitted(
                                name, part.get("input"), self.inspection_only
                            )
                            if call_id:
                                calls[call_id] = (name, reason)
                                continue
                            yield ToolActivity(
                                server="claude",
                                tool=name,
                                allowed=not reason,
                                reason=reason,
                            )
                    streamed = False
                elif kind == "user":
                    # The call's result says what happened: refused after all
                    # (the policy hook, a rule of Claude Code's own), or run after
                    # all (a command Claude Code itself knows only reads). A call
                    # the gate refused that comes back as an error was refused,
                    # whatever words Claude Code used.
                    content = event.get("message", {}).get("content", [])
                    for part in content if isinstance(content, list) else []:
                        if not isinstance(part, dict):
                            continue
                        call_id = str(part.get("tool_use_id", ""))
                        pending = calls.pop(call_id) if call_id in calls else None
                        name, guess = pending or ("", "")
                        reason = refusal(part)
                        if guess and part.get("is_error") and not reason:
                            reason = guess
                        if reason and trail.needs_approval(reason):
                            # Claude Code's own "needs approval" refusal, with no
                            # one to ask: a quiet skipped step, never Joinery's
                            # refusal (the agent reads the reason in the result).
                            if skipped := trail_calls.skip(call_id, name):
                                yield skipped
                            continue
                        ended = trail_calls.end(
                            call_id, not part.get("is_error"), part.get("content")
                        )
                        if ended is not None:
                            yield ended
                        if pending is None:
                            continue
                        yield ToolActivity(
                            server="claude",
                            tool=name,
                            allowed=not reason,
                            reason=reason,
                            call_id=call_id,
                        )
                elif kind == "result":
                    ok = (
                        not event.get("is_error", False)
                        and event.get("subtype") == "success"
                    )
                    if not ok:
                        yield Message(
                            text="Claude could not finish this request: "
                            + str(
                                event.get(
                                    "result", event.get("subtype", "unknown error")
                                )
                            ),
                            level="warn",
                        )
            # Calls the stream ended without a result for: the gate's guess.
            for call_id, (name, guess) in calls.items():
                yield ToolActivity(
                    server="claude",
                    tool=name,
                    allowed=not guess,
                    reason=guess,
                    call_id=call_id,
                )
            calls.clear()
            for ended in trail_calls.end_all():
                yield ended
            await self.process.wait()
            if self.process.returncode and not ok:
                details = tail((await stderr).decode("utf-8", "replace"))
                yield Message(
                    text=claude_exit_text(details), level="warn", details=details
                )
            yield Done(ok=ok and self.process.returncode == 0)
        finally:
            await stop_process(self.process)
            await stderr

    async def cancel(self) -> None:
        await stop_process(self.process)

    async def close(self) -> None:
        await self.cancel()
