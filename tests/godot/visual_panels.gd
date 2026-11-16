extends RefCounted
## The visual suite's Godot panel screens: the title bar with Godot's screen buttons, the rail
## in a short window, the sidebar's header and its show or hide toggle, Files stacked, Scene
## above Inspector open beside the viewport, Audio open alone in the bottom panel, each
## opened from its rail icon (godot_panels.gd), and Godot's Quick Open Scene picker from the
## rail's Scenes icon and "/open". The suite's "layout" screen is the default,
## with every panel hidden. Copied beside visual_editor_test.gd by run_visual_suite.py;
## [param suite] is that test, whose `_shot`, `_check`, `_frames` and `_plugin` these use.


static func screens(suite: Variant) -> void:
	await title_bar(suite)
	await rail_short(suite)
	await sidebar_title(suite)
	await sidebar_toggle(suite)
	await files_stacked(suite)
	await page_toggle(suite)
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
	await scenes(suite)


## The rail's Scenes icon, right after FileSystem, shows Godot's own Quick Open Scene picker,
## with Godot's shortcut in its tooltip. "/open <name>" opens the one scene the name matches,
## and shows the picker with the name typed in when several do (scene_finder.gd).
static func scenes(suite: Variant) -> void:
	var plugin: EditorPlugin = suite._plugin
	var panels: Node = plugin.get("_panels")
	var rail: Control = plugin.get("_rail")
	var items: Dictionary = rail.get("_panel_items")
	suite._check(items.has("Scenes"), "Scenes is on the rail")
	if not items.has("Scenes"):
		return
	var item: Control = items["Scenes"]
	var button := item.get("button") as Button
	var files: Control = items["FileSystem"] if items.has("FileSystem") else null
	suite._check(
		files != null and item.get_index() == files.get_index() + 1, "Scenes sits after FileSystem"
	)
	var finder: RefCounted = panels.get("finder")
	var keys := str(finder.call("shortcut_text"))
	suite._check(not keys.is_empty(), "Godot's Quick Open Scene shortcut is known")
	suite._check(
		button.tooltip_text == "Open a scene (%s)" % keys,
		"The Scenes tooltip names the shortcut: " + button.tooltip_text
	)
	var made := await _plant_scenes(suite)
	button.pressed.emit()
	await suite.get_tree().create_timer(0.8).timeout
	var dialog: Window = finder.call("dialog_node")
	suite._check(dialog != null and dialog.visible, "The Scenes icon shows Godot's picker")
	if dialog == null:
		return
	suite._check(button.button_pressed, "The Scenes icon is raised while the picker shows")
	await suite._shot("scenes-rail", [rail])
	await suite._shot("scenes-picker", [dialog], dialog)
	_cancel(dialog)
	await suite._frames(2)
	var dock: Control = plugin.get("_dock")
	dock.call("run_command", "/open harb")
	await suite.get_tree().create_timer(0.5).timeout
	var boxes := dialog.find_children("*", "LineEdit", true, false)
	suite._check(
		dialog.visible and not boxes.is_empty() and (boxes[0] as LineEdit).text == "harb",
		"/open with several matches shows the picker with the name typed in"
	)
	await suite._shot("scenes-picker-typed", [dialog], dialog)
	_cancel(dialog)
	await suite._frames(2)
	var was := EditorInterface.get_edited_scene_root()
	dock.call("run_command", "/open harbour night")
	await suite.get_tree().create_timer(0.5).timeout
	var opened := EditorInterface.get_edited_scene_root()
	suite._check(
		opened != null and opened.scene_file_path == made[1],
		"/open with one match opens that scene"
	)
	suite._check(not dialog.visible, "One match opens without the picker")
	if opened != null and opened != was:
		EditorInterface.close_scene()
		await suite._frames(4)
	for path: String in made:
		DirAccess.remove_absolute(ProjectSettings.globalize_path(path))
	DirAccess.remove_absolute(ProjectSettings.globalize_path("res://levels"))
	EditorInterface.get_resource_filesystem().scan()
	await suite.get_tree().create_timer(0.5).timeout


## Close Godot's picker as Esc does, so it tells its caller nothing was picked.
static func _cancel(dialog: Window) -> void:
	dialog.notification(Window.NOTIFICATION_WM_CLOSE_REQUEST)
	dialog.hide()


## Two small level scenes to find, known to Godot's file system once it has scanned.
static func _plant_scenes(suite: Variant) -> Array[String]:
	var made: Array[String] = ["res://levels/harbour.tscn", "res://levels/harbour_night.tscn"]
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path("res://levels"))
	for path: String in made:
		var level := Node3D.new()
		level.name = path.get_file().get_basename().capitalize().replace(" ", "")
		var scene := PackedScene.new()
		scene.pack(level)
		ResourceSaver.save(scene, path)
		level.free()
	var system := EditorInterface.get_resource_filesystem()
	system.scan()
	var deadline := Time.get_ticks_msec() + 10000
	while Time.get_ticks_msec() < deadline:
		await suite.get_tree().create_timer(0.2).timeout
		if not system.is_scanning() and system.get_file_type(made[1]) == "PackedScene":
			break
	suite._check(system.get_file_type(made[1]) == "PackedScene", "The planted scenes are scanned")
	return made


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


## The Journeyman sidebar's header: the "Journeyman" title in the heading style at the row's
## left, before the icon buttons.
static func sidebar_title(suite: Variant) -> void:
	var dock: Control = (suite._plugin as EditorPlugin).get("_dock")
	var header: Control = dock.get("_header")
	var title: Label = header.get("title")
	suite._check(title != null and title.is_visible_in_tree(), "The sidebar header has a title")
	if title == null:
		return
	suite._check(title.text == "Journeyman", "The header title reads Journeyman")
	suite._check(title.theme_type_variation == "JoineryTitle", "The title is in the heading style")
	var row := header.get("row") as Control
	var first: Control
	for child: Node in row.get_children():
		if (child as Control).is_visible_in_tree():
			first = child as Control
			break
	suite._check(first == title, "The title is first in the header row")
	suite._check(
		title.get_minimum_size().x <= title.size.x + 0.5, "The title shows in full, not cut short"
	)
	for button: Node in row.find_children("*", "Button", true, false):
		if (button as Control).is_visible_in_tree():
			suite._check(
				(button as Control).global_position.x >= title.get_global_rect().end.x,
				"The header buttons sit right of the title"
			)
	await suite._shot("sidebar-header", [row])


## The Files panel opens with the folder tree on top and the files below, each the column's
## full width, even when it was left side by side.
static func files_stacked(suite: Variant) -> void:
	var plugin: EditorPlugin = suite._plugin
	var panels: Node = plugin.get("_panels")
	var shell: RefCounted = plugin.get("_shell")
	var items: Dictionary = (plugin.get("_rail") as Control).get("_panel_items")
	suite._check(items.has("FileSystem"), "Files is on the rail")
	if not items.has("FileSystem"):
		return
	var button := items["FileSystem"].button as Button
	button.pressed.emit()
	await suite.get_tree().create_timer(0.3).timeout
	suite._check(panels.call("is_shown", "FileSystem"), "Files opens from the rail")
	suite._check(shell.call("files_split") == "vertical", "Files opens with the tree on top")
	var dock: Control = panels.call("_dock", "FileSystem")
	# Side by side with Godot's own Change Split Mode button, then closed and opened again.
	var toggle: Button
	for node: Node in dock.find_children("*", "Button", true, false):
		if (node as Button).tooltip_text == "Change Split Mode":
			toggle = node as Button
	suite._check(toggle != null, "Files has Godot's Change Split Mode button")
	if toggle != null:
		for step: int in 3:
			if shell.call("files_split") == "horizontal":
				break
			toggle.pressed.emit()
		suite._check(shell.call("files_split") == "horizontal", "The split button still works")
		button.pressed.emit()
		await suite.get_tree().create_timer(0.2).timeout
		button.pressed.emit()
		await suite.get_tree().create_timer(0.3).timeout
		suite._check(shell.call("files_split") == "vertical", "Files reopens stacked")
	var lists := dock.find_children("*", "ItemList", true, false)
	var trees := dock.find_children("*", "Tree", true, false)
	if not lists.is_empty() and not trees.is_empty():
		var list := lists[0] as Control
		var tree := trees[0] as Control
		suite._check(
			list.global_position.y >= tree.get_global_rect().end.y - 0.5, "The files sit below"
		)
		suite._check(
			list.size.x >= dock.size.x * 0.8,
			"The file list has the column's width (%d of %d)" % [list.size.x, dock.size.x]
		)
	await suite._shot("files-stacked", [dock])
	button.pressed.emit()
	await suite.get_tree().create_timer(0.2).timeout


## The whole sidebar hides and shows: from the Joinery menu's command, its shortcut and the
## rail's Journeyman icon, which stays on screen while the sidebar is hidden. The viewport takes
## the sidebar's width, and the choice is an editor setting.
## Joinery's pages (Timeline, Share) open from the rail and close on a second click.
static func page_toggle(suite: Variant) -> void:
	var plugin: EditorPlugin = suite._plugin
	var rail: Control = plugin.get("_rail")
	var pages: Dictionary = plugin.get("_pages")
	var buttons: Dictionary = rail.get("_buttons")
	suite._check(not pages.is_empty(), "Joinery's pages are on the rail")
	for id: String in pages:
		var button: Button = buttons.get(id)
		if button == null:
			suite._check(false, "%s has a rail icon" % id)
			continue
		for expected: String in [id, "godot"]:
			# A click on a toggle button: flip it, then press.
			button.set_pressed_no_signal(not button.button_pressed)
			button.pressed.emit()
			await suite.get_tree().create_timer(0.3).timeout
			suite._check(
				rail.call("selected") == expected,
				(
					"%s %s"
					% [id, "opens from the rail" if expected == id else "closes on a second click"]
				)
			)
		suite._check(not button.button_pressed, "%s's icon shows it is closed" % id)


static func sidebar_toggle(suite: Variant) -> void:
	var plugin: EditorPlugin = suite._plugin
	var toggle: Node = plugin.get_node_or_null("JoinerySidebarToggle")
	suite._check(toggle != null, "The sidebar has a show or hide toggle")
	if toggle == null:
		return
	var base := EditorInterface.get_base_control()
	var dock: Control = plugin.get("_dock")
	var settings := EditorInterface.get_editor_settings()
	var viewport := base.find_child("DockVSplitCenter", true, false) as Control
	var items: Dictionary = (plugin.get("_rail") as Control).get("_panel_items")
	suite._check(toggle.call("is_shown") and dock.is_visible_in_tree(), "The sidebar shows")
	suite._check(items.has("Journeyman"), "Journeyman is on the rail")
	var icon: Button = items["Journeyman"].button if items.has("Journeyman") else null
	suite._check(icon != null and icon.button_pressed, "Journeyman's rail icon shows it is open")
	var width := viewport.size.x
	toggle.call("toggle")
	await suite.get_tree().create_timer(0.8).timeout
	suite._check(not dock.is_visible_in_tree(), "The sidebar hides")
	suite._check(
		viewport.size.x > width + 100.0,
		"The viewport takes the sidebar's width (%d, was %d)" % [viewport.size.x, width]
	)
	suite._check(settings.get_setting(toggle.SETTING) == false, "Hidden is an editor setting")
	suite._check(icon != null and icon.is_visible_in_tree(), "The rail icon stays to bring it back")
	suite._check(icon != null and not icon.button_pressed, "The rail icon shows it is hidden")
	await suite._shot("sidebar-hidden", [base])
	if icon != null:
		icon.pressed.emit()
	await suite.get_tree().create_timer(0.3).timeout
	suite._check(dock.is_visible_in_tree(), "The rail icon brings the sidebar back")
	suite._check(is_equal_approx(viewport.size.x, width), "The viewport gives the width back")
	suite._check(settings.get_setting(toggle.SETTING) == true, "Shown is an editor setting")
	# The shortcut (Ctrl+Alt+J, Cmd+Option+J on macOS), twice.
	var key := ((toggle.call("shortcut") as Shortcut).events[0] as InputEventKey).duplicate()
	key.pressed = true
	for expected: bool in [false, true]:
		suite.get_tree().root.push_input(key)
		await suite.get_tree().create_timer(0.3).timeout
		suite._check(dock.is_visible_in_tree() == expected, "The shortcut shows or hides it")
	var menus := base.find_children("Joinery", "PopupMenu", true, false)
	if not menus.is_empty():
		var menu := menus[0] as PopupMenu
		var listed := false
		for index: int in menu.item_count:
			listed = listed or menu.get_item_text(index) == toggle.LABEL
		suite._check(listed, "The Joinery menu lists " + toggle.LABEL)
