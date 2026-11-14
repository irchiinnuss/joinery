"""The playtest bot's --reach: exact node names (or a glob), and no script error of its
own when a level reload frees the nodes it was heading for."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from joinery import done_when, playtest, playtest_report

ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "joinery" / "playtest_bot" / "bot_drive.gd"


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

CHECK = """extends SceneTree

var drive: RefCounted


func _level(names: Array[String]) -> Node3D:
	var level := Node3D.new()
	level.name = "Level"
	for word: String in names:
		var node := Node3D.new()
		node.name = word
		level.add_child(node)
	root.add_child(level)
	current_scene = level
	return level


func _names(word: String) -> Array[String]:
	var found: Array[String] = []
	for item: Variant in (drive.get("_reach_nodes") as Dictionary).get(word, []):
		if is_instance_valid(item):
			found.append(String((item as Node).name))
	return found


func _initialize() -> void:
	var script: GDScript = load("DRIVE")
	drive = script.new(self, RandomNumberGenerator.new())
	drive.set("reach", PackedStringArray(["redkeycard", "bluekey*"]))
	var first := _level(["RedKeycard", "RedKeycardDoor", "BlueKeycard2"])
	drive.call("_check_reach", 0)
	print("EXACT ", _names("redkeycard"))
	print("GLOB ", _names("bluekey*"))
	# The level reloads: the old nodes are freed, a new scene takes their place.
	root.remove_child(first)
	first.free()
	_level(["RedKeycard"])
	drive.call("_check_reach", 1)
	print("RELOADED ", _names("redkeycard"))
	# A node is freed in the scene the bot is in, between its looks.
	(current_scene.get_node("RedKeycard") as Node).free()
	drive.call("_check_reach", 2)
	print("FREED ", _names("redkeycard"))
	print("reach: ok")
	quit()
"""


@needs_godot
def test_reach_is_exact_and_survives_a_level_reload(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "check.gd").write_text(CHECK.replace("DRIVE", str(DRIVE)))
    result = subprocess.run(
        [_godot(), "--headless", "--path", str(tmp_path), "--script", "check.gd"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, "JOINERY_BACKGROUND": "1"},
    )
    output = result.stdout + result.stderr
    assert "reach: ok" in output, output
    assert 'EXACT ["RedKeycard"]' in output, output
    assert 'GLOB ["BlueKeycard2"]' in output, output
    assert 'RELOADED ["RedKeycard"]' in output, output
    assert "FREED []" in output, output
    assert "SCRIPT ERROR" not in output and "previously freed" not in output, output


BOT_ERROR = """SCRIPT ERROR: Trying to assign invalid previously freed instance.
          at: _check_reach (/Users/me/joinery/joinery/playtest_bot/bot_drive.gd:300)
          GDScript backtrace (most recent call first):
              [0] _check_reach (/Users/me/joinery/joinery/playtest_bot/bot_drive.gd:300)
"""


def test_an_error_in_the_bot_is_a_joinery_bug_with_its_place() -> None:
    bot = {
        "finished": True,
        "seconds_played": 60.0,
        "fps": 60,
        "log": [
            {
                "kind": "script_error",
                "message": "Trying to assign invalid previously freed instance.",
                "where": "/Users/me/joinery/joinery/playtest_bot/bot_drive.gd:300",
                "frame": 120,
            },
            {
                "kind": "error",
                "message": "Joinery panel failed",
                "where": "res://addons/joinery/dock.gd:12",
                "frame": 130,
            },
        ],
    }
    report = playtest_report.build(
        bot, BOT_ERROR, 0, options={"seed": 1, "seconds": 60.0, "project": "P"}
    )
    items = report["items"]
    assert [i["kind"] for i in items] == ["joinery", "joinery"], items
    first = next(i for i in items if "freed" in i["detail"])
    assert first["title"] == "Joinery bug"
    assert first["severity"] == "low"
    assert first["where"] == "joinery/playtest_bot/bot_drive.gd:300"
    assert "not your game" in first["detail"]
    other = next(i for i in items if "panel" in i["detail"])
    assert other["where"] == "res://addons/joinery/dock.gd:12"
    criterion = done_when.Criterion("d1", "No errors", "no_errors", seconds=60)
    result = done_when._played(criterion, report, Path("."), "")
    assert result.status == "pass", result.evidence


def test_reach_words_keep_a_glob() -> None:
    assert playtest.reach_words(["Red keycard", "Red keycard*", "door_?"]) == [
        "redkeycard",
        "redkeycard*",
        "door?",
    ]
