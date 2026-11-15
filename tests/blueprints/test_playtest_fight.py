"""The playtest bot fights its way through a combat level (bot_fight.gd): the first-person-3d
blueprint with a blockout of three rooms, a guard standing in the first doorway, a key in the
middle room, a barred door it opens and the exit beyond. The bot can only finish by shooting
the guard, taking the key before the door, and walking out; and the same seed plays the
same run again."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from joinery import blockout, playtest
from tests.blueprints.godot_run import import_project, make_project, needs_godot

LAYOUT = """
version = 1
name = "arena"
mode = "3d"
door_width = 3
door_height = 2.6

[spawn]
at = [3, 4]
facing = "east"

[[rooms]]
id = "start"
rect = [0, 0, 10, 8]

[[rooms]]
id = "hall"
rect = [10, 0, 12, 8]

[[rooms]]
id = "vault"
rect = [22, 0, 8, 8]

[[openings]]
room = "start"
wall = "east"
at = 4

[[openings]]
room = "hall"
wall = "east"
at = 4

[[markers]]
kind = "enemy"
id = "Guard"
at = [10, 4]

[[markers]]
kind = "key"
id = "VaultKey"
at = [18, 1.5]

[[markers]]
kind = "door"
id = "VaultDoor"
at = [22, 4]
key = "VaultKey"
size = 3

[[markers]]
kind = "exit"
id = "Exit"
at = [27, 4]
"""

#: The game side a combat game has and the blueprint doesn't: an `attack` action and a gun
#: that hurts what it hits, a guard at each enemy marker and bars in each door that open.
RIG = """extends Node

var _cooldown: float = 0.0


func _ready() -> void:
	if not InputMap.has_action(&"attack"):
		InputMap.add_action(&"attack")
		var key := InputEventKey.new()
		key.keycode = KEY_X
		InputMap.action_add_event(&"attack", key)


func _physics_process(delta: float) -> void:
	_cooldown = maxf(_cooldown - delta, 0.0)
	for marker: Node in get_tree().get_nodes_in_group(&"enemy"):
		if marker is Marker3D and not marker.has_meta(&"rigged"):
			marker.set_meta(&"rigged", true)
			var guard: Node = load("res://game/test_guard.gd").new()
			guard.name = "Body"
			marker.add_child(guard)
	for door: Node in get_tree().get_nodes_in_group(&"door"):
		if not door.has_meta(&"rigged"):
			door.set_meta(&"rigged", true)
			door.add_child(load("res://game/test_bars.gd").new())
	if Input.is_action_pressed(&"attack") and _cooldown <= 0.0:
		_cooldown = 0.25
		_shoot()


func _shoot() -> void:
	var camera := get_viewport().get_camera_3d()
	if camera == null:
		return
	var from := camera.global_position
	var query := PhysicsRayQueryParameters3D.create(from, from - camera.global_basis.z * 60.0)
	for player: Node in get_tree().get_nodes_in_group(&"player"):
		if player is CollisionObject3D:
			query.exclude = [(player as CollisionObject3D).get_rid()]
	var hit := camera.get_world_3d().direct_space_state.intersect_ray(query)
	var node: Node = hit.get("collider") as Node
	while node != null:
		if node.has_method(&"take_damage"):
			node.call(&"take_damage", 10.0)
			return
		node = node.get_parent()
"""

GUARD = """extends CharacterBody3D
## Stands in the doorway, filling it, until shot down.

var health: float = 30.0
var dead: bool = false


func _ready() -> void:
	add_to_group(&"enemy")
	var shape := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = Vector3(0.8, 2.4, 3.4)
	shape.shape = box
	shape.position = Vector3(0, 1.2, 0)
	add_child(shape)


func take_damage(amount: float) -> void:
	if dead:
		return
	health -= amount
	if health <= 0.0:
		dead = true
		hide()
		collision_layer = 0
"""

BARS = """extends StaticBody3D
## Bars across the doorway; the level opens them once the key is held.

var is_open: bool = false


func _ready() -> void:
	var shape := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = Vector3(0.3, 3.0, 3.4)
	shape.shape = box
	shape.position = Vector3(0, 1.5, 0)
	add_child(shape)


func open() -> void:
	is_open = true
	collision_layer = 0
	hide()
"""


def _arena(tmp_path: Path) -> tuple[Path, str]:
    project = make_project(tmp_path, "first-person-3d", [])
    built = blockout.make(project, LAYOUT)
    assert built.ok, [p.text for p in built.checked.problems]
    for relative, data in built.files.items():
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    game = project / "game"
    (game / "test_rig.gd").write_text(RIG)
    (game / "test_guard.gd").write_text(GUARD)
    (game / "test_bars.gd").write_text(BARS)
    settings = project / "project.godot"
    text = settings.read_text()
    rig = 'TestRig="*res://game/test_rig.gd"\n'
    if "[autoload]\n" in text:
        text = text.replace("[autoload]\n", "[autoload]\n\n" + rig, 1)
    else:
        text += "\n[autoload]\n\n" + rig
    settings.write_text(text)
    import_project(project)
    return project, built.scene


def _play(project: Path, scene: str, out: Path) -> dict[str, Any]:
    report = playtest.run(
        playtest.Options(project=project, seconds=45, seed=4242, scene=scene, out=out)
    )
    assert report["status"] == "played", report
    return report


@needs_godot
def test_the_bot_shoots_the_guard_takes_the_key_and_walks_out(tmp_path: Path) -> None:
    project, scene = _arena(tmp_path)
    report = _play(project, scene, tmp_path / "first")
    fight = report["fight"]
    assert fight["active"], fight
    assert fight["kills"] == 1 and fight["enemies"] == 1, fight
    assert fight["keys"] == ["VaultKey"], fight
    assert "VaultDoor" in fight["doors"], fight
    assert fight["finished"] and fight["reached_exit"], fight
    order = [g["kind"] for g in fight["goals"]]
    last_exit = max(i for i, kind in enumerate(order) if kind == "exit")
    assert order.index("key") < order.index("door") < last_exit, order
    bugs = [i for i in report["items"] if "Joinery" in i["title"]]
    assert bugs == [], bugs
    # The same seed plays the same run: a replay of it stays valid.
    again = _play(project, scene, tmp_path / "second")["fight"]
    assert again["trail"] == fight["trail"], (again["trail"], fight["trail"])
    assert again["finished_t"] == fight["finished_t"]
