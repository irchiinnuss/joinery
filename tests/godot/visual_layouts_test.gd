@tool
extends EditorPlugin
## The editor's layouts for the visual suite: at its smallest window (1024×600), 1280×720,
## 1366×768 and 1920×1080 (logical pixels, so the editor's scale is applied), every combination
## of the Scene and Inspector column, the Files column, the Journeyman sidebar (hidden, at its
## own width, or widened by dragging its edge), the Review panel in the bottom panel, with the
## sidebar's change card and the review's details expanded. Each combination is captured and
## every visible interactive control must stay inside the window and inside its parent
## (ui_checks.bounds); in each size the Play menu and the sidebar's menus open inside the window.
## Last, a change with long reviewer notes and Details closed, in the bottom panel of short
## windows, under the 3D view and the script editor: its links, Discard and Accept show whole.
##
## Installed by run_visual_suite.py into a disposable testbed copy beside ui_checks.gd, and run
## in one theme: sizes, not colours, are what this checks.

## Window sizes in logical pixels. Where both side columns, the viewport and the sidebar don't
## fit, the column opened first closes (godot_panels.make_room).
const SIZES := [Vector2i(1024, 600), Vector2i(1280, 720), Vector2i(1366, 768), Vector2i(1920, 1080)]
## The sidebar's states: hidden, its own width, and dragged wide.
const SIDEBAR := ["nosidebar", "sidebar", "wide"]
## How wide "wide" drags the sidebar, in logical pixels (as far as the layout lets it).
const WIDE := 1000
## Short windows for a review with long notes and its details closed.
const SHORT := [Vector2i(1024, 600), Vector2i(1280, 640), Vector2i(1280, 720)]
## A reviewer's note long enough to fill the rail's middle.
const LONG_NOTE := (
	"game/tools/push_mower.gd: while turning, the visible mower trails the player's facing by"
	+ " up to a quarter turn, because the mesh's yaw is eased toward the body's yaw every"
	+ " frame instead of being parented to it; at walking speed the handle visibly leaves the"
	+ " player's hands, and a sharp turn makes the blades point the wrong way for half a second."
)

var _checks: GDScript
var _failed := false
var _plugin: EditorPlugin
var _panels: Node
var _toggle: Node
var _dock: Control
var _scale := 1.0


func _enter_tree() -> void:
	_checks = load(get_script().resource_path.get_base_dir().path_join("ui_checks.gd"))
	load("res://addons/joinery/ui_motion.gd").set_enabled(false)
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count: int = 4) -> void:
	for index: int in count:
		await get_tree().process_frame


static func _name(
	size: Vector2i, column: bool, files: bool, sidebar: String, review: bool
) -> String:
	var parts := PackedStringArray(["layout", "%dx%d" % [size.x, size.y]])
	if column:
		parts.append("scene")
	if files:
		parts.append("files")
	parts.append(sidebar)
	if review:
		parts.append("review")
	return "-".join(parts)


func _run() -> void:
	await get_tree().create_timer(3.0).timeout
	for node: Node in get_tree().root.find_children("*", "EditorPlugin", true, false):
		if node.get_script() == load("res://addons/joinery/plugin.gd"):
			_plugin = node as EditorPlugin
	_check(_plugin != null, "Joinery plugin must load")
	if _plugin == null:
		get_tree().quit(1)
		return
	_panels = _plugin.get("_panels")
	_toggle = _plugin.get_node_or_null("JoinerySidebarToggle")
	_dock = _plugin.get("_dock")
	_scale = EditorInterface.get_editor_scale()
	_check(_panels != null and _toggle != null and _dock != null, "Panels, sidebar and toggle")
	if _failed:
		get_tree().quit(1)
		return
	EditorInterface.set_main_screen_editor("3D")
	_converse()
	var window := get_tree().root
	var was := window.size
	for size: Vector2i in SIZES:
		window.size = Vector2i(Vector2(size) * _scale)
		await _frames(8)
		_check(
			window.size == Vector2i(Vector2(size) * _scale),
			"The window takes %s (is %s)" % [size, window.size]
		)
		for column: bool in [false, true]:
			for files: bool in [false, true]:
				for sidebar: String in SIDEBAR:
					for review: bool in [false, true]:
						await _arrange(column, files, sidebar, review)
						await _shot(_name(size, column, files, sidebar, review))
		await _menus(size)
	await _long_review()
	await _arrange(false, false, "sidebar", false)
	window.size = was
	await _frames(2)
	print("visual layouts: ", "FAILED" if _failed else "ok")
	get_tree().quit(1 if _failed else 0)


## A conversation whose change card has finished, with its steps expanded, and a review with
## its details open, so both are at their widest.
func _converse() -> void:
	_dock.call("set_chat_agent", "codex")
	_dock.call("set_chat_state", "connected", "Codex connected")
	var transcript: Node = _dock.get("_transcript")
	transcript.call("add_message", "You", "Make the door a little wider and darken the wood.")
	var card: Control = transcript.call("current_card")
	if card != null:
		card.call("set_sent")
		for step: String in [
			"I'll open art/sources/props/harbour_keeper_front_door_with_long_name.blend first.",
			"Widened Door by 20% with the Solidify and Bevel modifiers applied.",
			"Darkened the Wood material's base colour and re-baked the albedo texture.",
		]:
			card.call("add_step", step)
	_dock.call(
		"show_agent_message",
		"I widened **Door** by 20% in Blender and darkened the `Wood` material."
	)
	_dock.call("show_timing", {"outcome": "done", "total_ms": 30000})
	if card != null:
		card.call("set_details_open", true)
	(
		_dock
		. call(
			"show_file_review",
			"Make the door a little wider and darken the wood",
			"~ art/textures/door_wood.png",
			[
				{
					"path": "art/textures/door_wood.png",
					"media": "image",
					"before": _image("wood-before.png", Color("b07a45")),
					"after": _image("wood-after.png", Color("6b4423")),
				}
			],
			[
				{"path": "art/sources/props/harbour_keeper_front_door.blend", "change": "modified"},
				{"path": "art/models/props/harbour_keeper_front_door.glb", "change": "modified"},
				{"path": "recipes/harbour_keeper_front_door.toml", "change": "modified"},
			],
			"tx-layout"
		)
	)
	var panel: Control = _plugin.get("_review_panel")
	if panel != null:
		panel.call("set_details_open", true)


## A plain swatch saved in the cache, as a before or after image for the review.
func _image(file: String, colour: Color) -> String:
	var folder := OS.get_cache_dir().path_join("joinery-visual")
	DirAccess.make_dir_recursive_absolute(folder)
	var image := Image.create(512, 512, false, Image.FORMAT_RGB8)
	image.fill(colour)
	var path := folder.path_join(file)
	image.save_png(path)
	return path


## Close everything, then open what this combination names, in the order a user would: the
## sidebar is widened first, the columns and the review opened after.
func _arrange(column: bool, files: bool, sidebar: String, review: bool) -> void:
	for id: String in ["Scene", "Inspector", "FileSystem", "Review"]:
		_panels.call("hide_panel", id)
	_toggle.call("set_shown", true)
	await _frames(6)
	await _sidebar_width(_dock.get_combined_minimum_size().x)
	if sidebar == "nosidebar":
		_toggle.call("set_shown", false)
	elif sidebar == "wide":
		await _sidebar_width(WIDE * _scale)
		var widened := _sidebar_column()
		print("LAYOUT-WIDE ", widened.size.x / _scale if widened != null else -1.0)
	if column:
		_panels.call("toggle_column")
	if files:
		_panels.call("show_panel", "FileSystem")
	if review:
		_panels.call("show_panel", "Review")
	await get_tree().create_timer(0.25).timeout
	await _frames(6)


## Widen or narrow the sidebar to [param width], as far as the layout lets it: the same change
## to its split's offset that dragging the sidebar's left edge makes.
func _sidebar_width(width: float) -> void:
	var column := _sidebar_column()
	if column == null or not column.is_visible_in_tree():
		return
	var split := column.get_parent() as SplitContainer
	var index := -1
	for child: Node in split.get_children():
		var control := child as Control
		if control != null and control.visible and not control.top_level:
			if control == column:
				break
			index += 1
	var offsets := split.split_offsets
	if index < 0 or index >= offsets.size():
		return
	offsets[index] -= int(width - column.size.x)
	split.split_offsets = offsets
	await _frames(4)


## The dock column the sidebar sits in: a child of the editor's main horizontal split.
func _sidebar_column() -> Control:
	var node: Node = _dock
	while node != null and node.get_parent() != null:
		if (
			node.get_parent() is SplitContainer
			and not (node.get_parent() as SplitContainer).vertical
		):
			return node as Control
		node = node.get_parent()
	return null


## A code change with long reviewer notes, Details closed, in the bottom panel of a short
## window with the sidebar hidden, at its width and wide, under the 3D view and under the
## script editor (whose least height is larger): the links and Discard and Accept under the
## notes stay whole inside the window and inside every part that clips them.
func _long_review() -> void:
	var lines := PackedStringArray(
		["--- a/game/tools/push_mower.gd", "+++ b/game/tools/push_mower.gd", "@@ -12,9 +12,14 @@"]
	)
	for index: int in 24:
		lines.append("+\tvar yaw_%d := lerp_angle(mesh.rotation.y, body.rotation.y, delta)" % index)
	_dock.call(
		"show_file_review",
		"Keep the push mower in the player's hands while turning, at every walking speed",
		"\n".join(lines),
		[],
		[{"path": "game/tools/push_mower.gd", "change": "modified"}],
		"tx-layout-long"
	)
	var panel: Control = _plugin.get("_review_panel")
	panel.call("set_details_open", false)
	(
		panel
		. call(
			"show_glance",
			{
				"facts": ["Turning lag up to 90°", "Handle offset 0.4 m at walking speed"],
				"review_notes":
				[
					{"severity": "warn", "text": LONG_NOTE},
					{"severity": "warn", "text": LONG_NOTE},
					{"severity": "info", "text": LONG_NOTE},
				],
				"reviewer_status": "done",
			}
		)
	)
	var window := get_tree().root
	for size: Vector2i in SHORT:
		window.size = Vector2i(Vector2(size) * _scale)
		await _frames(8)
		for sidebar: String in SIDEBAR:
			for screen: String in ["3D", "Script"]:
				EditorInterface.set_main_screen_editor(screen)
				await _arrange(false, false, sidebar, true)
				var name := (
					"%s-long-%s" % [_name(size, false, false, sidebar, true), screen.to_lower()]
				)
				await _shot(name)
				for part: String in ["_accept", "_reject", "_details_button", "_revise"]:
					_whole(name, part.trim_prefix("_"), panel.get(part) as Control)
	EditorInterface.set_main_screen_editor("3D")


## [param control] shows in full: inside the window and every ancestor that clips it.
func _whole(screen: String, label: String, control: Control) -> void:
	_check(control != null and control.is_visible_in_tree(), "%s: %s shows" % [screen, label])
	if control == null or not control.is_visible_in_tree():
		return
	var rect := control.get_global_rect().grow(-1.0)
	var room := get_tree().root.get_visible_rect()
	var node := control.get_parent()
	while node is Control:
		var holder := node as Control
		if holder.clip_contents or holder is ScrollContainer:
			room = room.intersection(holder.get_global_rect())
		node = node.get_parent()
	_check(room.encloses(rect), "%s: %s is whole on screen: %s in %s" % [screen, label, rect, room])


func _shot(name: String) -> void:
	await _frames()
	var window := get_tree().root
	_checks.capture(name, window)
	_checks.bounds(name, [EditorInterface.get_base_control()], window.get_visible_rect())


## Godot's Play menu and the sidebar's menu open inside the window, with the sidebar wide and
## both columns open.
func _menus(size: Vector2i) -> void:
	await _arrange(true, true, "wide", true)
	var base := EditorInterface.get_base_control()
	var window := get_tree().root.get_visible_rect()
	var menus: Array[MenuButton] = []
	for node: Node in base.find_children("*", "EditorRunBar", true, false):
		for menu: Node in node.find_children("*", "MenuButton", true, false):
			if (menu as MenuButton).is_visible_in_tree():
				menus.append(menu as MenuButton)
	for node: Node in _dock.find_children("*", "MenuButton", true, false):
		if (node as MenuButton).is_visible_in_tree():
			menus.append(node as MenuButton)
	_check(menus.size() >= 2, "The Play menu and the sidebar's menu show at %s" % size)
	for menu: MenuButton in menus:
		# A message's menu may sit scrolled out of the transcript's view: bring it in first.
		var scroll := menu.get_parent()
		while scroll != null and not scroll is ScrollContainer:
			scroll = scroll.get_parent()
		if scroll != null:
			(scroll as ScrollContainer).ensure_control_visible(menu)
			await _frames(2)
		var button := menu.get_global_rect()
		_check(
			window.encloses(button.grow(-1.0)),
			"%s's button is inside the window at %s: %s" % [menu.name, size, button]
		)
		menu.show_popup()
		await _frames(4)
		var popup := menu.get_popup()
		var rect := Rect2(Vector2(popup.position), Vector2(popup.size))
		if not popup.is_embedded():
			rect.position -= Vector2(get_tree().root.position)
		_check(
			window.encloses(rect.grow(-1.0)),
			(
				"%s's menu opens inside the window at %s: %s (button %s, embedded %s, window at %s, screen %s)"
				% [
					menu.get_path(),
					size,
					rect,
					button,
					popup.is_embedded(),
					get_tree().root.position,
					DisplayServer.screen_get_usable_rect(get_tree().root.current_screen),
				]
			)
		)
		popup.hide()
		await _frames(2)
