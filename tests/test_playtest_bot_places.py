"""The playtest bot finds a room by what it holds, not by its origin, and its save
round-trip doesn't call a turning enemy a broken save."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "joinery" / "playtest_bot"


def _godot() -> str:
    for candidate in (
        os.environ.get("JOINERY_TEST_GODOT", ""),
        os.environ.get("JOINERY_GODOT_BIN", ""),
        str(ROOT / ".tools/Godot.app/Contents/MacOS/Godot"),
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return ""


needs_godot = pytest.mark.skipif(not _godot(), reason="needs a Godot 4.7 binary")


def _run(tmp_path: Path, script: str) -> str:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "check.gd").write_text(script.replace("BOT", str(BOT)))
    result = subprocess.run(
        [_godot(), "--headless", "--path", str(tmp_path), "--script", "check.gd"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, "JOINERY_BACKGROUND": "1"},
    )
    output = result.stdout + result.stderr
    assert "SCRIPT ERROR" not in output and "Parse Error" not in output, output
    return output


ROOM = """extends SceneTree


# Inside the tree only once the main loop runs.
func _process(_delta: float) -> bool:
	var drive: RefCounted = (load("BOT/bot_drive.gd") as GDScript).new(
		self, RandomNumberGenerator.new()
	)
	var level := Node3D.new()
	level.name = "Level"
	root.add_child(level)
	current_scene = level
	# A grouping room at the origin; its floor is far away.
	var room := Node3D.new()
	room.name = "ShiftOffice"
	level.add_child(room)
	var floor_mesh := MeshInstance3D.new()
	var box := BoxMesh.new()
	box.size = Vector3(10, 0.2, 8)
	floor_mesh.mesh = box
	room.add_child(floor_mesh)
	floor_mesh.global_position = Vector3(166, 0, 82)
	# A leaf marker with nothing inside: its origin is its place.
	var spot := Node3D.new()
	spot.name = "Exit"
	level.add_child(spot)
	spot.global_position = Vector3(1.5, 0, 0)
	var player := CharacterBody3D.new()
	player.name = "Player"
	level.add_child(player)
	drive.call("follow", player)
	drive.set("reach", PackedStringArray(["shiftoffice", "exit"]))
	drive.call("_check_reach", 0)
	for entry: Dictionary in drive.get("reach_results"):
		print("AT_ORIGIN %s %s %s" % [entry["target"], entry["reached"], entry["closest"]])
	print("AIM ", drive.call("_aim", room))
	player.global_position = Vector3(165, 1, 80)
	drive.call("_check_reach", 1)
	for entry: Dictionary in drive.get("reach_results"):
		print("IN_ROOM %s %s" % [entry["target"], entry["reached"]])
	print("places: ok")
	return true
"""


@needs_godot
def test_a_room_at_the_origin_is_reached_by_its_contents(tmp_path: Path) -> None:
    output = _run(tmp_path, ROOM)
    assert "places: ok" in output, output
    # The player starts at the origin, 160 m from the room's floor: not reached.
    line = next(x for x in output.splitlines() if x.startswith("AT_ORIGIN shiftoffice"))
    assert line.split()[2] == "false", output
    assert float(line.split()[3]) > 150, output
    # The leaf node has nothing inside: its origin, 1.5 m away, is within reach.
    assert "AT_ORIGIN exit true" in output, output
    assert "AIM (166.0, 0.0, 82.0)" in output, output
    assert "IN_ROOM shiftoffice true" in output, output


SAVE = """extends SceneTree


func _initialize() -> void:
	var bot: GDScript = load("BOT/bot.gd")
	# A Drudge facing just past +pi comes back facing just short of -pi: the same way.
	print("WRAP ", bot.call("_same", {"yaw": 3.154}, {"yaw": -3.046}))
	print("WRAP_VEC ", bot.call("_same", {"rotation": Vector3(0, 3.1, 0)}, {"rotation": Vector3(0, -3.1, 0)}))
	print("WRAP_DEG ", bot.call("_same", {"rotation_degrees": 179.0}, {"rotation_degrees": -179.0}))
	print("TURNED ", bot.call("_same", {"yaw": 0.0}, {"yaw": 2.0}))
	print("NOT_ANGLE ", bot.call("_same", {"x": 3.154}, {"x": -3.046}))
	# A Drudge walking on after the load: its position is live, its health isn't.
	var saved := {"position": Vector3(1, 0, 1), "health": 40}
	var loaded := {"position": Vector3(3, 0, 1), "health": 40}
	var later := {"position": Vector3(3.4, 0, 1), "health": 40}
	print("MOVING ", bot.call("_same_settled", saved, loaded, later))
	var hurt := {"position": Vector3(3, 0, 1), "health": 100}
	var hurt_later := {"position": Vector3(3.4, 0, 1), "health": 100}
	print("HEALTH ", bot.call("_same_settled", saved, hurt, hurt_later))
	# Standing still: held to the saved place.
	var still := {"position": Vector3(3, 0, 1), "health": 40}
	print("STILL ", bot.call("_same_settled", saved, still, still))
	print("save: ok")
	quit()
"""


@needs_godot
def test_save_compare_wraps_angles_and_lets_live_values_move(tmp_path: Path) -> None:
    output = _run(tmp_path, SAVE)
    assert "save: ok" in output, output
    for line in ("WRAP true", "WRAP_VEC true", "WRAP_DEG true", "MOVING true"):
        assert line in output, output
    for line in ("TURNED false", "NOT_ANGLE false", "HEALTH false", "STILL false"):
        assert line in output, output
