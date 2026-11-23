"""Project-owned MCP connections with honest readiness and per-call policy."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import ntpath
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any, TextIO

import anyio
from jsonschema import Draft202012Validator
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import BaseModel, ConfigDict, Field

from joinery import docker_cli
from joinery.hook import mcp_violation
from joinery.policy import Severity, command_violation, load_denylist, normalise
from joinery.read_access import read_only
from joinery.roles import REGISTRY_TOOLS
from joinery.rw_lock import ReadWriteLock


class ServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    command: str = Field(min_length=1)
    args: list[str] = []
    env: dict[str, str] = {}
    cwd: str = ""
    source: str = "custom"
    revision: str = ""
    container_name: str = Field(default="", pattern=r"^$|^joinery-mcp-[a-f0-9]{32}$")
    worker_image: str = ""
    worker_scope: str = Field(default="", pattern=r"^$|^[a-f0-9]{32}$")


#: Where an offline worker sees the folder it was given.
WORKDIR = "/work"


def input_problem(error: Any, schema: dict[str, Any]) -> str:
    """What is wrong with a tool's input, in words the agent can act on: a missing
    argument by name, with the ones the tool takes."""
    if error.validator == "required" and isinstance(error.instance, dict):
        wanted = [n for n in error.validator_value if n not in error.instance]
        known = sorted((schema.get("properties") or {}).keys())
        unknown = sorted(n for n in error.instance if known and n not in known)
        return (
            f"missing {', '.join(wanted)}"
            + (f" (it takes {', '.join(known)})" if known else "")
            + (f"; {', '.join(unknown)} is not one of them" if unknown else "")
            + "."
        )
    return f"{error.validator} constraint failed."


def map_paths(value: Any, project: PurePath, stage: PurePath) -> Any:
    """Move every absolute path under `project` to the same place under `stage`."""
    if isinstance(value, dict):
        return {k: map_paths(v, project, stage) for k, v in value.items()}
    if isinstance(value, list):
        return [map_paths(v, project, stage) for v in value]
    if isinstance(value, str):
        candidate: PurePath
        original: PurePath
        if isinstance(project, PureWindowsPath):
            # PureWindowsPath also permits exercising Windows drive/UNC/case
            # behavior on other hosts without changing the host's os.name.
            candidate = PureWindowsPath(ntpath.normpath(value))
            original = PureWindowsPath(ntpath.normpath(str(project)))
        else:
            candidate = Path(value)
            original = Path(project)
        if not candidate.is_absolute():
            return value
        if isinstance(project, Path):
            candidate = Path(value).resolve()
            original = project.resolve()
        try:
            relative = candidate.relative_to(original)
        except ValueError:
            return value
        return str(stage.joinpath(*relative.parts))
    return value


#: Where every staged change lives inside the project (transactions.TRANSACTIONS).
_CHANGES = re.compile(r"(?:^|[/\\])\.joinery[/\\]+transactions(?:[/\\]|$)")
CHANGE_FOLDERS = (
    "Name project files by their project path (res://… or the project folder): the "
    "project tools already read this change as staged, and no agent reads another "
    "change's folder."
)


def names_changes(value: Any) -> bool:
    """Some string in `value` points into the project's staged-change folders."""
    if isinstance(value, dict):
        return any(names_changes(v) for v in value.values())
    if isinstance(value, list | tuple):
        return any(names_changes(v) for v in value)
    return isinstance(value, str) and bool(_CHANGES.search(value))


#: The pinned Godot MCP's arguments that it joins onto ``projectPath`` on the host
#: side (path.join), where a ``res://`` prefix names a folder called "res:".
GODOT_FILE_ARGUMENTS = frozenset(
    {
        "filePath",
        "scenePath",
        "scriptPath",
        "shaderPath",
        "texturePath",
        "directoryPath",
        "newPath",
        "subdirectory",
    }
)


def godot_relative(arguments: dict[str, Any]) -> dict[str, Any]:
    """``res://a/b.gd`` -> ``a/b.gd`` in the Godot MCP's file arguments.

    Its Godot side adds ``res://`` back where it loads a resource, so the same
    file is meant either way; without this the server answers "File does not
    exist" for a file that is there.
    """
    return {
        key: value.removeprefix("res://")
        if key in GODOT_FILE_ARGUMENTS
        and isinstance(value, str)
        and value.startswith("res://")
        else value
        for key, value in arguments.items()
    }


#: Names agents give a file argument a tool calls something else.
PATH_ALIASES = ("path", "file", "file_path", "filePath", "scriptPath", "scenePath")
#: Required arguments an alias may stand for: the Godot MCP's file arguments and a
#: document operator's ``source``.
_ALIASED = GODOT_FILE_ARGUMENTS | {"source"}


def needs_filling(server: str, arguments: dict[str, Any]) -> bool:
    """The call may lack an argument :func:`filled` can supply."""
    return (server == "godot" and not arguments.get("projectPath")) or any(
        key in arguments for key in PATH_ALIASES
    )


def filled(
    server: str, schema: dict[str, Any], arguments: dict[str, Any], project: Path
) -> dict[str, Any]:
    """``arguments`` with what an agent commonly leaves out filled in from ``schema``:
    the Godot MCP's ``projectPath`` (this project), and one missing file argument
    given under another name (``path`` for ``filePath``, ``filePath`` for
    ``scriptPath``, ``path`` for ``source``)."""
    properties = schema.get("properties") or {}
    out = dict(arguments)
    if server == "godot" and "projectPath" in properties and not out.get("projectPath"):
        out["projectPath"] = str(project)
    missing = [
        name
        for name in schema.get("required") or []
        if name not in out and name in _ALIASED
    ]
    given = [key for key in PATH_ALIASES if key in out and key not in properties]
    if len(missing) == 1 and len(given) == 1:
        out[missing[0]] = out.pop(given[0])
    return out


def worker_root(spec: ServerSpec) -> Path | None:
    """The host folder a worker server sees as /work, or None for a host server."""
    if spec.command != "docker":
        return None
    for flag, value in zip(spec.args, spec.args[1:], strict=False):
        if flag not in {"-v", "--volume"}:
            continue
        for suffix in (":" + WORKDIR, ":" + WORKDIR + ":ro"):
            if value.endswith(suffix):
                return Path(value[: -len(suffix)])
    return None


def rewrite_root(
    result: CallToolResult, root: str, new_root: PurePath
) -> CallToolResult:
    """Put every path under `root` in a result under `new_root` instead.

    A worker answers with its own paths (/work/...), which mean nothing on the
    host; the agent should read the project paths it asked about.
    """
    pattern = re.compile(
        r"(?<![\w./\\-])"
        + re.escape(root.rstrip("/\\"))
        + r"((?:[/\\][^\s\"'`<>|,;:()\[\]{}]*)?)(?![\w-]|\.\w)"
    )

    def swap(match: re.Match[str]) -> str:
        tail = [part for part in re.split(r"[/\\]", match.group(1)) if part]
        return str(new_root.joinpath(*tail))

    def text(value: Any) -> Any:
        if isinstance(value, str):
            return pattern.sub(swap, value)
        if isinstance(value, dict):
            return {k: text(v) for k, v in value.items()}
        if isinstance(value, list):
            return [text(v) for v in value]
        return value

    content = [
        block.model_copy(update={"text": text(block.text)})
        if isinstance(block, TextContent)
        else block
        for block in result.content
    ]
    return result.model_copy(
        update={
            "content": content,
            "structured_content": text(result.structured_content),
        }
    )


class Readiness(BaseModel):
    name: str
    state: str = "selected"
    detail: str = ""
    tools: list[str] = []
    protocol: str = ""
    inspected: bool = False
    installed: bool = False
    connected: bool = False
    revision: str = ""


class PolicyStop(RuntimeError):
    """A Rule Zero stop must propagate to the agent turn, not become a retry."""


def host_environment() -> dict[str, str]:
    """Inherit runtime paths and selected graphics config, never provider keys."""
    names = (
        "PATH",
        "HOME",
        "LANG",
        "TMPDIR",
        "SYSTEMROOT",
        "SystemRoot",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "VK_DRIVER_FILES",
        "VK_ICD_FILENAMES",
        "LIBGL_ALWAYS_SOFTWARE",
        "GALLIUM_DRIVER",
        "MESA_LOADER_DRIVER_OVERRIDE",
    )
    return {key: os.environ[key] for key in names if key in os.environ}


def validate_schema(schema: Any) -> None:
    """MCP schemas are data, never permission to retrieve remote references."""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if (
                key in {"$ref", "$dynamicRef"}
                and isinstance(value, str)
                and not value.startswith("#")
            ):
                raise ValueError("MCP schemas may reference only local definitions.")
            validate_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            validate_schema(value)


def validate_arguments(arguments: Any) -> None:
    """Apply the same denylist to embedded commands and Python fallbacks."""
    if isinstance(arguments, dict):
        for value in arguments.values():
            validate_arguments(value)
    elif isinstance(arguments, list):
        for value in arguments:
            validate_arguments(value)
    elif isinstance(arguments, str):
        denylist = load_denylist()
        verdict = command_violation(arguments, denylist)
        if verdict is not None:
            raise PolicyStop(verdict.reason)
        with contextlib.suppress(SyntaxError):
            tree = ast.parse(arguments)
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    if normalise(module.split(".")[0]) in denylist.packages:
                        raise PolicyStop(f"{module} is a denied generative package")


def result_text(result: CallToolResult) -> str:
    """The text blocks of a tool result, joined."""
    return "\n".join(
        str(getattr(block, "text", ""))
        for block in result.content
        if hasattr(block, "text")
    ).strip()


def probe_problem(name: str, result: CallToolResult) -> str:
    """Why a read-only probe did not prove the app works, or "" when it did.

    An error result fails. So does a capabilities answer that is false: the
    offline worker did not start (`runtime_available: false`), the answer says
    `ok: false`, or every listed tool reports its application missing.
    """
    text = result_text(result)
    if result.is_error:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        return f"{name} failed: {first[:300] or 'no detail'}"
    try:
        data = json.loads(text)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    if data.get("runtime_available") is False:
        return f"{name}: the offline worker did not start"
    if data.get("ok") is False:
        return f"{name}: {str(data.get('error') or data.get('detail') or 'not ready')[:300]}"
    listed = [t for t in data.get("tools", []) if isinstance(t, dict)]
    if listed and not any(t.get("application_installed", True) for t in listed):
        return f"{name}: the application is missing from the worker"
    return ""


#: Servers that answer several requests at once over one stdio connection. Their
#: audited reads (joinery.read_access) are sent side by side; every other call to
#: them, and every call to any other server, still goes alone. The pinned Godot
#: MCP (tugcantopaloglu/godot-mcp) handles each request in its own async handler,
#: and its reads either read a file or run their own headless Godot
#: (validate_script), so reads don't share state. Others are not known to: the
#: first-party tool servers and the upstream Blender MCP drive one app session.
CONCURRENT_READS = frozenset({"godot"})


@dataclass(eq=False)
class _Request:
    tool: str
    arguments: dict[str, Any]
    future: asyncio.Future[CallToolResult]
    timeout: float


#: Seconds between asks while Docker doesn't answer (busy, starting, a hiccup):
#: about 15 s in all before the image counts as unavailable.
IMAGE_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0)
#: What Docker says when it answered and the image really isn't there.
_NO_IMAGE = ("No such image", "No such object")


async def _image_present(image: str) -> int:
    """`docker image inspect`'s exit code for [image] (1 when it takes over 10 s)."""
    return (await _image_state(image))[0]


async def _image_state(image: str) -> tuple[int, bool]:
    """`docker image inspect`'s exit code for [image], and whether Docker answered
    that the image is missing (False: no answer, a timeout, a daemon error)."""
    check = await asyncio.create_subprocess_exec(
        "docker",
        "image",
        "inspect",
        image,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _out, err = await asyncio.wait_for(check.communicate(), 10)
        code = check.returncode if check.returncode is not None else 1
    except TimeoutError:
        check.kill()
        await check.wait()
        return 1, False
    text = (err or b"").decode(errors="replace")
    return int(code), bool(code) and any(sign in text for sign in _NO_IMAGE)


async def _listed_id(image: str) -> str:
    """The id `docker image ls` lists for [image], "" when none (or no answer)."""
    try:
        listing = await asyncio.create_subprocess_exec(
            "docker",
            "image",
            "ls",
            "--quiet",
            image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return ""
    try:
        out, _err = await asyncio.wait_for(listing.communicate(), 10)
    except TimeoutError:
        listing.kill()
        await listing.wait()
        return ""
    if listing.returncode or not isinstance(out, bytes):
        return ""
    ids = out.decode(errors="replace").split()
    return ids[0] if ids else ""


async def image_ready(image: str) -> bool:
    """Whether [image] is there, asking again with backoff while Docker is busy.

    Docker saying the image is missing twice in a row is final only once
    `docker image ls` agrees: Docker Desktop sometimes answers "No such image"
    for a moment while it lists the image, and then the listed id is asked for
    instead. Anything else (no answer, a timeout, a daemon error) is asked again
    after 1, 2, 4 and 8 s."""
    missing = 0
    for delay in (*IMAGE_RETRY_DELAYS, None):
        code, absent = await _image_state(image)
        if code == 0:
            return True
        missing = missing + 1 if absent else 0
        if missing >= 2 or delay is None:
            listed = await _listed_id(image)
            if listed and (await _image_state(listed))[0] == 0:
                return True
            if not listed or delay is None:
                return False
            missing = 0
        await asyncio.sleep(delay)
    return False


@dataclass
class MCPConnection:
    spec: ServerSpec
    project: Path
    #: Where the server's own stderr goes (its banners and logs). None: this
    #: process's stderr; a readiness probe passes a log file so `joinery doctor`
    #: prints only its own report.
    errlog: TextIO | None = None
    readiness: Readiness = field(init=False)
    tools: dict[str, Tool] = field(default_factory=dict, init=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False)
    _ready: asyncio.Future[None] | None = field(default=None, init=False)
    _queue: asyncio.Queue[_Request | None] = field(
        default_factory=asyncio.Queue, init=False
    )
    #: Reads of a CONCURRENT_READS server share it; any other call holds it alone.
    _requests: ReadWriteLock = field(default_factory=ReadWriteLock, init=False)
    #: One connect at a time: two calls on a new connection start one server.
    _connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self) -> None:
        self.readiness = Readiness(
            name=self.spec.name, installed=True, revision=self.spec.revision
        )

    async def connect(self, timeout: float = 30) -> None:
        async with self._connect_lock:
            await self._connect(timeout)

    async def _connect(self, timeout: float) -> None:
        if self._task is not None and not self._task.done():
            if self._ready is not None:
                await asyncio.wait_for(asyncio.shield(self._ready), timeout)
            return
        verdict = mcp_violation(self.spec.name, "", self.project)
        if verdict:
            if verdict.severity is Severity.STOP:
                raise PolicyStop(verdict.reason)
            raise PermissionError(verdict.reason)
        # Asked again with backoff: a busy Docker answering late is not a
        # missing tool, and one late answer must not end the turn.
        if self.spec.worker_image and not await image_ready(self.spec.worker_image):
            self.readiness.state = "runtime-missing"
            if not await docker_cli.responding_async():
                # Not a missing tool: Docker itself stopped answering.
                self.readiness.detail = docker_cli.NOT_RESPONDING
                raise ValueError(self.readiness.detail)
            self.readiness.detail = (
                "This app's offline tools aren't available right now: Docker "
                "may not be running, or the tools need a refresh after an "
                "update. Joinery asked Docker for about 15 seconds. Carry on "
                "with other parts of the request and call this tool again "
                "later; if it still fails, ask the user to start Docker or "
                "press Refresh tools in the Joinery sidebar, then try again."
            )
            raise ValueError(self.readiness.detail)
        self.tools.clear()
        self._ready = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._serve())
        try:
            await asyncio.wait_for(asyncio.shield(self._ready), timeout)
        except BaseException:
            await self.close()
            raise

    async def _serve(self) -> None:
        # The SDK's process/cancel scope is opened and closed in this same task.
        # Neither model API keys nor the host's complete environment are inherited.
        env = host_environment()
        env.update(self.spec.env)
        parameters = StdioServerParameters(
            command=self.spec.command,
            args=self.spec.args,
            env=env,
            cwd=self.spec.cwd or self.project,
        )
        self.readiness.state = "starting"
        active: set[_Request] = set()
        try:
            mode = (
                "legacy"
                if self.spec.name
                in {"godot", "blender", "inkscape", "audacity", "claude-collider"}
                else "auto"
            )
            transport = stdio_client(parameters, errlog=self.errlog or sys.stderr)
            async with Client(transport, read_timeout_seconds=30, mode=mode) as client:
                self.readiness.state = "connected"
                self.readiness.connected = True
                self.readiness.protocol = client.protocol_version
                cursor: str | None = None
                while True:
                    page = await client.list_tools(cursor=cursor)
                    for tool in page.tools:
                        if tool.name in self.tools:
                            raise ValueError("Server returned duplicate tool names.")
                        Draft202012Validator.check_schema(tool.input_schema)
                        validate_schema(tool.input_schema)
                        self.tools[tool.name] = tool
                    cursor = page.next_cursor
                    if cursor is None:
                        break
                self.readiness.tools = sorted(self.tools)
                self.readiness.state = "discovered"
                assert self._ready is not None
                self._ready.set_result(None)

                async def answer(request: _Request) -> None:
                    try:
                        result = await client.call_tool(
                            request.tool,
                            request.arguments,
                            read_timeout_seconds=request.timeout,
                        )
                        if not request.future.done():
                            request.future.set_result(result)
                    except (Exception, BaseExceptionGroup) as exc:
                        if not request.future.done():
                            request.future.set_exception(
                                RuntimeError(
                                    f"{self.spec.name} tool call failed: {type(exc).__name__}"
                                )
                            )
                        raise
                    finally:
                        # Cancelled with the others when one request fails: its
                        # caller hears now, not at its own time-out.
                        if not request.future.done():
                            request.future.set_exception(
                                ConnectionError("MCP connection closed.")
                            )
                        active.discard(request)

                # Each request is answered in its own task, so reads that `call`
                # lets through together are in flight together; `call` keeps
                # every other request alone (CONCURRENT_READS).
                async with anyio.create_task_group() as requests:
                    while (request := await self._queue.get()) is not None:
                        active.add(request)
                        requests.start_soon(answer, request)
        except asyncio.CancelledError:
            raise
        except (Exception, BaseExceptionGroup) as exc:  # noqa: BLE001 - report isolated server failures
            self.readiness.state = "error"
            self.readiness.detail = f"{self.spec.name} could not connect or complete a request ({type(exc).__name__})."
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(RuntimeError(self.readiness.detail))
        finally:
            self.readiness.connected = False
            for request in active:
                if not request.future.done():
                    request.future.set_exception(
                        ConnectionError("MCP connection closed.")
                    )
            while not self._queue.empty():
                request = self._queue.get_nowait()
                if request is not None and not request.future.done():
                    request.future.set_exception(
                        ConnectionError("MCP connection closed.")
                    )
            if self.spec.container_name:
                cleanup = await asyncio.create_subprocess_exec(
                    "docker",
                    "rm",
                    "--force",
                    self.spec.container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    await asyncio.wait_for(cleanup.wait(), timeout=10)
                except TimeoutError:
                    cleanup.kill()
                    await cleanup.wait()
            if self.spec.worker_scope:
                listing = await asyncio.create_subprocess_exec(
                    "docker",
                    "ps",
                    "-aq",
                    "--filter",
                    "label=joinery.scope=" + self.spec.worker_scope,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    data, _ = await asyncio.wait_for(listing.communicate(), 10)
                    containers = data.decode().split()
                    if containers:
                        cleanup = await asyncio.create_subprocess_exec(
                            "docker",
                            "rm",
                            "--force",
                            *containers,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await asyncio.wait_for(cleanup.wait(), 10)
                except TimeoutError:
                    if listing.returncode is None:
                        listing.kill()
                        await listing.wait()
            if self.readiness.state != "error":
                self.readiness.state = "disconnected"

    async def call(
        self, tool: str, arguments: dict[str, Any], timeout: float = 60
    ) -> CallToolResult:
        verdict = mcp_violation(self.spec.name, tool, self.project, arguments)
        if verdict is not None:
            if verdict.severity is Severity.STOP:
                raise PolicyStop(verdict.reason)
            raise PermissionError(verdict.reason)
        # Starts the server when it isn't running, and waits while it is still
        # listing its tools (a call beside the one that started it, too).
        await self.connect()
        definition = self.tools.get(tool)
        if definition is None:
            if tool in REGISTRY_TOOLS:
                raise ValueError(
                    f"{self.spec.name} does not expose {tool}: {tool} is a "
                    "joinery-registry tool of its own. Call it directly, not "
                    "through call_tool or read_tool."
                )
            raise ValueError(f"{self.spec.name} does not expose {tool}.")
        error = next(
            Draft202012Validator(definition.input_schema).iter_errors(arguments), None
        )
        if error is not None:
            location = ".".join(str(part) for part in error.path) or "arguments"
            raise ValueError(
                f"Invalid {self.spec.name}.{tool} input at {location}: "
                + input_problem(error, definition.input_schema)
            )
        root = worker_root(self.spec)
        if root is not None:
            # The worker sees its folder as /work: host paths go in as worker
            # paths, and worker paths come back as host paths.
            arguments = map_paths(arguments, root, PurePosixPath(WORKDIR))
        shared = self.spec.name in CONCURRENT_READS and read_only(self.spec.name, tool)
        async with self._requests.read if shared else self._requests.write:
            future: asyncio.Future[CallToolResult] = (
                asyncio.get_running_loop().create_future()
            )
            await self._queue.put(_Request(tool, arguments, future, timeout))
            try:
                result = await asyncio.wait_for(asyncio.shield(future), timeout + 2)
            except (TimeoutError, asyncio.CancelledError):
                future.cancel()
                await self.close()
                raise
        return result if root is None else rewrite_root(result, WORKDIR, root)

    async def probe(self, tool: str, arguments: dict[str, Any]) -> CallToolResult:
        """Only the caller's explicit read-only app probe can establish inspected.

        A probe fails, and the connection is not inspected, when the call
        returns an error or its capabilities say the app is missing; the reason
        goes into `readiness.detail`. The result is returned either way.
        """
        result = await self.call(tool, arguments)
        problem = probe_problem(f"{self.spec.name}.{tool}", result)
        if problem:
            self.readiness.inspected = False
            self.readiness.detail = problem
        else:
            self.readiness.inspected = True
        return result

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.tools.clear()
