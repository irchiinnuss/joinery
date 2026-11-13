extends RefCounted
## The visual suite's Godot panel screens: the title bar with Godot's screen buttons, Scene
## above Inspector open beside the viewport, and Audio open alone in the bottom panel, each
## opened from its rail icon (godot_panels.gd). The suite's "layout" screen is the default,
## with every panel hidden. Copied beside visual_editor_test.gd by run_visual_suite.py;
## [param suite] is that test, whose `_shot`, `_check`, `_frames` and `_plugin` these use.


static func screens(suite: Variant) -> void:
	await title_bar(suite)
	var plugin: EditorPlugin = suite._plugin
	var panels: Node = plugin.get("_panels")
	var rail: Control = plugin.get("_rail")
	suite._check(panels != null and rail != null, "Godot's panels are on the rail")
	if panels == null or rail == null:
		return
	var base := EditorInterface.get_base_control()
	var items: Dictionary = rail.get("_panel_items")
	suite._check(not panels.call("is_shown", "Scene"), "Scene and Inspector start hidden")
	panels.call("toggle_column")
	await suite.get_tree().create_timer(0.3).timeout
	suite._check(items["Inspector"].button.button_pressed, "Inspector's icon shows it is open")
	await suite._shot("scene-column-open", [base])
	panels.call("toggle_column")
	(items["Audio"].button as Button).pressed.emit()
	await suite.get_tree().create_timer(0.3).timeout
	suite._check(panels.call("is_shown", "Audio"), "Audio opens from the rail")
	await suite._shot("panels-audio", [base])
	(items["Audio"].button as Button).pressed.emit()
	await suite._frames()


## Godot's 2D, 3D, Script and Game icons at the title bar's right end, at the editor's own
## width and in a narrow window, where nothing in the row may overlap.
static func title_bar(suite: Variant) -> void:
	var base := EditorInterface.get_base_control()
	var screens := base.find_child("EditorMainScreenButtons", true, false) as Control
	suite._check(screens != null and screens.is_visible_in_tree(), "Godot's screen buttons show")
	if screens == null:
		return
	var bar := screens.get_parent() as Control
	await suite._shot("title-bar", [bar])
	var window := bar.get_window()
	var was := window.size
	window.size = Vector2i(900, maxi(620, was.y))
	await suite._frames(8)
	var previous: Control
	for child: Node in bar.get_children():
		var control := child as Control
		if control == null or not control.is_visible_in_tree() or control.size.x <= 0.0:
			continue
		if previous != null:
			suite._check(
				control.position.x >= previous.position.x + previous.size.x - 0.5,
				"%s overlaps %s in a narrow window" % [control.name, previous.name]
			)
		previous = control
	suite._check(screens.get_global_rect().end.x <= window.size.x, "Screen buttons fit")
	await suite._shot("title-bar-narrow", [bar])
	window.size = was
	await suite._frames(8)
