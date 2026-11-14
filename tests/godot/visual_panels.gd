extends RefCounted
## The visual suite's Godot panel screens: the title bar with Godot's screen buttons, the rail
## in a short window, the sidebar's header and its show or hide toggle, Files stacked, Scene
## above Inspector open beside the viewport, and Audio open alone in the bottom panel, each
## opened from its rail icon (godot_panels.gd). The suite's "layout" screen is the default,
## with every panel hidden. Copied beside visual_editor_test.gd by run_visual_suite.py;
## [param suite] is that test, whose `_shot`, `_check`, `_frames` and `_plugin` these use.


static func screens(suite: Variant) -> void:
	await title_bar(suite)
	await rail_short(suite)
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


## The rail in a short window: its icons overflow and scroll up and down, the wheel over the
## icons scrolls them, and the last one comes into view. The column never shows a scroll bar,
## at the editor's own height or in a short window, and its width never changes.
static func rail_short(suite: Variant) -> void:
	var rail: Control = (suite._plugin as EditorPlugin).get("_rail")
	var scroll := rail.get_parent() as ScrollContainer if rail != null else null
	suite._check(scroll != null, "The rail sits in a scroll column")
	if scroll == null:
		return
	var bar := scroll.get_v_scroll_bar()
	var column := scroll.get_parent() as Control
	var width := column.size.x
	suite._check(not bar.visible, "The rail shows no scroll bar")
	var window := scroll.get_window()
	var was := window.size
	window.size = Vector2i(was.x, 420)
	await suite._frames(8)
	var tall := rail.get_combined_minimum_size().y
	suite._check(
		tall > scroll.size.y, "The rail overflows a short window (%d in %d)" % [tall, scroll.size.y]
	)
	suite._check(not bar.visible, "The rail shows no scroll bar while it overflows")
	suite._check(is_equal_approx(column.size.x, width), "Overflow never widens the rail")
	var last := _last_icon(rail)
	suite._check(last != null, "The rail has icons")
	if last == null:
		window.size = was
		return
	var view := scroll.get_global_rect()
	suite._check(not view.encloses(last.get_global_rect()), "The last icon starts out of view")
	for step: int in 40:
		if view.encloses(last.get_global_rect().grow(-0.5)):
			break
		var wheel := InputEventMouseButton.new()
		wheel.button_index = MOUSE_BUTTON_WHEEL_DOWN
		wheel.pressed = true
		wheel.position = view.get_center()
		wheel.global_position = wheel.position
		window.push_input(wheel)
		await suite._frames(1)
	suite._check(scroll.scroll_vertical > 0, "The wheel over an icon scrolls the rail")
	suite._check(
		view.encloses(last.get_global_rect().grow(-0.5)), "The wheel brings the last icon into view"
	)
	suite._check(not bar.visible, "The rail shows no scroll bar while scrolled")
	await suite._shot("rail-short", [scroll])
	scroll.scroll_vertical = 0
	await suite._frames(2)
	last.grab_focus()
	await suite._frames(4)
	suite._check(
		view.encloses(last.get_global_rect().grow(-0.5)),
		"Keyboard focus brings the last icon into view"
	)
	last.release_focus()
	scroll.scroll_vertical = 0
	window.size = was
	await suite._frames(8)


## The rail's icon buttons from top to bottom, without its readiness card.
static func _icons(rail: Control) -> Array[Button]:
	var card := rail.get("_card_layer") as Control
	var icons: Array[Button] = []
	for node: Node in rail.find_children("*", "Button", true, false):
		var button := node as Button
		if button.is_visible_in_tree() and not card.is_ancestor_of(button):
			icons.append(button)
	icons.sort_custom(
		func(a: Button, b: Button) -> bool: return a.global_position.y < b.global_position.y
	)
	return icons


static func _last_icon(rail: Control) -> Button:
	var icons := _icons(rail)
	return icons[-1] if not icons.is_empty() else null


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
