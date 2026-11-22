@tool
extends EditorPlugin
## Files a Joinery change wrote never bring up Godot's "Files have been modified outside Godot"
## dialog: an open scene and an open script the change rewrote are reloaded quietly when the
## editor holds no unsaved edits for them, after Accept, an automatic accept, a Revert and an
## Undo revert. A script with unsaved edits is never reloaded behind the user's back.
##
## The editor checks files on disk when its window gets the focus back (EditorNode for scenes,
## ScriptEditor for scripts). The test sends that notification itself.

const TITLE := "Files have been modified outside Godot"
const AUTO_RELOAD := "text_editor/behavior/files/auto_reload_scripts_on_external_change"

var _failed := false
var _layout_ready := false


func _enter_tree() -> void:
	EditorInterface.get_resource_filesystem().sources_changed.connect(
		func(_exist: bool) -> void: _layout_ready = true, CONNECT_ONE_SHOT
	)
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


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
		await _prepare()
		await _accepted(plugin)
		await _auto_accepted(plugin)
		await _reverted(plugin)
		await _unsaved_kept(plugin)
		plugin._chat.close()
	await get_tree().process_frame
	print("editor disk reload: ", "FAILED" if _failed else "ok")
	get_tree().quit(1 if _failed else 0)


func _prepare() -> void:
	# Godot is set to ask before it reloads a script changed outside it: Joinery's own files are
	# still reloaded without asking.
	EditorInterface.get_editor_settings().set_setting(AUTO_RELOAD, false)
	_write("res://game/mover.gd", _script_text(1))
	await _settle()
	EditorInterface.open_scene_from_path("res://main.tscn")
	EditorInterface.edit_script(load("res://game/mover.gd"))
	EditorInterface.set_main_screen_editor("3D")
	await _frames(5)
	_check(_script_open("res://game/mover.gd"), "The script is open in the script editor")


## Accept: the gateway wrote the scene and the script, then the review's decision arrives.
func _accepted(plugin: EditorPlugin) -> void:
	await _land(2)
	plugin._file_review = {"transaction_id": "accept-1"}
	await plugin._on_file_decision(
		{"transaction_id": "accept-1", "accepted": true, "changes": _changes()}
	)
	await _check_quiet("Accept", 2)
	plugin._close_review("", "info")


## A change kept without asking (auto_accept) or applied from the Review tab.
func _auto_accepted(plugin: EditorPlugin) -> void:
	await _land(3)
	await plugin._on_review_row_applied(
		"auto-1", PackedStringArray(["main.tscn", "game/mover.gd"]), _changes()
	)
	await _check_quiet("Automatic accept", 3)


## Revert: the gateway restores the earlier files.
func _reverted(plugin: EditorPlugin) -> void:
	await _land(4)
	await plugin._on_change_reverted("auto-1")
	await _check_quiet("Revert", 4)
	# Undo revert: the change's files are back on disk.
	await _land(5)
	await plugin._on_files_changed("auto-1", PackedStringArray(["main.tscn", "game/mover.gd"]))
	await _check_quiet("Undo revert", 5)


## An open scene and script with unsaved edits are not reloaded: the edits stay, and Godot asks
## about them as it does for any outside edit.
func _unsaved_kept(plugin: EditorPlugin) -> void:
	var editor := _script_editor("res://game/mover.gd")
	_check(editor != null, "The script's editor is found")
	var root := EditorInterface.get_edited_scene_root()
	if editor == null or root == null:
		return
	var code := editor.get_base_editor() as CodeEdit
	code.insert_line_at(0, "# my own note")
	var mine := Node3D.new()
	mine.name = "Mine"
	root.add_child(mine)
	mine.owner = root
	EditorInterface.mark_scene_as_unsaved()
	await _frames(2)
	await _land(6)
	await plugin._on_review_row_applied(
		"auto-2", PackedStringArray(["main.tscn", "game/mover.gd"]), _changes()
	)
	await _frames(5)
	_check(code.text.contains("# my own note"), "Unsaved edits in an open script are kept")
	root = EditorInterface.get_edited_scene_root()
	_check(
		root != null and root.get_node_or_null("Mine") != null,
		"Unsaved edits in an open scene are kept"
	)
	_close_dialogs()


func _changes() -> Array:
	return [
		{"path": "main.tscn", "change": "modified"},
		{"path": "game/mover.gd", "change": "modified"},
	]


## The gateway writes version [param version] of the scene and the script. The editor sees a
## change by its modified time, in whole seconds.
func _land(version: int) -> void:
	await get_tree().create_timer(1.1).timeout
	_write("res://game/mover.gd", _script_text(version))
	_write(
		"res://main.tscn",
		(
			'[gd_scene format=3]\n[node name="Scene" type="Node3D"]\n'
			+ '[node name="Existing%d" type="Node3D" parent="."]\n' % version
		)
	)


## The editor got the focus back: no "modified outside Godot" dialog, and the open scene and
## script show [param version].
func _check_quiet(step: String, version: int) -> void:
	await _frames(3)
	get_tree().root.propagate_notification(NOTIFICATION_APPLICATION_FOCUS_IN)
	await _frames(5)
	var shown := _dialogs()
	_check(shown.is_empty(), step + ": no modified-outside-Godot dialog: %s" % [shown])
	_close_dialogs()
	var root := EditorInterface.get_edited_scene_root()
	_check(
		root != null and root.get_node_or_null("Existing%d" % version) != null,
		step + ": the open scene shows the change"
	)
	var editor := _script_editor("res://game/mover.gd")
	var text := (editor.get_base_editor() as CodeEdit).text if editor != null else ""
	_check(text.contains("VERSION := %d" % version), step + ": the open script shows the change")


func _dialogs() -> Array[String]:
	var shown: Array[String] = []
	var base := EditorInterface.get_base_control()
	for node: Node in base.get_tree().root.find_children("*", "ConfirmationDialog", true, false):
		var dialog := node as ConfirmationDialog
		if dialog.visible and dialog.title == TITLE:
			var tree := dialog.find_children("*", "Tree", true, false)
			var names: Array[String] = []
			if not tree.is_empty() and (tree[0] as Tree).get_root() != null:
				for item: TreeItem in (tree[0] as Tree).get_root().get_children():
					names.append(item.get_text(0))
			shown.append(dialog.get_parent().get_class() + " " + ", ".join(names))
	return shown


func _close_dialogs() -> void:
	for node: Node in get_tree().root.find_children("*", "ConfirmationDialog", true, false):
		var dialog := node as ConfirmationDialog
		if dialog.visible and dialog.title == TITLE:
			dialog.hide()


func _script_open(path: String) -> bool:
	return _script_editor(path) != null


func _script_editor(path: String) -> ScriptEditorBase:
	var scripts := EditorInterface.get_script_editor()
	var open := scripts.get_open_scripts()
	var editors := scripts.get_open_script_editors()
	for index in open.size():
		if open[index].resource_path == path and index < editors.size():
			return editors[index]
	return null


func _script_text(version: int) -> String:
	return "extends Node\n\nconst VERSION := %d\n" % version


func _write(path: String, text: String) -> void:
	DirAccess.make_dir_recursive_absolute(path.get_base_dir())
	var file := FileAccess.open(path, FileAccess.WRITE)
	file.store_string(text)
	file.close()


func _settle() -> void:
	EditorInterface.get_resource_filesystem().scan()
	await _frames(30)


func _frames(count: int) -> void:
	for i in count:
		await get_tree().process_frame
