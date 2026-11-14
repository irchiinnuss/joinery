"""Sketch to blockout: a typed level layout, checked and built into a greybox scene.

The user shows Journeyman a sketch of a level; the agent reads it and writes a
layout (:mod:`joinery.blockout.layout`); Joinery checks it (:mod:`.check`, walking
from the spawn to every goal with :mod:`.reach`), builds the scene
(:mod:`.build`) and draws its plan (:mod:`.plan`). The layout file is the recipe:
the same layout always builds the same scene bytes. ADR 0043.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from joinery.blockout import build as scene_build
from joinery.blockout import plan as plan_picture
from joinery.blockout.check import Checked, check
from joinery.blockout.layout import Layout, LayoutError, Mode, parse, to_toml
from joinery.blockout.merge import merge as merge_scene
from joinery.blockout.player import measure
from joinery.blockout.world import Profile, profile_for

DESIGN_DIR = "design/blockouts"
SCRIPT_FILE = scene_build.SCRIPT_PATH.removeprefix("res://")
#: The mode each starting point's level plays in.
BLUEPRINT_MODES = {
    "third-person-3d": "3d",
    "first-person-3d": "3d",
    "vr-room": "3d",
    "platformer-2d": "2d-side",
    "top-down": "2d-topdown",
}

__all__ = [
    "Built",
    "Layout",
    "LayoutError",
    "make",
    "parse",
    "project_blueprint",
    "to_toml",
]


@dataclass
class Built:
    """A checked layout and the files it builds (nothing written yet)."""

    layout: Layout
    checked: Checked
    profile: Profile
    #: Project-relative path -> bytes: the layout, the scene, and the level script.
    files: dict[str, bytes]
    layout_file: str
    scene: str
    plan_png: bytes

    @property
    def ok(self) -> bool:
        return self.checked.ok

    def counts(self) -> dict[str, int]:
        world = self.checked.world
        return {
            "rooms": sum(1 for a in world.areas if a.kind == "room"),
            "corridors": sum(1 for a in world.areas if a.kind == "corridor"),
            "stairs": sum(1 for a in world.areas if a.kind == "stairs"),
            "platforms": len(world.platforms),
            "wall_pieces": len(world.pieces),
            "openings": len(self.layout.openings),
            "markers": len(world.markers),
        }


def project_blueprint(project: Path) -> str:
    """The starting point the project was made from ("" for none)."""
    try:
        from joinery.blueprints import read_record

        record = read_record(project)
    except (OSError, ValueError):
        return ""
    return record.blueprint if record is not None else ""


def scene_for(layout: Layout, scene: str | None) -> str:
    """The res:// scene the layout builds into: ``scene``, else game/<name>_blockout.tscn."""
    path = scene or f"res://game/{layout.name}_blockout.tscn"
    if not path.startswith("res://"):
        path = "res://" + path.lstrip("/")
    relative = path.removeprefix("res://")
    if (
        not relative.endswith(".tscn")
        or ".." in Path(relative).parts
        or relative.startswith(("addons/", ".", "design/"))
        or not re.fullmatch(r"[A-Za-z0-9_./-]+", relative)
    ):
        raise LayoutError(
            [
                f"scene: {path} must be a res:// .tscn path in the game (not addons/ or design/)."
            ]
        )
    return path


def player_profile(project: Path, mode: Mode, blueprint: str | None = None) -> Profile:
    """The player every layout check and generator uses: ``blueprint``'s (default the
    project's own starting point), measured from the game's own player scene when it
    is the project's (:mod:`.player`). Growing and checking share it, so whatever is
    grown is reachable by the check's player."""
    own = project_blueprint(project)
    chosen = blueprint or own
    profile = profile_for(chosen, mode)
    if chosen == own:
        # The player this game actually spawns, not only its starting point's.
        profile = measure(project, profile)
    return profile


def make(
    project: Path,
    data: Any,
    *,
    scene: str | None = None,
    blueprint: str | None = None,
) -> Built:
    """Check ``data`` (a layout dict or TOML) for ``project`` and build its files.

    ``blueprint`` picks the player the walk check uses (default: the project's own
    starting point). Raises :class:`LayoutError` when the layout can't be read at all;
    problems the agent can fix are in ``built.checked.problems``.
    """
    layout = parse(data)
    chosen = blueprint or project_blueprint(project)
    profile = player_profile(project, layout.mode, blueprint)
    expected = BLUEPRINT_MODES.get(chosen, "")
    checked = check(layout, profile, expected_mode=expected)
    path = scene_for(layout, scene)
    layout_file = f"{DESIGN_DIR}/{layout.name}.toml"
    text = to_toml(layout).encode()
    files: dict[str, bytes] = {layout_file: text}
    if checked.ok:
        sha = hashlib.sha256(text).hexdigest()
        built = scene_build.build(checked.world, "res://" + layout_file, sha)
        files[path.removeprefix("res://")] = built.encode()
        script = project / SCRIPT_FILE
        if not script.is_file():
            files[SCRIPT_FILE] = scene_build.SCRIPT_SOURCE.read_bytes()
    picture = plan_picture.render(checked.world, checked.reach)
    return Built(layout, checked, profile, files, layout_file, path, picture)


_RECORDED = re.compile(r'^metadata/blockout_(layout|sha256) = "([^"]*)"$', re.MULTILINE)


def merged_files(root: Path, built: Built) -> tuple[dict[str, bytes], list[str]]:
    """``built.files`` to write into ``root`` (the project or a review stage), with
    the scene merged into the one already there (:mod:`.merge`): placed enemies,
    pickups and changed materials survive a rebuild. Returns the files and what the
    merge kept or moved, in plain sentences."""
    files = dict(built.files)
    relative = built.scene.removeprefix("res://")
    target = root / relative
    if relative not in files or not target.is_file():
        return files, []
    try:
        old = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return files, []
    recorded = dict(_RECORDED.findall(old))
    base: str | None = None
    source = root / recorded.get("layout", "").removeprefix("res://")
    if recorded.get("layout") and source.is_file():
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() == recorded.get("sha256"):
            try:
                before = make(
                    root,
                    data.decode("utf-8"),
                    scene=built.scene,
                    blueprint=built.profile.blueprint or None,
                )
                base = before.files.get(relative, b"").decode("utf-8") or None
            except (LayoutError, UnicodeDecodeError, ValueError):
                base = None
    try:
        merged = merge_scene(old, files[relative].decode("utf-8"), base)
    except ValueError:
        return files, []
    files[relative] = merged.text.encode("utf-8")
    return files, merged.notes


def route(built: Built) -> dict[str, Any]:
    """The walk the playtest bot takes in Godot: every reachable target's waypoints in
    the scene's own coordinates (3D: x, height, z)."""
    world = built.checked.world
    reach = built.checked.reach
    spawn = world.spawn
    if reach is None or spawn is None:
        return {}
    three_d = world.three_d

    def point(x: float, y: float, height: float) -> list[float]:
        return (
            [round(x, 3), round(height, 3), round(y, 3)]
            if three_d
            else [round(x, 2), round(y, 2)]
        )

    return {
        "version": 1,
        "mode": world.mode,
        "spawn": point(spawn.at[0], spawn.at[1], spawn.height),
        "reach": 0.9 if three_d else max(world.profile.radius * 2.5, 12.0),
        "targets": [
            {
                "id": target.id,
                "kind": target.kind,
                "at": point(
                    target.at[0], target.at[1], world.floor_at(*target.at) or 0.0
                ),
                "waypoints": [
                    [*point(x, y, h), bool(jump)] for x, y, h, jump in target.route
                ],
            }
            for target in reach.targets
            if target.reached
        ],
    }
