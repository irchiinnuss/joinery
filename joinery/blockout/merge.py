"""Rebuild a blockout scene without losing what was placed in it.

A layout rebuild writes the greybox again, but the scene is also where the game grows:
enemies and pickups placed under their markers, a Krita material put on a floor, a
script on a door. :func:`merge` keeps them, by node path (``Markers/<marker id>``,
``Rooms/<room id>/Floor``):

* **Placed nodes** (in the scene, not built from the layout) are kept under the same
  parent. When the layout no longer has that parent (a marker or room was removed),
  they move up to the nearest node that is still there, and the merge says so.
* **Looks** (``material``, ``material_override``, ``surface_material_override/*``,
  ``mesh``, ``texture``, colours, a script, metadata) changed on a built node are
  kept on the rebuilt node of the same path. Shape and place come from the layout.
* Signal connections and editable children are kept while both ends exist, and the
  scene's own ``uid`` is kept so nothing that refers to it breaks.

What counts as built is known exactly when the layout the scene was last built from is
still at hand (its sha256 is on the scene's root): that layout is built again and
compared. Otherwise a node counts as placed when it is an instanced scene or carries
a script, and a look is kept when it differs from the rebuilt one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Properties that are a node's look, not its shape or place.
_LOOK = re.compile(
    r"^(?:material(?:_override|_overlay)?|surface_material_override/\d+|mesh|texture"
    r"|texture_\w+|modulate|self_modulate|color|script|metadata/(?!blockout_)\w+)$"
)
_HEADER = re.compile(r"^\[(\w+)(?:\s(.*))?\]\s*$")
_ATTR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\[[^\]]*\]|[^\s\]]+)')
_REF = re.compile(r'(ExtResource|SubResource)\(\s*"([^"]+)"\s*\)')
_PROP = re.compile(r"^([\w/]+) = (.*)$")


@dataclass
class _Section:
    kind: str
    attrs: dict[str, str]
    lines: list[str] = field(default_factory=list)

    def attr(self, key: str) -> str:
        return self.attrs.get(key, "").strip('"')

    def props(self) -> dict[str, str]:
        found: dict[str, str] = {}
        key = ""
        for line in self.lines:
            match = _PROP.match(line)
            if match:
                key = match.group(1)
                found[key] = match.group(2)
            elif key:
                found[key] += "\n" + line
        return found

    def copy(self) -> _Section:
        return _Section(self.kind, dict(self.attrs), list(self.lines))

    def text(self) -> str:
        head = " ".join(f"{k}={v}" for k, v in self.attrs.items())
        return "\n".join([f"[{self.kind}{' ' + head if head else ''}]", *self.lines])


@dataclass
class _Scene:
    header: _Section
    sections: list[_Section]

    def of(self, kind: str) -> list[_Section]:
        return [s for s in self.sections if s.kind == kind]

    def nodes(self) -> dict[str, _Section]:
        return {_path(n): n for n in self.of("node")}

    def resource(self, kind: str, ident: str) -> _Section | None:
        wanted = "ext_resource" if kind == "ExtResource" else "sub_resource"
        return next((s for s in self.of(wanted) if s.attr("id") == ident), None)


def _parse(text: str) -> _Scene:
    sections: list[_Section] = []
    for line in text.splitlines():
        match = _HEADER.match(line)
        if match:
            sections.append(
                _Section(match.group(1), dict(_ATTR.findall(match.group(2) or "")))
            )
        elif sections and line.strip():
            sections[-1].lines.append(line)
    if not sections or sections[0].kind != "gd_scene":
        raise ValueError("not a text scene")
    return _Scene(sections[0], sections[1:])


def _path(node: _Section) -> str:
    if "parent" not in node.attrs:
        return "."
    parent, name = node.attr("parent"), node.attr("name")
    return name if parent == "." else f"{parent}/{name}"


def _parent(path: str) -> str:
    return path.rpartition("/")[0] or "."


def _resolved(scene: _Scene, value: str) -> str:
    """``value`` with each resource replaced by what it is, for comparing scenes."""

    def body(match: re.Match[str]) -> str:
        found = scene.resource(match.group(1), match.group(2))
        if found is None:
            return match.group(0)
        if found.kind == "ext_resource":
            return f"ext:{found.attr('path')}"
        inner = _resolved(scene, "\n".join(found.lines))
        return f"sub:{found.attr('type')}{{{inner}}}"

    return _REF.sub(body, value)


@dataclass
class Merged:
    text: str
    #: Plain sentences: what was kept, and what had to move.
    notes: list[str]


class _Merge:
    def __init__(self, old: _Scene, new: _Scene) -> None:
        self.old = old
        self.new = new
        self.pool = {
            "ExtResource": {s.attr("id"): s for s in new.of("ext_resource")},
            "SubResource": {s.attr("id"): s for s in new.of("sub_resource")},
        }
        self.extra: dict[str, list[_Section]] = {"ExtResource": [], "SubResource": []}
        self.renamed: dict[tuple[str, str], str] = {}

    def bring(self, kind: str, ident: str) -> str:
        """The id, in the rebuilt scene, of the old scene's resource ``ident``."""
        key = (kind, ident)
        if key in self.renamed:
            return self.renamed[key]
        source = self.old.resource(kind, ident)
        if source is None:
            return ident
        pool = self.pool[kind]
        if kind == "ExtResource":
            same = next(
                (i for i, s in pool.items() if s.attr("path") == source.attr("path")),
                None,
            )
            if same is not None:
                self.renamed[key] = same
                return same
        fresh = ident
        while fresh in pool:
            fresh = "kept_" + fresh
        self.renamed[key] = fresh
        copy = source.copy()
        copy.attrs["id"] = f'"{fresh}"'
        pool[fresh] = copy
        # What it refers to is brought first: Godot reads a scene top down and
        # cannot use a sub-resource before it is defined.
        copy.lines = [self.rewrite(line) for line in copy.lines]
        self.extra[kind].append(copy)
        return fresh

    def rewrite(self, text: str) -> str:
        return _REF.sub(
            lambda m: f'{m.group(1)}("{self.bring(m.group(1), m.group(2))}")', text
        )


def merge(old_text: str, new_text: str, base_text: str | None = None) -> Merged:
    """``new_text`` (the rebuilt scene) with what ``old_text`` (the scene on disk)
    added to ``base_text`` (the old layout's own build; None when unknown)."""
    old, new = _parse(old_text), _parse(new_text)
    base = _parse(base_text) if base_text else None
    old_nodes, new_nodes = old.nodes(), new.nodes()
    base_nodes = base.nodes() if base is not None else None
    work = _Merge(old, new)

    def placed(path: str) -> bool:
        if path == ".":
            return False
        if base_nodes is not None:
            return path not in base_nodes
        node = old_nodes[path]
        if path not in new_nodes and (
            "instance" in node.attrs or "script" in node.props()
        ):
            return True
        up = _parent(path)
        return up in old_nodes and placed(up)

    notes: list[str] = []
    kept: list[_Section] = []
    present = set(new_nodes)
    #: Kept nodes that moved: old path -> new path, so their children follow them.
    moved: dict[str, str] = {}
    dropped: set[str] = set()
    for path, node in old_nodes.items():
        if path in new_nodes or not placed(path):
            continue
        if _parent(path) in dropped:
            dropped.add(path)
            continue
        parent = _parent(path)
        parent = moved.get(parent, parent)
        target = parent
        while target != "." and target not in present:
            target = _parent(target)
        copy = node.copy()
        if parent != _parent(path):
            copy.attrs["parent"] = f'"{target}"'
        if target != parent:
            copy.attrs["parent"] = f'"{target}"'
            where = target if target != "." else "the scene root"
            notes.append(
                f"{path} was under {parent}, which the layout no longer has; "
                f"it is now under {where}."
            )
        final = copy.attr("name") if target == "." else f"{target}/{copy.attr('name')}"
        if final in present:
            notes.append(
                f"{path} clashes with a rebuilt node of the same name; dropped."
            )
            dropped.add(path)
            continue
        present.add(final)
        if final != path:
            moved[path] = final
        copy.lines = [work.rewrite(line) for line in copy.lines]
        if "instance" in copy.attrs:
            copy.attrs["instance"] = work.rewrite(copy.attrs["instance"])
        kept.append(copy)
    looks = 0
    for path, node in new_nodes.items():
        mine = old_nodes.get(path)
        if mine is None:
            continue
        theirs = node.props()
        was = base_nodes[path].props() if base_nodes and path in base_nodes else None
        changed = False
        for key, value in mine.props().items():
            if not _LOOK.match(key):
                continue
            if was is not None and was.get(key) == value:
                continue  # as the old layout built it: the new layout decides
            if was is None and _resolved(old, value) == _resolved(
                new, theirs.get(key, "")
            ):
                continue
            theirs[key] = work.rewrite(value)
            changed = True
        if changed:
            looks += 1
            node.lines = [f"{k} = {v}" for k, v in theirs.items()]
    if kept:
        notes.insert(0, f"Kept {len(kept)} placed node{'s' if len(kept) != 1 else ''}.")
    if looks:
        notes.insert(
            0, f"Kept the look of {looks} rebuilt node{'s' if looks != 1 else ''}."
        )
    return Merged(_assemble(work, kept), notes)


def _assemble(work: _Merge, kept: list[_Section]) -> str:
    old, new = work.old, work.new
    paths = set(new.nodes()) | {_path(n) for n in kept}
    connections = [
        c
        for c in old.of("connection")
        if c.attr("from") in paths and c.attr("to") in paths
    ]
    editable = [e for e in old.of("editable") if e.attr("path") in paths]
    exts = new.of("ext_resource") + work.extra["ExtResource"]
    subs = new.of("sub_resource") + work.extra["SubResource"]
    attrs = dict(new.header.attrs)
    if "uid" in old.header.attrs:
        attrs["uid"] = old.header.attrs["uid"]
    attrs["load_steps"] = str(len(exts) + len(subs) + 1)
    order = ("load_steps", "format", "uid")
    header = _Section(
        "gd_scene",
        {k: attrs[k] for k in order if k in attrs}
        | {k: v for k, v in attrs.items() if k not in order},
    )
    parts = [header.text(), *(s.text() for s in exts), *(s.text() for s in subs)]
    parts += [n.text() for n in new.of("node")] + [n.text() for n in kept]
    parts += [c.text() for c in connections] + [e.text() for e in editable]
    return "\n\n".join(parts) + "\n"
