"""The work trail: each tool call an agent makes, as one short line a person reads.

While Journeyman works, the sidebar and ``joinery run`` show what it is doing as it
happens ("Reading level.gd", "Searching for Spawn", "Modelling the mug in Blender:
bevel, 2 segments"), with the agent's short thinking between the steps and a cheap
preview where one exists: the file and its +/− line count for an edit, a picture when
a render or texture lands.

Every provider adapter (``bridge.claude_session``, ``bridge.codex_session``,
``bridge.acp_session``) turns its own tool events into :class:`bridge.protocol.Trail`
events through this module, so the words are the same whichever agent runs.
"""

from __future__ import annotations

import difflib
import re
from pathlib import PurePosixPath
from typing import Any

from bridge.protocol import Trail

#: The longest line the trail shows; the rest goes behind an ellipsis.
WIDTH = 96
#: The longest thinking snippet.
THOUGHT_WIDTH = 120
#: Apps by their tool-server id, as the user knows them.
APPS = {
    "blender": "Blender",
    "krita": "Krita",
    "material-maker": "Material Maker",
    "materialmaker": "Material Maker",
    "inkscape": "Inkscape",
    "surge-xt": "Surge XT",
    "supercollider": "SuperCollider",
    "godot": "Godot",
    "gimp": "GIMP",
}
#: What the work in each app is called.
APP_VERBS = {
    "blender": "Modelling",
    "krita": "Painting",
    "material-maker": "Making",
    "materialmaker": "Making",
    "inkscape": "Drawing",
    "surge-xt": "Making",
    "supercollider": "Making",
    "gimp": "Painting",
}
#: Operations whose verb says more than the app's.
OP_VERBS = {
    "make_cycle": "Animating",
    "edit_action": "Animating",
    "animation_pass": "Animating",
    "edit_bone": "Rigging",
    "set_body_shape": "Shaping",
    "bake_shape_keys": "Baking",
    "bake_height_to_normal": "Baking",
    "paint_map": "Painting",
    "add_painted_map": "Painting",
    "kit_make": "Building",
    "kit_batch": "Building",
    "kit_edit": "Building",
    "compose_track": "Composing",
    "edit_track": "Composing",
}
#: Joinery's own tools, by name: the line, with ``{arg}`` filled from the call.
JOINERY = {
    "edit_scene": "Editing the scene: {summary}",
    "inspect_editor": "Looking at the scene",
    "view_editor": "Looking at your view",
    "details": "Reading more of a result",
    "search_project": "Searching for {pattern}",
    "find_recipe": "Finding the recipe for {path}",
    "inspect_game": "Looking at the running game",
    "drive_game": "Playing the game",
    "inspect_in_parallel": "Looking at several things at once",
    "submit_for_review": "Handing the change over",
    "precheck": "Checking the change",
    "propose_todo": "Writing the to-do",
    "start_task": "Starting a task: {title}",
    "start_variants": "Trying a few ways",
    "task_status": "Checking on a task",
    "playtest": "Playtesting",
    "access_check": "Checking accessibility",
    "perf_check": "Checking performance",
    "blockout": "Blocking out the level",
    "grow_level": "Growing the level",
    "make_variant": "Making a variant",
    "sound_audit": "Listening through the sounds",
    "tidy_check": "Looking for unused files",
    "tidy_remove": "Removing unused files",
    "rerecord_replay": "Recording the replay again",
    "save_design_doc": "Saving the design doc",
    "report_review": "Writing the review",
    "report_design": "Writing the design findings",
    "report_diagnosis": "Writing what went wrong",
    "reproduce_error": "Reproducing the error",
    "discover_tools": "Looking up tools",
    "tool_schema": "Looking up {tool}",
    "art_direction": "Reading the art direction",
    "game_brief": "Reading the game brief",
    "game_setup": "Reading the game setup",
    "preview_asset": "Looking at {path}",
    "list_assets": "Listing the assets",
    "kit_list": "Listing the kits",
    "resolve_source": "Finding the source of {path}",
}
#: Tool names (any provider's) by what they do.
READS = frozenset(
    {"read", "read_file", "view", "read_many_files", "notebookread", "open_file"}
)
SEARCHES = frozenset(
    {"grep", "search", "search_file_content", "search_files", "codebase_search"}
)
LISTS = frozenset({"glob", "ls", "list_directory", "list_dir", "list_files", "find"})
EDITS = frozenset(
    {
        "edit",
        "multiedit",
        "replace",
        "str_replace",
        "edit_file",
        "notebookedit",
        "patch",
    }
)
WRITES = frozenset({"write", "write_file", "create_file"})
SHELLS = frozenset(
    {"bash", "shell", "run_shell_command", "exec_command", "local_shell", "execute"}
)
FETCHES = frozenset({"webfetch", "web_fetch", "fetch"})
WEB_SEARCHES = frozenset({"websearch", "web_search", "google_web_search"})
PLANS = frozenset({"todowrite", "update_plan", "write_todos", "todoread"})
HELPERS = frozenset({"task", "agent"})
#: Pictures the editor can show as a thumbnail.
PICTURES = (".png", ".jpg", ".jpeg", ".webp")
_PICTURE = re.compile(
    r"((?:res://|/)[^\s\"'`<>|]+?\.(?:png|jpe?g|webp))(?=[\s\"'`<>|,;)\]}]|$)",
    re.IGNORECASE,
)


class Calls:
    """One turn's tool calls on the trail: each reported running when it starts and
    done or failed when it ends, under its id, with the same line."""

    def __init__(self) -> None:
        self._open: dict[str, Trail] = {}
        self._count = 0

    def start(self, call_id: str, name: str, arguments: object = None) -> Trail:
        """A call the agent ``name``d with ``arguments`` has started."""
        file, added, removed = edit_size(name, arguments)
        return self.start_line(
            call_id, describe(name, arguments), file=file, added=added, removed=removed
        )

    def start_line(
        self,
        call_id: str,
        text: str,
        *,
        file: str = "",
        added: int = 0,
        removed: int = 0,
    ) -> Trail:
        """A call already described (an ACP agent's own title) has started."""
        self._count += 1
        identifier = call_id or f"call-{self._count}"
        event = Trail(
            id=identifier,
            text=short(text) or "Working",
            file=file_name(file),
            added=added,
            removed=removed,
        )
        self._open[identifier] = event
        return event

    def end(self, call_id: str, ok: bool = True, result: object = None) -> Trail | None:
        """The call ``call_id`` ended; None for a call never reported started."""
        started = self._open.pop(call_id, None)
        if started is None:
            return None
        return started.model_copy(
            update={"state": "done" if ok else "failed", "picture": picture(result)}
        )

    def end_all(self) -> list[Trail]:
        """Every call still open when the turn's stream ended, as done."""
        ended = [
            call.model_copy(update={"state": "done"}) for call in self._open.values()
        ]
        self._open.clear()
        return ended


def thinking(text: str) -> Trail | None:
    """A thought between the steps as a trail line; None when it says nothing."""
    line = thought(text)
    return Trail(text=line, note="thought", state="done") if line else None


def short(text: str, width: int = WIDTH) -> str:
    """``text`` on one line, cut to ``width`` with an ellipsis."""
    line = " ".join(str(text).split())
    return line if len(line) <= width else line[: width - 1].rstrip() + "…"


def tool_name(name: str) -> str:
    """A provider's tool name without its server prefix ("mcp__joinery__call_tool",
    "joinery.call_tool", "joinery__call_tool" -> "call_tool")."""
    bare = str(name).strip()
    if bare.startswith("mcp__"):
        bare = bare.split("__", 2)[-1]
    for separator in ("__", "."):
        if separator in bare:
            bare = bare.rsplit(separator, 1)[-1]
    return bare


def server_name(name: str) -> str:
    """The server of a prefixed tool name ("mcp__blender__apply_scale" -> "blender")."""
    bare = str(name).strip()
    if bare.startswith("mcp__"):
        parts = bare.split("__")
        return parts[1] if len(parts) > 2 else ""
    return ""


def words(name: str) -> str:
    """A tool or operation name in words ("set_material_value" -> "set material value")."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name))
    return " ".join(spaced.replace("_", " ").replace("-", " ").split()).lower()


def file_name(path: object) -> str:
    """A path's last part ("res://game/level.gd" -> "level.gd")."""
    text = str(path or "").strip().rstrip("/")
    return PurePosixPath(text.removeprefix("res://")).name if text else ""


def describe(name: str, arguments: object = None) -> str:
    """The line for one tool call: ``name`` as the agent called it, with its
    ``arguments`` (a dict, or anything else when the provider gives none)."""
    args = arguments if isinstance(arguments, dict) else {}
    bare = tool_name(name)
    key = bare.lower()
    if key == "call_tool" and isinstance(args.get("tool"), str):
        inner = args.get("arguments")
        return app_line(
            str(args.get("server", "")),
            str(args["tool"]),
            inner if isinstance(inner, dict) else {},
        )
    server = server_name(name)
    if server and server != "joinery" and server in APPS:
        return app_line(server, bare, args)
    if bare in JOINERY:
        return short(_fill(JOINERY[bare], args))
    path = _path(args)
    if key in READS:
        return short(f"Reading {file_name(path)}" if path else "Reading a file")
    if key in SEARCHES:
        pattern = args.get("pattern") or args.get("query") or args.get("regex")
        return short(f"Searching for {pattern}" if pattern else "Searching the project")
    if key in LISTS:
        where = args.get("pattern") or args.get("path") or args.get("dir_path")
        return short(f"Looking through {where}" if where else "Looking through files")
    if key in EDITS or key == "apply_patch":
        named = path or _patch_file(args)
        return short(f"Editing {file_name(named)}" if named else "Editing a file")
    if key in WRITES:
        return short(f"Writing {file_name(path)}" if path else "Writing a file")
    if key in SHELLS:
        return command_line(args.get("command") or args.get("cmd") or "")
    if key in FETCHES:
        url = str(args.get("url") or args.get("prompt") or "")
        host = re.sub(r"^\w+://", "", url).split("/", 1)[0]
        return short(f"Reading {host}" if host else "Reading a web page")
    if key in WEB_SEARCHES:
        query = args.get("query") or ""
        return short(f"Searching the web for {query}" if query else "Searching the web")
    if key in PLANS:
        return "Updating the plan"
    if key in HELPERS:
        what = args.get("description") or args.get("prompt") or ""
        return short(f"Asking a helper: {what}" if what else "Asking a helper")
    return short(words(bare).capitalize() or "Working")


def app_line(server: str, tool: str, arguments: dict[str, Any]) -> str:
    """A call to one of the project's tools: "Modelling the mug in Blender: bevel,
    2 segments", "Reading level.gd" (Godot's own file tools read like the agent's)."""
    if server == "godot":
        path = _path(arguments)
        if tool in {"read_file", "get_file", "view_file"} and path:
            return short(f"Reading {file_name(path)}")
        if tool in {"write_file", "edit_file", "create_file"} and path:
            return short(f"Editing {file_name(path)}")
        return short(words(tool).capitalize() + " in Godot")
    if server == "joinery" and tool in JOINERY:
        return short(_fill(JOINERY[tool], arguments))
    apps = _apps(server, tool, arguments)
    app = APPS.get(apps[0], apps[0].replace("-", " ").title()) if apps else ""
    verb = OP_VERBS.get(tool) or (APP_VERBS.get(apps[0], "Working") if apps else "")
    subject = _subject(arguments)
    detail = _detail(tool, arguments)
    if not app:
        head = words(tool).capitalize()
        return short(f"{head}: {subject}" if subject else head)
    line = f"{verb} {subject} in {app}" if subject else f"{verb} in {app}"
    return short(f"{line}: {detail}" if detail else line)


def command_line(command: object) -> str:
    """A shell command as a line: what it runs, in words where Joinery knows it."""
    if isinstance(command, list):
        parts = [str(part) for part in command]
        wraps = len(parts) == 3 and parts[1] in {"-c", "-lc"}
        command = parts[2] if wraps else " ".join(parts)
    text = str(command)
    text = " ".join(text.split())
    # A shell wrapper ("bash -lc '…'") runs what it quotes.
    wrapped = re.match(r"^(?:/bin/)?(?:ba|z)?sh\s+-l?c\s+(['\"])(.*)\1$", text)
    if wrapped:
        text = wrapped.group(2)
    lowered = text.lower()
    if lowered.startswith(("rg ", "grep ")):
        found = re.search(r"(['\"])(.+?)\1", text)
        return short(
            f"Searching for {found.group(2)}" if found else "Searching the project"
        )
    if lowered.startswith(("cat ", "head ", "tail ", "sed -n")):
        return short(f"Reading {file_name(text.split()[-1])}")
    if lowered.startswith(("ls", "find ")):
        return "Looking through files"
    if re.search(r"\b(pytest|gut|unittest|test)\b", lowered):
        return "Running the tests"
    if lowered.startswith("git "):
        return short(
            "Looking at the history" if " log" in lowered else f"Running {text}"
        )
    return short(f"Running {text}" if text else "Running a command")


def thought(text: str) -> str:
    """A short thinking or plan snippet: its first sentence, markdown taken out."""
    plain = re.sub(r"[*_`#>]+", "", str(text))
    plain = " ".join(plain.split())
    if not plain:
        return ""
    first = re.split(r"(?<=[.!?])\s", plain, maxsplit=1)[0]
    return short(first, THOUGHT_WIDTH)


def edit_size(name: str, arguments: object = None) -> tuple[str, int, int]:
    """The file an edit call changes and its lines added and removed; ("", 0, 0) for
    a call that edits nothing it says."""
    args = arguments if isinstance(arguments, dict) else {}
    key = tool_name(name).lower()
    if key == "call_tool" and isinstance(args.get("arguments"), dict):
        inner: dict[str, Any] = args["arguments"]
        if str(args.get("tool", "")) in {"write_file", "edit_file"}:
            return _edited(_path(inner), inner)
        return "", 0, 0
    if key == "apply_patch":
        patch = str(args.get("input") or args.get("patch") or "")
        return _patch_file(args), *diff_size(patch)
    if key in EDITS or key in WRITES:
        return _edited(_path(args), args)
    return "", 0, 0


def diff_size(diff: str) -> tuple[int, int]:
    """Lines added and removed in a unified diff."""
    added = removed = 0
    for line in str(diff).splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def text_size(old: str, new: str) -> tuple[int, int]:
    """Lines added and removed going from ``old`` to ``new``."""
    added = removed = 0
    for line in difflib.ndiff(str(old).splitlines(), str(new).splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def picture(result: object) -> str:
    """The first picture path a tool's result names ("" when none): a render or a
    texture that just landed."""
    for text in _texts(result):
        found = _PICTURE.search(text)
        if found:
            return found.group(1)
    return ""


def _texts(result: object) -> list[str]:
    if isinstance(result, str):
        return [result]
    if isinstance(result, dict):
        found: list[str] = []
        for value in result.values():
            found += _texts(value)
        return found
    if isinstance(result, list):
        found = []
        for value in result:
            found += _texts(value)
        return found
    return []


def _edited(path: str, args: dict[str, Any]) -> tuple[str, int, int]:
    if not path:
        return "", 0, 0
    if isinstance(args.get("edits"), list):
        added = removed = 0
        for edit in args["edits"]:
            if isinstance(edit, dict):
                more, fewer = text_size(
                    str(edit.get("old_string", edit.get("old", ""))),
                    str(edit.get("new_string", edit.get("new", ""))),
                )
                added, removed = added + more, removed + fewer
        return path, added, removed
    if isinstance(args.get("patch"), list):
        added = removed = 0
        for pair in args["patch"]:
            if isinstance(pair, dict):
                more, fewer = text_size(
                    str(pair.get("old", "")), str(pair.get("new", ""))
                )
                added, removed = added + more, removed + fewer
        return path, added, removed
    old = args.get("old_string", args.get("old_str", args.get("old_text")))
    new = args.get("new_string", args.get("new_str", args.get("new_text")))
    if old is not None or new is not None:
        return path, *text_size(str(old or ""), str(new or ""))
    content = args.get("content", args.get("file_text"))
    if isinstance(content, str):
        return path, len(content.splitlines()), 0
    return path, 0, 0


def _path(args: dict[str, Any]) -> str:
    for key in (
        "file_path",
        "filePath",
        "path",
        "absolute_path",
        "notebook_path",
        "target_file",
        "file",
    ):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    paths = args.get("paths")
    if isinstance(paths, list) and paths:
        return str(paths[0])
    return ""


def _patch_file(args: dict[str, Any]) -> str:
    patch = str(args.get("input") or args.get("patch") or "")
    found = re.search(r"^\*\*\* (?:Update|Add) File: (.+)$", patch, re.MULTILINE)
    if found:
        return found.group(1).strip()
    found = re.search(r"^\+\+\+ (?:b/)?(.+)$", patch, re.MULTILINE)
    return found.group(1).strip() if found else ""


def _apps(server: str, tool: str, arguments: dict[str, Any]) -> tuple[str, ...]:
    if server in APPS and server != "joinery":
        return (server,)
    try:
        from joinery.gateway import op_apps

        apps = op_apps(tool, arguments)
    except Exception:  # noqa: BLE001 - the words never stop the work they describe
        apps = ()
    return apps or ((server,) if server and server != "joinery" else ())


def _subject(arguments: dict[str, Any]) -> str:
    """What the call works on, in words ("art/props/mug.blend" -> "the mug")."""
    for key in ("source", "model", "path", "image", "graph", "document", "name"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            stem = PurePosixPath(value.removeprefix("res://")).stem
            return "the " + words(stem) if stem else ""
    return ""


def _detail(tool: str, arguments: dict[str, Any]) -> str:
    """The operations a call runs and its few numbers ("bevel, 2 segments")."""
    steps = arguments.get("steps")
    if isinstance(steps, list) and steps:
        names = [
            words(str(step.get("op", "")))
            for step in steps
            if isinstance(step, dict) and step.get("op")
        ]
        shown = ", ".join(names[:3])
        return shown + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    parts = [words(str(arguments.get("op") or arguments.get("operation") or tool))]
    for key, value in arguments.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = f"{value:g}" if isinstance(value, float) else str(value)
        parts.append(f"{number} {words(key)}")
        if len(parts) == 3:
            break
    return ", ".join(part for part in parts if part)


def _fill(template: str, arguments: dict[str, Any]) -> str:
    """``template`` with each ``{arg}`` from the call; a line whose arg is missing
    keeps only what comes before it."""
    line = template
    for field in re.findall(r"\{(\w+)\}", template):
        value = arguments.get(field)
        if isinstance(value, str) and value.strip():
            shown = file_name(value) if field == "path" else value
            line = line.replace("{" + field + "}", shown)
        else:
            line = re.split(r"[:\s]*(?:for |of |at )?\{" + field + r"\}", line)[0]
    return line.strip()
