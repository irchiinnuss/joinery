"""The project's own player, measured: its collider, how high it steps and jumps.

The walk check (:mod:`.reach`) needs what the player can do. Each starting point has a
profile tuned against the playtest bot (:data:`.world.PROFILES`); a game's player
drifts from it as soon as someone edits the player scene or script. So the check reads
the player the game actually spawns (``game/hooks/player_scene`` in project.godot):

* body: the root body's own collision shape (capsule, cylinder, box, sphere; circle,
  rectangle or capsule in 2D) gives the radius (half-width side-on) and height;
* step: a body without step logic only rides over an edge its rounded bottom meets
  at a floor angle (``r * (1 - cos floor_max_angle)``), or what the script exports
  as ``step_height``;
* jump: ``jump_velocity`` (or ``jump_speed``) against the rising gravity
  (``rise_gravity`` or the project's default gravity) gives the apex, and the run
  speed and air time give the widest gap.

Jump height and gap are capped by the starting point's profile, whose numbers were
measured with the bot's own jumps (held, not perfect): a stronger jump never makes the
check promise more than the bot shows, a weaker one makes it promise less. Anything
that can't be read keeps the profile's number.
"""

from __future__ import annotations

import math
import re
from dataclasses import replace
from pathlib import Path

from joinery.blockout.world import Profile

#: How much of the ideal jump a ledge or gap may need: the body's rounded bottom must
#: clear the edge, and a run-up is never perfect.
JUMP_MARGIN = 0.85
ACROSS_MARGIN = 0.7
_HEADER = re.compile(r"^\[(\w+)(\s.*)?\]\s*$")
_ATTR = re.compile(r"(\w+)=(\"[^\"]*\"|\[[^\]]*\]|[^\s\]]+)")
_PROP = re.compile(r"^([\w/]+)\s*=\s*(.+?)\s*$")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:e-?\d+)?")
_EXPORT = re.compile(
    r"^\s*(?:@export(?:_range\([^)]*\))?\s+)?(?:var|const)\s+(\w+)\s*(?::\s*\w+)?\s*:?=\s*(-?[\d.]+)\b",
    re.MULTILINE,
)


def _sections(text: str) -> list[tuple[str, dict[str, str], dict[str, str]]]:
    """``(kind, header attributes, properties)`` for each ``[…]`` block of a .tscn."""
    found: list[tuple[str, dict[str, str], dict[str, str]]] = []
    for line in text.splitlines():
        header = _HEADER.match(line)
        if header:
            attrs = {k: v.strip('"') for k, v in _ATTR.findall(header.group(2) or "")}
            found.append((header.group(1), attrs, {}))
            continue
        prop = _PROP.match(line)
        if prop and found:
            found[-1][2][prop.group(1)] = prop.group(2)
    return found


def _numbers(value: str) -> list[float]:
    return [float(n) for n in _NUMBER.findall(value)]


def _res(project: Path, path: str) -> Path | None:
    if not path.startswith("res://"):
        return None
    target = (project / path.removeprefix("res://")).resolve()
    return target if target.is_relative_to(project.resolve()) else None


def player_scene(project: Path) -> Path | None:
    """The player scene the game spawns into levels, if the project names one."""
    try:
        text = (project / "project.godot").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith(("hooks/player_scene=", "game/hooks/player_scene=")):
            found = _res(project, line.split("=", 1)[1].strip().strip('"'))
            return found if found is not None and found.is_file() else None
    return None


def _gravity(project: Path, three_d: bool) -> float:
    key = "3d/default_gravity=" if three_d else "2d/default_gravity="
    section = ""
    try:
        lines = (project / "project.godot").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if line.startswith("["):
            section = line.strip()
        elif section == "[physics]" and line.startswith(key):
            numbers = _numbers(line.split("=", 1)[1])
            if numbers and numbers[0] > 0:
                return numbers[0]
    return 9.8 if three_d else 980.0


def _shape(kind: str, props: dict[str, str]) -> tuple[float, float] | None:
    """(radius or half-width, height) of a collision shape resource."""
    radius = _numbers(props.get("radius", ""))
    height = _numbers(props.get("height", ""))
    size = _numbers(props.get("size", ""))
    if kind in {"CapsuleShape3D", "CylinderShape3D", "CapsuleShape2D"}:
        r = radius[0] if radius else 0.5
        h = height[0] if height else 2.0 if kind != "CapsuleShape2D" else 30.0
        return r, h
    if kind in {"SphereShape3D", "CircleShape2D"}:
        r = radius[0] if radius else 0.5 if kind == "SphereShape3D" else 10.0
        return r, r * 2
    if kind == "BoxShape3D":
        x, y, z = size if len(size) == 3 else (1.0, 1.0, 1.0)
        return max(x, z) / 2, y
    if kind == "RectangleShape2D":
        x, y = size if len(size) == 2 else (20.0, 20.0)
        return x / 2, y
    return None


def _values(project: Path, script: str, overrides: dict[str, str]) -> dict[str, float]:
    """Numbers the player script sets (exports, vars, consts), as the scene overrides."""
    values: dict[str, float] = {}
    path = _res(project, script)
    if path is not None and path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for name, value in _EXPORT.findall(text):
            try:
                values.setdefault(name, float(value))
            except ValueError:
                continue
    for name, value in overrides.items():
        numbers = _numbers(value)
        if len(numbers) == 1 and _NUMBER.fullmatch(value.strip()):
            values[name] = numbers[0]
    return values


def _first(values: dict[str, float], *names: str) -> float | None:
    for name in names:
        for key in (name, name.upper()):
            if key in values:
                return values[key]
    return None


def measure(project: Path, base: Profile) -> Profile:
    """``base`` with the project's own player's numbers (see the module docstring)."""
    scene = player_scene(project)
    if scene is None:
        return base
    try:
        sections = _sections(scene.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return base
    ext = {
        a.get("id", ""): a.get("path", "")
        for k, a, _ in sections
        if k == "ext_resource"
    }
    subs = {
        a.get("id", ""): (a.get("type", ""), p)
        for k, a, p in sections
        if k == "sub_resource"
    }
    nodes = [(a, p) for k, a, p in sections if k == "node"]
    root = next(((a, p) for a, p in nodes if "parent" not in a), None)
    if root is None or not root[0].get("type", "").startswith("CharacterBody"):
        return base
    three_d = base.mode == "3d"
    changes: dict[str, float] = {}
    for attrs, props in nodes:
        if attrs.get("parent") != "." or not attrs.get("type", "").startswith(
            "CollisionShape"
        ):
            continue
        if props.get("disabled") == "true":
            continue
        ref = re.search(r'SubResource\("([^"]+)"\)', props.get("shape", ""))
        shape = _shape(*subs[ref.group(1)]) if ref and ref.group(1) in subs else None
        if shape is not None:
            changes["radius"], changes["height"] = shape
            break
    script = re.search(r'ExtResource\("([^"]+)"\)', root[1].get("script", ""))
    values = _values(project, ext.get(script.group(1), "") if script else "", root[1])
    angle = _numbers(root[1].get("floor_max_angle", "")) or [math.radians(45)]
    radius = changes.get("radius", base.radius)
    step = _first(values, "step_height", "max_step_height", "step_up")
    if step is None and three_d:
        step = radius * (1 - math.cos(angle[0]))
    if step is not None:
        changes["step"] = (
            min(max(step, 0.0), base.step) if base.step else max(step, 0.0)
        )
    velocity = _first(values, "jump_velocity", "jump_speed", "jump_force")
    if base.jumps and velocity is not None and velocity != 0:
        gravity = _first(values, "rise_gravity", "jump_gravity") or _gravity(
            project, three_d
        )
        fall = _first(values, "fall_gravity") or gravity
        speed = _first(
            values, "walk_speed", "run_speed", "speed", "move_speed", "max_speed"
        )
        v = abs(velocity)
        apex = v * v / (2 * gravity)
        changes["jump"] = min(apex * JUMP_MARGIN, base.jump)
        if speed:
            air = v / gravity + math.sqrt(2 * apex / fall)
            changes["across"] = min(speed * air * ACROSS_MARGIN, base.across)
    if not changes:
        return base
    return replace(
        base,
        radius=changes.get("radius", base.radius),
        height=changes.get("height", base.height),
        step=changes.get("step", base.step),
        jump=changes.get("jump", base.jump),
        across=changes.get("across", base.across),
        measured=True,
    )
