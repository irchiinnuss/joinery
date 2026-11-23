"""A recipe's operations in a few lines, so an agent needn't read its op log whole.

A recipe's op log (``recipes/<id>.py`` for Blender and Krita, ``.scd`` for
SuperCollider) is mostly Joinery's own helper code: a Blender log runs to 1,300
lines and 50k characters for a handful of operations. ``find_recipe(recipe=...)``
answers with this summary instead: the record (tool, outputs, sources, note), the
op log's size, its operations by name with how often each runs and what they name,
and the last few operations as they are written. Read-only.
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from joinery.recipe import RECIPE_DIR

#: Operations shown as written, from the end of the log.
LAST = 8
#: Characters kept of one operation as written.
OP_CHARS = 160
#: Operation names listed, most used first.
NAMES = 16
#: Lines of a log's opening comment kept.
HEAD_COMMENT = 12
_ID = re.compile(r"[0-9a-f]{32}")
#: Top-level statements that set the scene up rather than do anything.
_SETUP = re.compile(
    r"^(sys\.excepthook|os\.chdir|bpy\.ops\.wm\.read_factory_settings|"
    r"bpy\.context\.preferences|bpy\.context\.scene\.unit_settings|_JOINERY_|"
    r"_joinery_[a-z_]*\s*=)"
)
#: The open, check, save and export every Blender op log runs around its operations.
_FRAMING = frozenset(
    {
        "bpy.ops.wm.open_mainfile",
        "_joinery_scope_check",
        "_joinery_settle_images",
        "assert",
        "bpy.ops.file.pack_all",
        "bpy.ops.wm.save_as_mainfile",
        "_joinery_export_textures",
        "_joinery_bake_shape_keys",
        "_joinery_bake_for_export",
        "_joinery_drop_rig_widgets",
        "_joinery_name_parts",
        "bpy.ops.export_scene.gltf",
    }
)
_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(")
_SYNTH = re.compile(r"SynthDef\(\s*\\?['\"]?([A-Za-z_][A-Za-z0-9_]*)")
_UGEN = re.compile(r"\b([A-Z][A-Za-z0-9]+)\.(?:ar|kr|ir)\b")


def _record(root: Path, ref: str) -> Path:
    """The recipe record ``ref`` names: an id, a recipe path, or one of its outputs."""
    ref = ref.strip().removeprefix("res://")
    match = _ID.search(ref)
    if match:
        for folder in (RECIPE_DIR, f"{RECIPE_DIR}/history"):
            record = root / folder / f"{match.group(0)}.toml"
            if record.is_file():
                return record
        raise ValueError(f"No recipe {match.group(0)} here.")
    named = root / ref
    if named.suffix == ".toml" and named.is_file():
        return named
    if PurePosixPath(ref).parts[:1] == (RECIPE_DIR,):
        record = named.with_suffix(".toml")
        if record.is_file():
            return record
    for record in sorted((root / RECIPE_DIR).glob("*.toml")):
        try:
            data = tomllib.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if ref in [str(o) for o in data.get("outputs", [])]:
            return record
    raise ValueError(
        f"No recipe makes {ref}. Give a recipe id, or ask find_recipe(request=...)."
    )


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _name(node.value)
        return f"{inner}.{node.attr}" if inner else node.attr
    if isinstance(node, ast.Call):
        return _name(node.func)
    return ""


def _target(call: ast.Call) -> str:
    """The first string a call is given: the object, material or file it works on."""
    for arg in [*call.args, *(k.value for k in call.keywords)]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value[:80]
    return ""


def _cut(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= OP_CHARS else line[: OP_CHARS - 1] + "…"


def _python_ops(
    script: str,
) -> tuple[list[str], list[str], dict[str, list[str]], int]:
    """(op names in order, ops as written, targets by name, framing statements) of
    a Python op log."""
    try:
        tree = ast.parse(script)
    except (SyntaxError, ValueError):
        return [], [], {}, 0
    framing = 0
    names: list[str] = []
    written: list[str] = []
    targets: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(
            node,
            ast.FunctionDef
            | ast.AsyncFunctionDef
            | ast.ClassDef
            | ast.Import
            | ast.ImportFrom,
        ):
            continue
        source = ast.get_source_segment(script, node) or ""
        if not source or _SETUP.match(source):
            continue
        value = getattr(node, "value", None)
        if isinstance(node, ast.Assign | ast.AnnAssign) and not isinstance(
            value, ast.Call
        ):
            continue
        if isinstance(value, ast.Call):
            name = _name(value.func) or type(node).__name__.lower()
            target = _target(value)
        else:
            name, target = type(node).__name__.lower(), ""
        if name in _FRAMING or source.startswith("for _joinery"):
            framing += 1
            continue
        names.append(name)
        written.append(_cut(source))
        if target and target not in targets.setdefault(name, []):
            targets[name].append(target)
    return names, written, targets, framing


def _comment_head(script: str, marker: str) -> list[str]:
    head: list[str] = []
    for line in script.splitlines():
        stripped = line.strip().lstrip('"')
        if stripped.startswith(marker):
            head.append(_cut(stripped.lstrip(marker[0]).strip()))
        elif head:
            break
        if len(head) >= HEAD_COMMENT:
            break
    return head


def summarise(root: Path, ref: str, last: int = LAST) -> dict[str, Any]:
    """The recipe ``ref`` names (under ``root``: the project, or its staged change)
    in short: record, op log size, operations by name and the last ``last``."""
    record = _record(root, ref)
    data = tomllib.loads(record.read_text(encoding="utf-8"))
    script_path = str(data.get("script", ""))
    out: dict[str, Any] = {
        "recipe": record.relative_to(root).as_posix(),
        "tool": str(data.get("tool", "")),
        "script": script_path,
        "outputs": [str(o) for o in data.get("outputs", [])][:12],
    }
    for key in ("sources", "note", "seed"):
        if data.get(key):
            out[key] = data[key]
    if PurePosixPath(record.parent.name) == PurePosixPath("history"):
        out["superseded"] = True
    script = ""
    if script_path:
        try:
            script = (root / script_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            out["script_missing"] = True
    if script:
        out["script_size"] = {"lines": script.count("\n") + 1, "chars": len(script)}
    suffix = PurePosixPath(script_path).suffix
    if suffix == ".py" and script:
        names, written, targets, framing = _python_ops(script)
        counts = Counter(names)
        out["operations"] = len(names)
        if framing:
            out["framing"] = f"{framing} open, check, save and export steps left out"
        out["by_name"] = {
            name: (
                {"count": count, "targets": targets[name][:6]}
                if targets.get(name)
                else count
            )
            for name, count in counts.most_common(NAMES)
        }
        out["last"] = written[-max(1, last) :]
        comments = [
            _cut(line.strip()[1:].strip())
            for line in script.splitlines()
            if line.startswith("# ") and not line.startswith("# ---")
        ]
        if comments:
            out["notes"] = comments[-max(1, last) :]
    elif script:
        out["about"] = _comment_head(script, "//")
        synths = _SYNTH.findall(script)
        if synths:
            out["synths"] = list(dict.fromkeys(synths))[:NAMES]
        ugens = Counter(_UGEN.findall(script))
        if ugens:
            out["ugens"] = dict(ugens.most_common(NAMES))
        if not synths and not ugens:
            calls = Counter(c for c in _CALL.findall(script) if not c.startswith("_"))
            out["calls"] = dict(calls.most_common(NAMES))
    out["next"] = (
        "Read only the lines you need from the script (search_project for an "
        "operation's line, then a range); change its parameters with the owning "
        "tool's operations."
    )
    return out


def lookup(roots: list[Path], ref: str, last: int = LAST) -> dict[str, Any]:
    """:func:`summarise` from the first of ``roots`` that has the recipe (the staged
    change, then the project)."""
    problem: ValueError | None = None
    for root in dict.fromkeys(roots):
        try:
            return summarise(root, ref, last)
        except ValueError as exc:
            problem = problem or exc
    raise problem or ValueError("No recipe here.")
