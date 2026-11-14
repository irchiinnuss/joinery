"""Blockouts that agree with the game: markers for what the level holds, rebuilds that
keep what was placed, the real player's reach, and a walk that says what happened."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from joinery import blockout
from joinery.blockout import command
from joinery.blockout.merge import merge
from joinery.blockout.player import measure
from joinery.blockout.world import profile_for

LAYOUT = """name = "test"
mode = "3d"

[spawn]
at = [2, 3]

[[rooms]]
id = "a"
rect = [0, 0, 6, 6]
open = ["east"]

[[rooms]]
id = "b"
rect = [6, 0, 6, 6]
open = ["west"]
"""
MARKERS = """
[[markers]]
kind = "exit"
id = "way_out"
at = [10, 3]

[[markers]]
kind = "key"
id = "red"
at = [4, 1]

[[markers]]
kind = "door"
id = "gate"
at = [6, 3]
key = "red"

[[markers]]
kind = "enemy"
id = "grunt"
at = [9, 1]

[[markers]]
kind = "pickup"
id = "medkit"
at = [9, 5]
"""

PLAYER = """[gd_scene format=3]

[ext_resource type="Script" path="res://game/player/player.gd" id="1_p"]

[sub_resource type="CapsuleShape3D" id="Body"]
radius = {radius}
height = 1.6

[node name="Player" type="CharacterBody3D" groups=["player", "saveable"]]
script = ExtResource("1_p")
{extra}
[node name="Shape" type="CollisionShape3D" parent="."]
shape = SubResource("Body")
"""


def _project(
    tmp_path: Path, radius: float = 0.4, jump: float = 7.5, extra: str = ""
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "project.godot").write_text(
        '[game]\n\nhooks/player_scene="res://game/player/player.tscn"\n'
    )
    player = tmp_path / "game/player"
    player.mkdir(parents=True)
    (player / "player.tscn").write_text(PLAYER.format(radius=radius, extra=extra))
    (player / "player.gd").write_text(
        "extends CharacterBody3D\n@export var walk_speed: float = 6.0\n"
        f"@export var jump_velocity: float = {jump}\n"
    )
    return tmp_path


# --- Markers --------------------------------------------------------------------------


def test_markers_for_exit_keys_doors_and_enemies_build_into_the_scene() -> None:
    built = blockout.make(Path("/nonexistent"), LAYOUT + MARKERS)
    assert built.ok, [p.text for p in built.checked.problems]
    scene = built.files["game/test_blockout.tscn"].decode()
    assert (
        '[node name="way_out" type="Area3D" parent="Markers" groups=["exit", "goal"]]'
        in scene
    )
    assert (
        '[node name="red" type="Area3D" parent="Markers" groups=["key", "pickup"]]'
        in scene
    )
    assert 'metadata/key = "red"' in scene
    assert (
        '[node name="grunt" type="Marker3D" parent="Markers" groups=["enemy"]]' in scene
    )
    reach = built.checked.reach
    assert reach is not None
    assert {t.id for t in reach.targets} == {"way_out", "red", "gate", "medkit"}
    assert blockout.parse(blockout.to_toml(built.layout)) == built.layout


def test_a_door_must_name_a_key_that_is_there() -> None:
    bad = LAYOUT + MARKERS.replace('key = "red"', 'key = "blue"')
    errors = [p.text for p in blockout.make(Path("/x"), bad).checked.errors]
    assert any("no key marker" in e for e in errors)
    odd = LAYOUT + '\n[[markers]]\nkind = "coin"\nat = [3, 3]\nkey = "red"\n'
    errors = [p.text for p in blockout.make(Path("/x"), odd).checked.errors]
    assert any("only a door names a key" in e for e in errors)


# Three rooms in a row; locked doors in the doorways between them.
DOORS = """name = "keys"
mode = "3d"

[spawn]
at = [2, 3]

[[rooms]]
id = "a"
rect = [0, 0, 6, 6]

[[rooms]]
id = "b"
rect = [6, 0, 6, 6]

[[rooms]]
id = "c"
rect = [12, 0, 6, 6]

[[openings]]
room = "a"
wall = "east"

[[openings]]
room = "b"
wall = "east"

[[markers]]
kind = "door"
id = "gate"
at = [6, 3]
key = "red"

[[markers]]
kind = "door"
id = "vault"
at = [12, 3]
key = "blue"

[[markers]]
kind = "exit"
id = "way_out"
at = [16, 3]
"""


def _keys(red: tuple[int, int], blue: tuple[int, int]) -> str:
    return "".join(
        f'\n[[markers]]\nkind = "key"\nid = "{name}"\nat = [{x}, {y}]\n'
        for name, (x, y) in (("red", red), ("blue", blue))
    )


def test_keys_open_their_doors_in_turn() -> None:
    # red is by the spawn and opens gate; behind it lies blue, which opens vault.
    built = blockout.make(Path("/x"), DOORS + _keys((2, 1), (9, 1)))
    assert built.ok, [p.text for p in built.checked.problems]
    reach = built.checked.reach
    assert reach is not None and all(t.reached for t in reach.targets)
    assert reach.locks == []


def test_a_key_behind_its_own_door_is_reported_with_what_it_locks_away() -> None:
    built = blockout.make(Path("/x"), DOORS + _keys((9, 1), (9, 5)))
    reach = built.checked.reach
    assert reach is not None
    reached = {t.id: t for t in reach.targets}
    assert not reached["red"].reached and not reached["way_out"].reached
    assert reached["red"].reason.startswith(
        "key red is locked behind door gate, the door it opens"
    )
    assert "only reachable through locked door gate, whose key red is locked " in (
        reached["blue"].reason
    )
    assert reach.locks[0] == (
        "Room b is only reachable through locked door gate, whose key red is locked "
        "behind that same door."
    )
    errors = [p.text for p in built.checked.errors]
    assert reached["red"].reason in errors and reach.locks[0] in errors


def test_an_area_behind_a_door_whose_key_is_out_of_reach_is_reported() -> None:
    # blue lies in room c, behind vault, the door it opens: room c is shut away.
    built = blockout.make(Path("/x"), DOORS + _keys((2, 1), (16, 1)))
    reach = built.checked.reach
    assert reach is not None
    reached = {t.id: t.reached for t in reach.targets}
    assert reached == {
        "gate": True,
        "vault": True,
        "way_out": False,
        "red": True,
        "blue": False,
    }
    assert reach.locks == [
        (
            "Room c is only reachable through locked door vault, whose key blue is "
            "locked behind that same door."
        )
    ]
    # Without the doors' keys the same layout is walkable end to end.
    open_doors = DOORS.replace('key = "red"\n', "").replace('key = "blue"\n', "")
    walked = blockout.make(Path("/x"), open_doors + _keys((2, 1), (16, 1)))
    assert walked.ok, [p.text for p in walked.checked.problems]


SIDE_DOOR = """name = "hall"
mode = "2d-side"
units = "tiles"

[spawn]
at = [3, 10]

[[rooms]]
id = "hall"
rect = [0, 0, 40, 12]

[[markers]]
kind = "door"
id = "gate"
at = [20, 11]
key = "red"

[[markers]]
kind = "goal"
at = [36, 11]

[[markers]]
kind = "key"
id = "red"
at = [{key}, 11]
"""


def test_a_side_view_door_shuts_its_doorway_until_its_key_is_got() -> None:
    before = blockout.make(Path("/x"), SIDE_DOOR.format(key=8))
    assert before.ok, [p.text for p in before.checked.problems]
    behind = blockout.make(Path("/x"), SIDE_DOOR.format(key=30))
    reach = behind.checked.reach
    assert reach is not None
    # The door itself is walked up to; what lies past it is not.
    assert {t.id: t.reached for t in reach.targets} == {
        "gate": True,
        "goal_1": False,
        "red": False,
    }
    errors = [p.text for p in behind.checked.errors]
    assert any(e.startswith("key red is locked behind door gate") for e in errors)


# --- Rebuilds keep what was placed ----------------------------------------------------


def _write(root: Path, built: blockout.Built) -> None:
    files, _ = blockout.merged_files(root, built)
    for relative, data in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(data)


def _place(scene: Path) -> None:
    text = scene.read_text()
    text = text.replace(
        "[sub_resource",
        '[ext_resource type="PackedScene" path="res://enemies/grunt.tscn" id="9_grunt"]\n\n'
        '[ext_resource type="Material" path="res://art/materials/brick.tres" id="9_brick"]\n\n'
        "[sub_resource",
        1,
    )
    floor = '[node name="Floor" type="CSGBox3D" parent="Rooms/a"]'
    start = text.index(floor)
    end = text.index("\n\n", start)
    block = "\n".join(
        'material = ExtResource("9_brick")' if line.startswith("material = ") else line
        for line in text[start:end].splitlines()
    )
    text = text[:start] + block + text[end:]
    text += (
        '\n[node name="Grunt" parent="Markers/grunt" instance=ExtResource("9_grunt")]\n'
        '\n[node name="Medkit" type="MeshInstance3D" parent="Markers/medkit"]\n'
    )
    scene.write_text(text)


def test_a_rebuild_keeps_placed_enemies_pickups_and_materials(tmp_path: Path) -> None:
    first = blockout.make(tmp_path, LAYOUT + MARKERS)
    _write(tmp_path, first)
    scene = tmp_path / "game/test_blockout.tscn"
    _place(scene)
    # The room grows; the medkit marker goes away.
    changed = LAYOUT.replace(
        "rect = [6, 0, 6, 6]", "rect = [6, 0, 8, 6]"
    ) + MARKERS.replace(
        '[[markers]]\nkind = "pickup"\nid = "medkit"\nat = [9, 5]\n', ""
    )
    second = blockout.make(tmp_path, changed)
    files, notes = blockout.merged_files(tmp_path, second)
    text = files["game/test_blockout.tscn"].decode()
    assert (
        '[node name="Grunt" parent="Markers/grunt" instance=ExtResource("9_grunt")]'
        in text
    )
    assert 'path="res://enemies/grunt.tscn"' in text
    floor = text[text.index('[node name="Floor" type="CSGBox3D" parent="Rooms/a"]') :]
    assert floor.split("\n\n")[0].count('material = ExtResource("9_brick")') == 1
    # Rebuilt from the new layout: room b is wider.
    assert "size = Vector3(8, 0.2, 6)" in text
    # The medkit marker is gone, so what was placed under it moves up, and says so.
    assert '[node name="Medkit" type="MeshInstance3D" parent="Markers"]' in text
    assert any("Markers/medkit" in n for n in notes)
    assert any(n.startswith("Kept 2 placed nodes") for n in notes)
    # Writing it and rebuilding again changes nothing.
    scene.write_bytes(files["game/test_blockout.tscn"])
    (tmp_path / second.layout_file).write_bytes(second.files[second.layout_file])
    again, _ = blockout.merged_files(tmp_path, blockout.make(tmp_path, changed))
    assert again["game/test_blockout.tscn"].decode() == text


def test_without_the_old_layout_placed_and_scripted_nodes_still_survive() -> None:
    new = (
        '[gd_scene load_steps=2 format=3]\n\n[sub_resource type="StandardMaterial3D" '
        'id="StandardMaterial3D_1"]\nalbedo_color = Color(1, 1, 1, 1)\n\n'
        '[node name="Blockout" type="Node3D"]\n\n'
        '[node name="Floor" type="CSGBox3D" parent="."]\n'
        'material = SubResource("StandardMaterial3D_1")\n'
    )
    old = (
        '[gd_scene load_steps=3 format=3 uid="uid://abc"]\n\n'
        '[ext_resource type="Script" path="res://door.gd" id="1_door"]\n\n'
        '[sub_resource type="StandardMaterial3D" id="StandardMaterial3D_1"]\n'
        "albedo_color = Color(0.2, 0.1, 0.1, 1)\n\n"
        '[node name="Blockout" type="Node3D"]\n\n'
        '[node name="Floor" type="CSGBox3D" parent="."]\n'
        'material = SubResource("StandardMaterial3D_1")\n\n'
        '[node name="Door" type="Node3D" parent="."]\nscript = ExtResource("1_door")\n\n'
        '[node name="Old" type="CSGBox3D" parent="."]\n\n'
        '[connection signal="ready" from="Door" to="." method="x"]\n'
    )
    merged = merge(old, new)
    assert 'uid="uid://abc"' in merged.text.splitlines()[0]
    assert '[node name="Door" type="Node3D" parent="."]' in merged.text
    assert '[node name="Old"' not in merged.text  # built before, gone from the layout
    # The edited colour is kept under its own id; the rebuilt one stays.
    assert 'id="kept_StandardMaterial3D_1"' in merged.text
    assert 'material = SubResource("kept_StandardMaterial3D_1")' in merged.text
    assert '[connection signal="ready" from="Door"' in merged.text


# --- The real player ------------------------------------------------------------------


def test_the_check_uses_the_games_own_player(tmp_path: Path) -> None:
    base = profile_for("third-person-3d", "3d")
    measured = measure(_project(tmp_path, radius=0.5, jump=4.0), base)
    assert measured.measured and measured.radius == 0.5 and measured.height == 1.6
    # A capsule rides over an edge only as high as its rounded bottom meets it.
    assert measured.step == pytest.approx(0.5 * (1 - 0.7071), abs=1e-3)
    # A weaker jump than the starting point's: the check promises less.
    assert measured.jump == pytest.approx(16 / 19.6 * 0.85, abs=1e-3)
    assert measured.jump < base.jump
    # A stronger one never promises more than the starting point's tuned numbers.
    strong = measure(_project(tmp_path / "s", jump=20.0), base)
    assert strong.jump == base.jump and strong.across == base.across
    exported = measure(_project(tmp_path / "e", extra="step_height = 0.05\n"), base)
    assert exported.step == pytest.approx(0.05)


def test_a_ledge_the_real_player_cant_step_up_is_an_error(tmp_path: Path) -> None:
    ledge = LAYOUT.replace('open = ["west"]', 'open = ["west"]\nfloor = 0.25') + (
        '\n[[markers]]\nkind = "goal"\nat = [10, 3]\n'
    )
    project = _project(tmp_path, jump=0.5)
    # Asked to check for another starting point's player: that one's profile.
    other = blockout.make(project, ledge, blueprint="first-person-3d")
    assert not other.profile.measured
    own = blockout.make(project, ledge)
    assert own.profile.measured
    assert not own.ok and any("can't be reached" in p.text for p in own.checked.errors)


# --- The walk -------------------------------------------------------------------------


def _args(project: Path, layout: Path) -> argparse.Namespace:
    return argparse.Namespace(
        action="walk",
        layout=layout,
        project=project,
        scene="",
        blueprint="",
        picture=None,
        json=True,
    )


def test_walk_runs_the_layout_as_it_builds_now_and_returns_the_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    layout = project / "level.toml"
    layout.write_text(LAYOUT + MARKERS)
    seen: dict[str, Any] = {}

    def run(options: Any) -> dict[str, Any]:
        scene = project / options.scene.removeprefix("res://")
        seen["scene"] = scene.read_text()
        seen["script"] = (scene.parent / "blockout_level.gd").is_file()
        route = json.loads(Path(options.route).read_text())
        return {
            "status": "played",
            "route": [
                {"id": t["id"], "kind": t["kind"], "reached": True}
                for t in route["targets"]
            ],
        }

    monkeypatch.setattr(command.playtest, "godot_binary", lambda: Path("/godot"))
    monkeypatch.setattr(command.playtest, "run", run)
    assert command.main(_args(project, layout)) == 0
    data = json.loads(capsys.readouterr().out)["walk"]
    assert {r["id"] for r in data["route"]} == {"way_out", "red", "gate", "medkit"}
    assert data["planned"][0]["waypoints"]
    assert "reason" not in data
    # Built fresh into a scratch folder, never into the game, and cleaned up after.
    assert 'path="res://.joinery/walk/' in seen["scene"] and seen["script"]
    assert not (project / "game/test_blockout.tscn").exists()
    assert not (project / ".joinery/walk").exists()


def test_walk_says_why_when_it_cannot_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = tmp_path / "level.toml"
    layout.write_text(LAYOUT + MARKERS)
    (tmp_path / "project.godot").write_text("[application]\n")
    assert command.main(_args(tmp_path, layout)) == 1
    assert "spawns no player" in json.loads(capsys.readouterr().out)["walk"]["reason"]
    project = _project(tmp_path / "p")
    (project / "level.toml").write_text(LAYOUT + MARKERS)

    def missing() -> Path:
        raise ValueError("no Godot here")

    monkeypatch.setattr(command.playtest, "godot_binary", missing)
    assert command.main(_args(project, project / "level.toml")) == 1
    assert "no Godot here" in json.loads(capsys.readouterr().out)["walk"]["reason"]
    monkeypatch.setattr(command.playtest, "godot_binary", lambda: Path("/godot"))
    report = {
        "status": "crashed",
        "route": [],
        "items": [
            {
                "severity": "critical",
                "title": "Boot",
                "detail": "The scene did not load.",
            }
        ],
    }
    monkeypatch.setattr(command.playtest, "run", lambda _o: report)
    assert command.main(_args(project, project / "level.toml")) == 1
    reason = json.loads(capsys.readouterr().out)["walk"]["reason"]
    assert "never started" in reason and "did not load" in reason


def test_merged_files_reads_the_layout_the_scene_names(tmp_path: Path) -> None:
    built = blockout.make(tmp_path, LAYOUT + MARKERS)
    text = built.files[built.layout_file]
    assert (
        f'metadata/blockout_sha256 = "{hashlib.sha256(text).hexdigest()}"'
        in built.files["game/test_blockout.tscn"].decode()
    )
