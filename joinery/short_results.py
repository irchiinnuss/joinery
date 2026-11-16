"""Short forms of the tool results agents get most: check reports, tool operations,
blockouts and the game's setup.

``joinery.compact`` cuts any long result down evenly to a budget. These go further for
the shapes that come back again and again in a request, because a result stays in
the agent's context for every later call of its session: a precheck that lists 354
staged files, nine look failures that differ only in a texture name, an operation
that repeats its own record twice inside itself. Each short form keeps what the
agent acts on (status, counts, every distinct problem with its fix, what was made
where) and says what it left out; the full result is kept and ``details`` reads it.

Shapes, not tool names, decide: the same for every agent and every tool server.
Nothing here changes what a tool did.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

#: Staged paths named in a check report; the rest are counted by kind.
CHANGES_NAMED = 8
#: Characters of a problem's message: its first line, cut.
MESSAGE_CHARS = 300
#: Blockout walk targets listed when every one was reached.
TARGETS_NAMED = 3

#: Paths Godot or Joinery write beside a change; never named in a summary.
_BESIDE = (".import", ".uid")


def _first_line(text: str) -> str:
    lines = [line for line in str(text).splitlines() if line.strip()]
    if not lines:
        return ""
    head = lines[0].strip()
    rest = len(lines) - 1
    if len(head) > MESSAGE_CHARS:
        head = head[: MESSAGE_CHARS - 1].rstrip() + "…"
    return head + (f" (+{rest} lines)" if rest else "")


def _changes(rows: list[Any], cuts: list[str]) -> dict[str, Any]:
    """A long list of staged paths as counts by kind and the first few files."""
    kinds: Counter[str] = Counter()
    named: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path", ""))
        kinds[str(row.get("change", "changed"))] += 1
        if path and not path.endswith(_BESIDE) and len(named) < CHANGES_NAMED:
            named.append(path)
    cuts.append(f"changes: {len(rows)} paths counted by kind")
    return {"counts": dict(kinds), "first": named, "total": len(rows)}


def _rule(problem: dict[str, Any]) -> str:
    items = problem.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return str(items[0].get("rule", ""))
    return ""


def _problems(rows: list[Any], cuts: list[str]) -> list[Any]:
    """Problems with their first message line; alike ones (same check and rule)
    folded into the first, which says how many and where the rest are."""
    out: list[Any] = []
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    folded = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            out.append(row)
            continue
        key = (str(row.get("check", "")), _rule(row))
        if key[1] and key in groups:
            group = groups[key]
            group["alike"] = int(group.get("alike", 0)) + 1
            group["alike_at"].append(f"problems/{index}")
            folded += 1
            continue
        short: dict[str, Any] = {
            k: v for k, v in row.items() if k not in {"message", "items"}
        }
        short["message"] = _first_line(str(row.get("message", "")))
        items = row.get("items")
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict):
                first = {
                    k: (_first_line(str(v)) if isinstance(v, str) else v)
                    for k, v in first.items()
                    if v not in ("", None)
                }
            short["items"] = [first]
            if len(items) > 1:
                short["more_items"] = len(items) - 1
        paths = short.get("paths")
        if isinstance(paths, list) and len(paths) > CHANGES_NAMED:
            short["paths"] = paths[:CHANGES_NAMED] + [
                f"… {len(paths) - CHANGES_NAMED} more"
            ]
        out.append(short)
        if key[1]:
            short["alike_at"] = []
            groups[key] = short
    for group in groups.values():
        if not group["alike_at"]:
            del group["alike_at"]
        else:
            where = group["alike_at"]
            group["alike_at"] = where if len(where) <= 3 else [where[0], "…", where[-1]]
    cuts.append(
        "problems: first lines only"
        + (f", {folded} alike folded into the first of each kind" if folded else "")
    )
    return out


def _is_check_report(value: dict[str, Any]) -> bool:
    return isinstance(value.get("problems"), list) and isinstance(
        value.get("changes"), list
    )


def _check_report(value: dict[str, Any], cuts: list[str]) -> dict[str, Any]:
    out = dict(value)
    if len(value["changes"]) > CHANGES_NAMED:
        out["changes"] = _changes(value["changes"], cuts)
    if value["problems"]:
        out["problems"] = _problems(value["problems"], cuts)
    return out


def _models(models: dict[str, Any], cuts: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path, meshes in models.items():
        if not isinstance(meshes, list):
            out[path] = meshes
            continue
        out[path] = [
            f"{m.get('mesh', '')}: {m.get('vertices', 0)} vertices"
            + (f", {m['morph_targets']} shape keys" if m.get("morph_targets") else "")
            if isinstance(m, dict)
            else m
            for m in meshes
        ]
    cuts.append("models: one line per mesh")
    return out


def _is_operation(value: dict[str, Any]) -> bool:
    return "recipe" in value and isinstance(value.get("outputs"), list)


def _operation(value: dict[str, Any], cuts: list[str]) -> dict[str, Any]:
    """A tool operation's record without the records of the steps inside it."""
    out: dict[str, Any] = {}
    made = {str(p) for p in value.get("outputs", [])}
    inner: list[str] = []
    for key, item in value.items():
        if isinstance(item, dict) and _is_operation(item):
            # A nested step's own record (the painting under a painted map): its
            # files are what matters; the status and review flags repeat the outer.
            more = [str(p) for p in item.get("outputs", []) if str(p) not in made]
            made |= set(more)
            if more:
                out.setdefault("also_made", []).extend(more)
            inner.append(key)
            continue
        if key == "models" and isinstance(item, dict):
            out[key] = _models(item, cuts)
            continue
        out[key] = item
    if inner:
        cuts.append(", ".join(inner) + ": nested step records, their files kept")
    return out


def _blockout(value: dict[str, Any], cuts: list[str]) -> dict[str, Any]:
    walk = dict(value["walk"])
    targets = [t for t in walk["targets"] if isinstance(t, dict)]
    missed = [t for t in targets if not t.get("reached")]
    walk["reached"] = len(targets) - len(missed)
    walk["targets"] = missed or [str(t.get("id", "")) for t in targets[:TARGETS_NAMED]]
    cuts.append(f"walk/targets: {walk['reached']} reached ones left out")
    out = dict(value)
    out["walk"] = walk
    if out.get("facts") == [walk.get("fact")]:
        out.pop("facts")  # The walk's own fact, said twice.
    return out


def _setup_units(units: dict[str, Any], cuts: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, unit in units.items():
        if isinstance(unit, dict) and isinstance(unit.get("files"), list):
            out[name] = {**unit, "files": len(unit["files"])}
        else:
            out[name] = unit
    cuts.append("units: file lists counted")
    return out


def _shorten(value: Any, cuts: list[str]) -> Any:
    if not isinstance(value, dict):
        return value
    if _is_check_report(value):
        return _check_report(value, cuts)
    if _is_operation(value):
        return _operation(value, cuts)
    walk = value.get("walk")
    if isinstance(walk, dict) and isinstance(walk.get("targets"), list):
        return _blockout(value, cuts)
    game = value.get("game")
    if isinstance(game, dict) and isinstance(game.get("units"), dict):
        return {**value, "game": {**game, "units": _setup_units(game["units"], cuts)}}
    if isinstance(value.get("units"), dict) and "blueprint" in value:
        return {**value, "units": _setup_units(value["units"], cuts)}
    return value


def _tool_result(value: dict[str, Any], cuts: list[str]) -> dict[str, Any]:
    """A project tool's MCP result: its JSON text parts and structured content."""
    out = dict(value)
    parts: list[Any] = []
    for part in value.get("content", []):
        text = part.get("text") if isinstance(part, dict) else None
        if isinstance(text, str) and text.lstrip()[:1] == "{":
            try:
                decoded = json.loads(text)
            except ValueError:
                decoded = None
            if isinstance(decoded, dict):
                short = _shorten(decoded, cuts)
                if short is not decoded:
                    part = {**part, "text": json.dumps(short, ensure_ascii=False)}
        parts.append(part)
    out["content"] = parts
    structured = value.get("structuredContent")
    if isinstance(structured, dict):
        out["structuredContent"] = _shorten(structured, [])
    return out


def shorten(value: Any) -> tuple[Any, list[str]]:
    """``value`` in its short form, and what was left out ([] when it has none)."""
    cuts: list[str] = []
    if isinstance(value, dict) and isinstance(value.get("content"), list):
        short = _tool_result(value, cuts)
    else:
        short = _shorten(value, cuts)
    return (short, cuts) if cuts else (value, [])
