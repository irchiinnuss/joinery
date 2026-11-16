@tool
extends EditorPlugin
## Accept and Revert in a real editor: a change's new model is imported before the scenes
## that use it load, and the console error feed holds the errors that race brings.

var _failed := false


## Stands in for the chat bridge: what the console error feed reports.
class FakeBridge:
	extends RefCounted
	var reported: Array[String] = []

	func report_errors(_source: String, text: String) -> void:
		reported.append(text)


class FakeTasks:
	extends Node
	var bridge: Object


## Set once the editor's first file scan has ended.
var _layout_ready := false


func _enter_tree() -> void:
	# Connected after the editor's own handler, so this runs once the layout is restored.
	EditorInterface.get_resource_filesystem().sources_changed.connect(
		func(_exist: bool) -> void: _layout_ready = true, CONNECT_ONE_SHOT
	)
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _event(plugin: EditorPlugin, payload: Dictionary, targets: Array = []) -> Dictionary:
	var selection: Array = []
	for id: String in targets:
		selection.append({"id": id})
	return {"payload": payload, "context": plugin._editor_context(selection), "targets": targets}


func _run() -> void:
	var deadline := Time.get_ticks_msec() + 180000
	while not _layout_ready and Time.get_ticks_msec() < deadline:
		await get_tree().process_frame
	_check(_layout_ready, "The editor finishes loading its layout")
	await get_tree().create_timer(1.0).timeout
	var plugin: EditorPlugin
	for node: Node in get_tree().root.find_children("*", "EditorPlugin", true, false):
		if node.get_script() == load("res://addons/joinery/plugin.gd"):
			plugin = node as EditorPlugin
	_check(plugin != null, "Joinery plugin must load")
	if plugin != null:
		await _accepted_instanced_model(plugin)
		plugin._workspace._clear_preview()
		plugin._chat.close()
	await get_tree().process_frame
	await get_tree().process_frame
	RenderingServer.force_draw()
	print("editor accept import: ", "FAILED" if _failed else "ok")
	get_tree().quit(1 if _failed else 0)


## A change adds a model the editor has never seen, a scene that instances it by path (no
## uid) and the open level that uses that scene. Accept must import the model before any of
## those scenes loads: no "missing dependencies" error, and the level reloads with the
## instance. Revert brings the level back without it.
func _accepted_instanced_model(plugin: EditorPlugin) -> void:
	var level := EditorInterface.get_edited_scene_root()
	_check(level != null and level.scene_file_path == "res://main.tscn", "The level is open")
	if level == null:
		return
	var original := FileAccess.get_file_as_string("res://main.tscn")
	var model := Node3D.new()
	var mesh := MeshInstance3D.new()
	mesh.mesh = BoxMesh.new()
	model.add_child(mesh)
	mesh.owner = model
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	_check(document.append_from_scene(model, state) == OK, "Export the prop fixture")
	model.free()
	DirAccess.make_dir_recursive_absolute("res://art/props")
	DirAccess.make_dir_recursive_absolute("res://game/tools")
	_check(
		document.write_to_filesystem(state, "res://art/props/gas_can.glb") == OK,
		"Write the new prop model"
	)
	var tool_scene := FileAccess.open("res://game/tools/gas_can.tscn", FileAccess.WRITE)
	tool_scene.store_string(
		(
			"[gd_scene load_steps=2 format=3]\n\n"
			+ '[ext_resource type="PackedScene" path="res://art/props/gas_can.glb" id="1"]\n\n'
			+ '[node name="GasCan" type="Node3D"]\n\n'
			+ '[node name="Model" parent="." instance=ExtResource("1")]\n'
		)
	)
	tool_scene.close()
	var with_tool := original.replace(
		"[gd_scene format=3]\n",
		(
			"[gd_scene load_steps=2 format=3]\n"
			+ '[ext_resource type="PackedScene" path="res://game/tools/gas_can.tscn" id="1"]\n'
		)
	)
	with_tool += '[node name="GasCan" parent="." instance=ExtResource("1")]\n'
	var level_file := FileAccess.open("res://main.tscn", FileAccess.WRITE)
	level_file.store_string(with_tool)
	level_file.close()
	var changes := [
		{"path": "art/props/gas_can.glb", "change": "created"},
		{"path": "game/tools/gas_can.tscn", "change": "created"},
		{"path": "main.tscn", "change": "modified"},
	]
	var feed := _error_feed()
	plugin._file_review = {"transaction_id": "gas-can"}
	await plugin._on_file_decision(
		{"transaction_id": "gas-can", "accepted": true, "changes": changes}
	)
	_check_no_load_error("Accept")
	await _error_feed_settles(feed)
	_check(
		FileAccess.file_exists("res://art/props/gas_can.glb.import"),
		"The new model is imported on Accept"
	)
	_check(
		EditorInterface.get_open_scenes().has("res://game/tools/gas_can.tscn"),
		"The new scene opens"
	)
	EditorInterface.open_scene_from_path("res://main.tscn")
	level = EditorInterface.get_edited_scene_root()
	var instance := level.get_node_or_null("GasCan") if level != null else null
	_check(
		(
			instance != null
			and instance.scene_file_path == "res://game/tools/gas_can.tscn"
			and not instance.find_children("*", "MeshInstance3D", true, false).is_empty()
		),
		"The level reloads with the new prop in it"
	)
	EditorInterface.open_scene_from_path("res://game/tools/gas_can.tscn")
	EditorInterface.close_scene()
	EditorInterface.open_scene_from_path("res://main.tscn")
	# Revert: the gateway restores the level and removes the new files.
	level_file = FileAccess.open("res://main.tscn", FileAccess.WRITE)
	level_file.store_string(original)
	level_file.close()
	for path: String in [
		"res://game/tools/gas_can.tscn",
		"res://art/props/gas_can.glb",
		"res://art/props/gas_can.glb.import",
	]:
		DirAccess.remove_absolute(path)
	await plugin._on_change_reverted("gas-can")
	_check_no_load_error("Revert")
	level = EditorInterface.get_edited_scene_root()
	_check(
		level != null and level.get_node_or_null("GasCan") == null,
		"Revert reloads the level without the prop"
	)
	plugin._close_review("", "info")
	EditorInterface.set_main_screen_editor("3D")


## The console error feed (sidebar_debug.gd) while the new model waits for its import:
## errors naming it, or its imported copy, are held; an unrelated error goes out at once.
func _error_feed() -> Dictionary:
	var bridge := FakeBridge.new()
	var tasks := FakeTasks.new()
	tasks.bridge = bridge
	var watcher: Node = load("res://addons/joinery/sidebar_debug.gd").new()
	watcher.set("tasks", tasks)
	var imported := (
		"res://.godot/imported/gas_can.glb-%s.scn" % "res://art/props/gas_can.glb".md5_text()
	)
	watcher.call(
		"take_output",
		(
			"ERROR: Failed loading resource: res://art/props/gas_can.glb.\n"
			+ "   at: _load (core/io/resource_loader.cpp:317)\n"
			+ "ERROR: Cannot open file '%s'.\n" % imported
			+ "ERROR: Failed loading resource: res://art/props/never_made.glb.\n"
			+ "ERROR: Cannot open file 'res://.godot/imported/never_made.glb-0.scn'.\n"
		)
	)
	_check(
		bridge.reported.size() == 1 and bridge.reported[0].contains("never_made.glb."),
		"Only the error that is not import noise is reported at once: %s" % [bridge.reported]
	)
	_check(watcher.call("last_error").contains("never_made"), "/debug never picks a held error")
	return {"watcher": watcher, "tasks": tasks, "bridge": bridge, "imported": imported}


## Once the import settles, held errors whose files load are dropped; the rest are reported.
func _error_feed_settles(feed: Dictionary) -> void:
	var settle: GDScript = load("res://addons/joinery/file_settle.gd")
	var deadline := Time.get_ticks_msec() + 30000
	while settle.call("settling") and Time.get_ticks_msec() < deadline:
		await get_tree().process_frame
	await get_tree().create_timer(1.1).timeout
	var watcher: Node = feed["watcher"]
	watcher.call("release_held")
	var reported: Array[String] = (feed["bridge"] as FakeBridge).reported
	var text := "\n".join(reported)
	_check(FileAccess.file_exists(str(feed["imported"])), "The held imported copy now exists")
	_check(not text.contains("gas_can"), "Errors from the import race never reach the agent")
	_check(
		reported.size() == 2 and text.contains("never_made.glb-0.scn"),
		"A held error whose file still does not load is reported: %s" % [reported]
	)
	watcher.free()
	(feed["tasks"] as Node).free()


func _check_no_load_error(step: String) -> void:
	for dialog: Node in get_tree().root.find_children("*", "DependencyErrorDialog", true, false):
		_check(not (dialog as Window).visible, step + ": no missing-dependencies error")
		(dialog as Window).hide()
