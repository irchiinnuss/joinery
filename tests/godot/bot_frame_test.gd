extends SceneTree
## bot_frame.gd aims the view at what is worth seeing: a visible enemy rather than one
## behind a wall, the open side of a corner rather than a wall, never the ceiling.
## Run headless by tests/test_bot_frame.py with the bot folder as BOT_DIR.

var _failed := false


func _check(ok: bool, what: String) -> void:
	if not ok:
		_failed = true
		push_error("FAILED: " + what)


func _box(parent: Node, at: Vector3, size: Vector3) -> StaticBody3D:
	var body := StaticBody3D.new()
	var shape := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = size
	shape.shape = box
	body.add_child(shape)
	body.position = at
	parent.add_child(body)
	return body


func _enemy(parent: Node, name: String, at: Vector3) -> Node3D:
	var enemy := Node3D.new()
	enemy.name = name
	enemy.position = at
	parent.add_child(enemy)
	enemy.add_to_group(&"enemy")
	return enemy


func _player(parent: Node) -> CharacterBody3D:
	var body := CharacterBody3D.new()
	var shape := CollisionShape3D.new()
	shape.shape = CapsuleShape3D.new()
	body.add_child(shape)
	var head := Node3D.new()
	head.position = Vector3(0, 1.6, 0)
	body.add_child(head)
	var camera := Camera3D.new()
	head.add_child(camera)
	parent.add_child(body)
	camera.current = true
	return body


func _forward() -> Vector3:
	return -get_root().get_viewport().get_camera_3d().global_basis.z


func _play(framer: RefCounted, body: Node3D, frames: int) -> void:
	for frame in frames:
		await physics_frame
		framer.call(&"step", frame, body)


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var script := load(OS.get_environment("BOT_DIR").path_join("bot_frame.gd")) as GDScript
	var world := Node3D.new()
	get_root().add_child(world)
	var body := _player(world)
	# One enemy ahead behind a wall, one to the right in the open.
	_box(world, Vector3(0, 1.5, -5), Vector3(6, 3, 0.4))
	_enemy(world, "Hidden", Vector3(0, 0, -10))
	var seen := _enemy(world, "Seen", Vector3(8, 0, 0))
	await physics_frame
	await physics_frame
	var framer: RefCounted = script.new(self)
	await _play(framer, body, 150)
	var to_seen := (seen.global_position - body.global_position).normalized()
	var forward := _forward()
	_check(
		Vector2(forward.x, forward.z).normalized().dot(Vector2(to_seen.x, to_seen.z)) > 0.95,
		"looks at the enemy it can see (forward %s)" % forward
	)
	_check(forward.y > -0.3 and forward.y < 0.2, "the view stays close to level")
	# Nothing to look at: walls ahead, left and behind; open to the right (+X).
	for node: Node in get_nodes_in_group(&"enemy"):
		node.queue_free()
	body.rotation.y = 0.0
	_box(world, Vector3(0, 1.5, -2), Vector3(4, 3, 0.4))
	_box(world, Vector3(-2, 1.5, 0), Vector3(0.4, 3, 4))
	_box(world, Vector3(0, 1.5, 2), Vector3(4, 3, 0.4))
	await physics_frame
	await physics_frame
	framer = script.new(self)
	await _play(framer, body, 150)
	forward = _forward()
	_check(forward.x > 0.9, "turns to the open side, not a wall (forward %s)" % forward)
	print("bot_frame: failed" if _failed else "bot_frame: ok")
	quit(1 if _failed else 0)
