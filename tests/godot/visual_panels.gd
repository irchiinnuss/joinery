extends RefCounted
## The visual suite's Godot panel screens: Scene above Inspector open beside the viewport, and
## Audio open alone in the bottom panel, each opened from its rail icon (godot_panels.gd). The
## suite's "layout" screen is the default, with every panel hidden. Copied beside
## visual_editor_test.gd by run_visual_suite.py; [param suite] is that test, whose `_shot`,
## `_check`, `_frames` and `_plugin` these use.


static func screens(suite: Variant) -> void:
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
