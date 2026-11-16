extends SceneTree
## File names on the work trail are links: "Read [backyard_blockout.tscn]" opens the scene,
## a script opens in the script editor, a native source in its tool's Assets view, an imported
## asset in the FileSystem dock and the Inspector, and a file only in a change waiting for
## review in the Review panel's preview. Keyboard reachable, named "Open <file>".

const FOLDER := "res://trail_links"
const FILE_OPEN := preload("res://addons/joinery/sidebar_file_open.gd")
const LINK := preload("res://addons/joinery/sidebar_trail_link.gd")

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _plant(name: String, text: String) -> String:
	var path := FOLDER.path_join(name)
	var file := FileAccess.open(path, FileAccess.WRITE)
	file.store_string(text)
	file.close()
	return path


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(FOLDER))
	var scene := _plant(
		"backyard_blockout.tscn", '[gd_scene format=3]\n\n[node name="Yard" type="Node"]\n'
	)
	var script := _plant("level.gd", "extends Node\n")
	var source := _plant("mug.blend", "not really a blend file")
	var texture := _plant("bark.png", "not really a png")
	var opened: Array[String] = []
	FILE_OPEN.recorder = func(how: String, path: String) -> void: opened.append(how + " " + path)

	var chat: Control = load("res://addons/joinery/journeyman_dock.gd").new()
	root.size = Vector2i(480, 900)
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	chat.connect("prompt_submitted", func(_text: String, _targets: Array) -> void: pass)
	chat.set_chat_state("connected", "")
	chat.call("_on_submit", "Fence the backyard")
	chat.set_chat_busy(true)
	chat.call("_on_sent")
	var card: Control = chat.get("_transcript").call("current_card")
	var trail: Node = card.get("_steps")

	# Reading a scene: its name is a link on the live line, and opens the scene.
	chat.add_trail({"id": "r", "text": "Reading backyard_blockout.tscn", "path": scene})
	await process_frame
	var link: Button = trail.call("link")
	_check(link != null and link.text == "backyard_blockout.tscn", "The name is a link")
	_check(link.accessibility_name == "Open backyard_blockout.tscn", link.accessibility_name)
	_check(link.focus_mode == Control.FOCUS_ALL, "Reachable from the keyboard")
	_check(link.tooltip_text == "trail_links/backyard_blockout.tscn", link.tooltip_text)
	_check(
		trail.call("texts") == PackedStringArray(["Reading backyard_blockout.tscn"]),
		"The line reads as before: %s" % [trail.call("texts")]
	)
	link.pressed.emit()
	_check(opened == ["scene " + scene], "A scene opens as the edited scene: %s" % [opened])
	chat.add_trail({"id": "r", "text": "Reading backyard_blockout.tscn", "state": "done"})
	await process_frame
	link = trail.call("link")
	_check(link != null and link.text == "backyard_blockout.tscn", "Done, the name stays a link")

	# An edit: the script editor, with the line's +/− still there.
	(
		chat
		. add_trail(
			{
				"id": "e",
				"text": "Editing level.gd",
				"file": "level.gd",
				"added": 12,
				"removed": 3,
				"path": script,
			}
		)
	)
	await process_frame
	link = trail.call("link")
	link.pressed.emit()
	_check(opened[-1] == "script " + script, "A script opens in the script editor: %s" % [opened])
	var line: Node = trail.call("live_line")
	_check(line.find_child("Size", false, false) != null, "The edit keeps its line count")

	# A native source opens under its tool; an imported asset in the FileSystem and Inspector.
	chat.add_trail({"id": "s", "text": "Reading mug.blend", "path": source})
	await process_frame
	(trail.call("link") as Button).pressed.emit()
	_check(opened[-1] == "source " + source, "A .blend opens in its tool: %s" % [opened])
	chat.add_trail({"id": "t", "text": "Reading bark.png", "path": texture})
	await process_frame
	(trail.call("link") as Button).pressed.emit()
	_check(opened[-1] == "asset " + texture, "A texture is selected and inspected: %s" % [opened])

	# A file that is neither in the project nor edited by the call: no link.
	chat.add_trail({"id": "n", "text": "Reading ghost.gd", "path": FOLDER.path_join("ghost.gd")})
	await process_frame
	_check(trail.call("link") == null, "A missing file is no link")

	# A new file only in the change: it opens in review once the change waits there.
	var staged := FOLDER.path_join("new_lamp.glb")
	chat.add_trail(
		{"id": "w", "text": "Writing new_lamp.glb", "file": "new_lamp.glb", "path": staged}
	)
	await process_frame
	var before := opened.size()
	(trail.call("link") as Button).pressed.emit()
	_check(opened.size() == before, "Before review there is nothing to open yet")
	_check(
		str(chat.get("_transcript").call("caption_text")) == FILE_OPEN.NOT_YET,
		"And the sidebar says when it will"
	)
	chat.set("_review_pending", true)
	(trail.call("link") as Button).pressed.emit()
	_check(opened[-1] == "review " + staged, "In review it opens there: %s" % [opened])
	chat.set("_review_pending", false)

	# The opened record links every named file too.
	trail.call("show_all", true)
	await process_frame
	var named := 0
	for words: Node in trail.find_children("Words", "Label", true, false):
		if LINK.link_of(words as Label) != null:
			named += 1
	_check(named >= 5, "Each line naming a file links it when opened: %d" % named)

	chat.set_chat_busy(false)
	FILE_OPEN.recorder = Callable()
	chat.queue_free()
	await process_frame
	for name: String in DirAccess.get_files_at(ProjectSettings.globalize_path(FOLDER)):
		DirAccess.remove_absolute(ProjectSettings.globalize_path(FOLDER).path_join(name))
	print("trail links: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
