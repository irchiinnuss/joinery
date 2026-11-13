@tool
extends EditorPlugin
## Install only in a temporary copy of testbed for full editor verification.

var _failed := false
var _screens: Array[String] = []


func _enter_tree() -> void:
	main_screen_changed.connect(func(screen: String) -> void: _screens.append(screen))
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	await get_tree().create_timer(3.0).timeout
	var base := EditorInterface.get_base_control()
	print("layout check: editor loaded")
	var plugin: EditorPlugin
	for node: Node in get_tree().root.find_children("*", "EditorPlugin", true, false):
		if node.get_script() == load("res://addons/joinery/plugin.gd"):
			plugin = node as EditorPlugin
			break
	_check(plugin != null, "Joinery plugin must load")
	if plugin == null:
		get_tree().quit(1)
		return
	_check(plugin._dock.name == "Journeyman", "Restore the earlier sidebar layout")
	_check(
		plugin._dock.size.x >= plugin._dock.get_theme_default_font_size() * 24,
		"Saved narrow layouts must open with a readable chat width"
	)
	_check(plugin._chat != null, "Start the selected agent chat transport")
	_check(
		plugin._dock.prompt_submitted.is_connected(plugin._chat.submit),
		"Ordinary prompts must reach the chat transport"
	)
	if OS.get_environment("JOINERY_EXPECT_CODEX") == "1":
		var connection_deadline := Time.get_ticks_msec() + 15000
		while not plugin._chat._connected and Time.get_ticks_msec() < connection_deadline:
			await get_tree().process_frame
		_check(plugin._chat._connected, "The startup Codex account must connect in the editor")
	var chat_before: String = plugin._dock._transcript.get_parsed_text()
	var navigation := base.find_child("JoineryNavigation", true, false) as Control
	_check(navigation != null, "Icon navigation must exist")
	var switches := base.find_child("EditorMainScreenButtons", true, false) as Control
	_check(switches is HBoxContainer, "Godot's screen buttons sit in a row")
	_check(switches.is_visible_in_tree(), "Godot's screen buttons show")
	_check(switches.get_parent().is_class("EditorTitleBar"), "They sit in the title bar")
	_check(not navigation.is_ancestor_of(switches), "They are not on the app rail")
	var shown: Array[String] = []
	for button: Node in switches.get_children():
		if button is Button and (button as Button).visible:
			shown.append(button.name)
			_check(button.text.is_empty(), "Godot's screen buttons are icon-only")
			_check(button.icon != null, "%s shows Godot's icon" % button.name)
			_check(button.tooltip_text == button.name, "%s's tooltip names it" % button.name)
	for screen: String in ["2D", "3D", "Script"]:
		_check(screen in shown, screen + " is in the title bar: %s" % [shown])
	for screen: String in shown:
		# No Asset Store (Joinery fetches no assets), no Assets (it opens from the rail).
		_check(screen in ["2D", "3D", "Script", "Game"], screen + " must not be in the title bar")
	_check(base.find_child("JoineryScreenSwitch", true, false) == null, "No text switch")
	await _godot_panels(plugin, base)
	_check(plugin._shell._toolbars.size() >= 1, "The viewport toolbar must be discovered")
	for control: Control in plugin._shell._toolbars:
		_check(control.visible, "Viewport controls show by default")
	var scene_tabs := base.find_children("*", "EditorSceneTabs", true, false)
	_check(not scene_tabs.is_empty(), "Scene tabs must exist")
	for tabs: Node in scene_tabs:
		_check((tabs as Control).is_visible_in_tree(), "Scene tabs (with their + button) must show")
	var sidebar := base.find_child("DockSlotRightBL", true, false) as TabContainer
	_check(not sidebar.tabs_visible, "Chat sidebar must not have a dock tab bar")
	_check(sidebar.get_popup() == null, "Journeyman must not show a docking menu")
	sidebar.get_tab_bar().tab_rmb_clicked.emit(0)
	for popup: Node in base.find_children("*", "DockContextPopup", true, false):
		_check(not popup.visible, "Right-click must not open the sidebar docking menu")
	for version: Node in base.find_children("*", "EditorVersionButton", true, false):
		_check(not version.is_visible_in_tree(), "Bottom version info must be hidden")
	plugin._on_toggle_toolbar()
	_check(
		plugin._dock._transcript.get_parsed_text() == chat_before,
		"Toolbar diagnostics must stay out of chat"
	)
	var scene := EditorInterface.get_edited_scene_root()
	# The testbed's main scene needs Blender to import; without it no scene is open.
	if scene != null and scene.has_node("Door"):
		var selection := EditorInterface.get_selection()
		selection.clear()
		selection.add_node(scene.get_node("Door"))
		await get_tree().create_timer(0.1).timeout
		print("layout check: object target ", plugin._dock.target_snapshot())
		_check(
			str(plugin._dock.target_snapshot()[0]["id"]).ends_with("::Door"),
			"Engine object selection must reach Chatup"
		)
		selection.clear()
		selection.add_node(scene.get_node("Music"))
		await get_tree().create_timer(0.1).timeout
		_check(
			plugin._dock.target_snapshot()[0]["kind"] == "Audio", "Identify engine audio selection"
		)
		selection.clear()
	for control: Control in plugin._shell._toolbars:
		_check(not control.visible, "Viewport controls must be toggleable")
	plugin._on_toggle_toolbar()
	EditorInterface.set_main_screen_editor("3D")
	await get_tree().create_timer(0.2).timeout
	await _capture("/tmp/joinery-editor-layout-3d.png")
	print("layout check: 3D view captured")
	var deadline := Time.get_ticks_msec() + 15000
	while plugin._workspace._command_job != null and Time.get_ticks_msec() < deadline:
		await get_tree().process_frame
	_check(plugin._rail._buttons.has("blender"), "Blender must be in the rail")
	_check(plugin._rail._buttons.has("godot"), "Godot must be in the app rail")
	_check(plugin._rail._buttons.has(""), "Assets must be in the app rail")
	await _rail_states(plugin)
	_check(
		plugin._rail.find_child("ViewportToolbarToggle", true, false) == null,
		"The viewport toolbar has no rail toggle; /toolbar shows it"
	)
	for id: String in plugin._rail._buttons:
		var app_button: Button = plugin._rail._buttons[id]
		_check(app_button.icon_alignment == HORIZONTAL_ALIGNMENT_CENTER, "Center app icons")
	if plugin._rail._buttons.has("blender"):
		plugin._rail._buttons["blender"].pressed.emit()
		await get_tree().process_frame
		print("layout check: Blender selected")
		_check(plugin._workspace._tool_id() == "blender", "Rail must select Blender view")
		for asset: Dictionary in plugin._workspace._rows:
			_check(asset["tool"] == "blender", "Blender view may only list Blender-owned assets")
		for index: int in plugin._workspace._assets.item_count:
			var text: String = plugin._workspace._assets.get_item_text(index)
			_check(not text.contains(".blend"), "Display names must omit extensions")
			_check(not text.contains("_"), "Display names must be readable")
		await get_tree().create_timer(0.3).timeout
		deadline = Time.get_ticks_msec() + 10000
		while plugin._workspace._command_job != null and Time.get_ticks_msec() < deadline:
			await get_tree().process_frame
		_check(plugin._workspace._command_job == null, "Initial workspace load must finish")
		var before: String = plugin._workspace._stamp
		var lock := FileAccess.open("res://joinery.lock", FileAccess.READ_WRITE)
		lock.seek_end()
		lock.store_string("\n# automatic refresh test\n")
		lock.close()
		deadline = Time.get_ticks_msec() + 10000
		while plugin._workspace._stamp == before and Time.get_ticks_msec() < deadline:
			await get_tree().process_frame
		_check(plugin._workspace._stamp != before, "Workspace must refresh automatically")
		print("layout check: automatic refresh checked")
		var viewer := plugin._workspace._preview.get_child(0) as SubViewportContainer
		_check(viewer != null, "Model viewer must be available")
		var camera := (viewer.get_child(0) as SubViewport).get_camera_3d()
		var zoom := InputEventMouseButton.new()
		zoom.position = viewer.get_global_rect().get_center()
		zoom.global_position = zoom.position
		zoom.pressed = true
		zoom.button_index = MOUSE_BUTTON_WHEEL_UP
		var distance := camera.position.z
		get_tree().root.push_input(zoom)
		_check(camera.position.z < distance, "Wheel must zoom in the real editor")
		zoom.button_index = MOUSE_BUTTON_WHEEL_DOWN
		get_tree().root.push_input(zoom)
		_check(camera.position.z > distance * 0.9, "Wheel must zoom out in the real editor")
		_check(plugin._workspace._preview.get_child_count() == 1, "Model has no extra controls")
		for button: Node in plugin._workspace.find_children("*", "Button", true, false):
			_check(button.text != "Refresh", "No manual Refresh button")
		await _capture("/tmp/joinery-editor-layout-tools.png")
		plugin._workspace.select_resource("res://art/sky/src/dusk_sky.blend")
		var selected: PackedInt32Array = plugin._workspace._assets.get_selected_items()
		plugin._workspace._assets.item_selected.emit(selected[0])
		_check(
			plugin._workspace._preview.get_child(0) is TextureRect,
			"Sky source must show its rendered counterpart"
		)
		_check(
			plugin._dock.target_snapshot()[0]["id"] == "res://art/sky/src/dusk_sky.blend",
			"Preview selection must target the native sky source"
		)
		plugin._dock._prompt.text = "/"
		plugin._dock._update_commands("/")
		await get_tree().process_frame
		_check(plugin._dock._command_popup.visible, "Slash command menu must open")
		_check(
			(
				plugin._dock._command_popup.get_global_rect().end.y
				<= plugin._dock._prompt.global_position.y
			),
			"Slash command menu must open upward"
		)
		await _capture("/tmp/joinery-editor-chatup.png")
		plugin._dock._prompt.text = "/hel"
		plugin._dock._update_commands("/hel")
		plugin._dock._prompt.grab_focus()
		var enter := InputEventKey.new()
		enter.keycode = KEY_ENTER
		enter.pressed = true
		get_tree().root.push_input(enter)
		_check(
			plugin._dock._transcript.get_parsed_text().contains("You  /hel"),
			"Enter must send a slash command in the real editor"
		)
		_check(not plugin._dock._command_popup.visible, "Enter must dismiss suggestions")
		await _capture("/tmp/joinery-editor-chat-sent.png")
	if plugin._rail._buttons.has("supercollider"):
		plugin._rail._buttons["supercollider"].pressed.emit()
		await get_tree().process_frame
		_check(
			plugin._workspace._audio.stream != null, "Audio app view must show playback controls"
		)
		await _capture("/tmp/joinery-editor-layout-audio.png")
	await _review_in_focus(plugin)
	await _single_accept_notice(plugin)
	print("editor layout: ", "FAILED" if _failed else "ok")
	get_tree().quit(1 if _failed else 0)


func _rail_states(plugin: EditorPlugin) -> void:
	# The scene is a Node3D, so the Godot item opens the 3D view.
	EditorInterface.set_main_screen_editor("Script")
	_screens.clear()
	plugin._rail._buttons["godot"].pressed.emit()
	await get_tree().process_frame
	_check(_screens.has("3D"), "Godot opens 3D for a 3D scene")
	var row := EditorInterface.get_base_control().find_child("EditorMainScreenButtons", true, false)
	_check(row != null and row.get_node("3D").button_pressed, "Godot's 3D button shows it is open")
	# Assets opens from the rail and still reaches its screen, though it has no title bar button.
	EditorInterface.set_main_screen_editor("Assets")
	await get_tree().process_frame
	_check(_screens.has("Assets"), "Godot still opens Joinery's Assets screen")
	_check(not row.get_node("3D").button_pressed, "No screen button is raised over Assets")
	plugin._rail._buttons["godot"].pressed.emit()
	await get_tree().process_frame
	_check(plugin._rail._buttons["godot"].button_pressed, "Godot is the selected rail item")
	var deadline := Time.get_ticks_msec() + 30000
	while plugin._rail._job != null and Time.get_ticks_msec() < deadline:
		await get_tree().process_frame
	# Readiness is optional data: when the CLI reports it, the tooltip says it.
	var readiness: Dictionary = plugin._rail._readiness
	if readiness.has("blender") and plugin._rail._buttons.has("blender"):
		var status := str(readiness["blender"]["status"])
		var wording := str(plugin._rail.STATUS_TEXT.get(status, ""))
		var tip: String = plugin._rail._buttons["blender"].tooltip_text
		_check(wording.is_empty() or tip.contains(wording), "Blender's tooltip names its readiness")


## Godot's own panels on the rail: hidden by default, each opened and hidden from its icon,
## the bottom panel showing one dock at a time, and the choice kept as an editor setting.
func _godot_panels(plugin: EditorPlugin, base: Control) -> void:
	var panels: Node = plugin._panels
	_check(panels != null, "Godot's panels are wired in Joinery's layout")
	if panels == null:
		return
	var settings := EditorInterface.get_editor_settings()
	settings.set_setting(panels.SIDE_SETTING, PackedStringArray())
	for id: String in ["Scene", "Inspector", "FileSystem"]:
		if panels.is_shown(id):
			panels.hide_panel(id)
	await get_tree().create_timer(0.3).timeout
	var bottom := panels._bottom as TabContainer
	_check(bottom.current_tab == -1 and not bottom.tabs_visible, "No bottom strip by default")
	var items: Dictionary = plugin._rail._panel_items
	for id: String in ["Scene", "Inspector", "FileSystem", "Output", "Audio", "Debugger"]:
		_check(items.has(id), id + " is on the rail")
		if items.has(id):
			var button: Button = items[id].button
			_check(not button.button_pressed, id + " starts hidden")
			var named: String = button.accessibility_name
			_check(button.tooltip_text.contains(named), id + "'s icon has a tooltip naming it")
	_check(not items.has("Review") or items["Review"].get_parent().name == "OwnPanels", "Review")
	var viewport := base.find_child("DockVSplitCenter", true, false) as Control
	var narrow := viewport.size.x
	await _capture("/tmp/joinery-editor-panels-hidden.png")
	# Scene and Inspector together (Ctrl+Alt+I), side by side with the viewport.
	panels.toggle_column()
	await get_tree().create_timer(0.3).timeout
	for pair: Array in [["Scene", "DockSlotLeftUR"], ["Inspector", "DockSlotLeftBR"]]:
		var dock: Control = panels._dock(pair[0])
		_check(dock.get_parent().name == pair[1], "%s opens in %s" % pair)
		_check(dock.is_visible_in_tree(), str(pair[0]) + " shows")
		_check(items[pair[0]].button.button_pressed, str(pair[0]) + "'s icon shows it is open")
	var column := base.find_child("DockVSplitLeftR", true, false) as Control
	_check(column.size.x >= 200.0, "The column opens at a usable width (%d)" % column.size.x)
	_check(viewport.size.x < narrow, "The viewport gives the column its width")
	_check(
		Array(settings.get_setting(panels.SIDE_SETTING)) == ["Scene", "Inspector"],
		"The open side panels are an editor preference"
	)
	await _capture("/tmp/joinery-editor-panels-column.png")
	# Focus hides everything; leaving it brings back the user's own choice.
	plugin._on_toggle_focus()
	await get_tree().create_timer(0.2).timeout
	_check(not panels.is_shown("Scene"), "Focus hides the column")
	plugin._on_toggle_focus()
	await get_tree().create_timer(0.2).timeout
	_check(panels.is_shown("Scene"), "Leaving Focus brings the column back")
	items["Scene"].button.pressed.emit()
	await get_tree().create_timer(0.2).timeout
	_check(not panels.is_shown("Scene") and panels.is_shown("Inspector"), "One icon, one panel")
	panels.toggle_column()
	await get_tree().create_timer(0.2).timeout
	_check(not panels.is_shown("Inspector"), "The shortcut hides what shows")
	print("layout check: side panels checked")
	items["FileSystem"].button.pressed.emit()
	await get_tree().create_timer(0.3).timeout
	var files: Control = panels._dock("FileSystem")
	_check(files.get_parent().name == "DockSlotLeftUL", "Files open in their own side column")
	items["FileSystem"].button.pressed.emit()
	await get_tree().create_timer(0.2).timeout
	print("layout check: files checked")
	# A bottom dock shows alone, with its own title and ×, and hides again.
	items["Audio"].button.pressed.emit()
	await get_tree().create_timer(0.3).timeout
	_check(panels.is_shown("Audio"), "Audio opens in the bottom panel")
	var visible_tabs := 0
	for index: int in bottom.get_tab_count():
		visible_tabs += 0 if bottom.is_tab_hidden(index) else 1
	_check(visible_tabs == 1, "The bottom panel shows one dock (%d tabs)" % visible_tabs)
	var center_split := base.find_child("DockVSplitCenter", true, false) as Control
	_check(bottom.size.y >= center_split.size.y * 0.3, "Bottom docks open with useful height")
	await _capture("/tmp/joinery-editor-panels-audio.png")
	print("layout check: audio shown")
	var close := bottom.find_child("JoineryHidePanel", true, false) as Button
	_check(close != null and close.is_visible_in_tree(), "The bottom panel has a ×")
	if close != null:
		close.pressed.emit()
	_check(bottom.current_tab == -1, "× hides the bottom panel")
	items["Output"].button.pressed.emit()
	await get_tree().process_frame
	_check(panels.is_shown("Output"), "Output opens from the rail")
	items["Output"].button.pressed.emit()
	await get_tree().process_frame
	_check(not panels.is_shown("Output"), "Output's icon again hides it")
	push_error("Joinery layout check: a deliberate error for Output's mark")
	await get_tree().create_timer(0.8).timeout
	_check(items["Output"].dot.visible, "Output's icon marks new errors")
	_check(not panels.is_shown("Output"), "Output never opens by itself")


func _review_in_focus(plugin: EditorPlugin) -> void:
	if plugin._focus.is_active():
		plugin._on_toggle_focus()
	plugin._on_toggle_focus()
	_check(plugin._focus.is_active(), "Focus mode must find the editor docks")
	var dock := (plugin._review_tab as Control).get_parent() as Control
	var tabs := dock.get_parent() as TabContainer
	_check(tabs.current_tab == -1, "Focus mode collapses the bottom panel to its strip")
	var focus_button := EditorInterface.get_base_control().find_child("JoineryFocus", true, false)
	_check(focus_button != null and focus_button.button_pressed, "⤢ shows Focus mode is on")
	plugin._open_review("Layout check")
	await get_tree().create_timer(0.3).timeout
	var review: Control = plugin._review_panel
	_check(tabs.get_tab_idx_from_control(dock) == 0, "Review is the first bottom tab")
	_check(review.is_visible_in_tree(), "A pending review opens on its full before and after")
	var accept := _button_starting(review, "Accept")
	_check(accept != null and accept.is_visible_in_tree(), "Accept stays visible in Focus mode")
	await _capture("/tmp/joinery-editor-focus-review.png")
	plugin._close_review("", "info")
	await get_tree().process_frame
	_check(tabs.current_tab == -1 and not tabs.tabs_visible, "The panel hides once nothing waits")
	focus_button.pressed.emit()
	_check(not plugin._focus.is_active(), "⤢ turns Focus mode off")
	_check(not focus_button.button_pressed, "⤢ shows Focus mode is off")


## A file decision that also commits a live scene edit posts exactly one notice.
func _single_accept_notice(plugin: EditorPlugin) -> void:
	var scene := EditorInterface.get_edited_scene_root()
	var edit := {
		"summary": "Add a layout check box",
		"operations": [{"operation": "create_box", "name": "LayoutBox", "size": [1, 1, 1]}]
	}
	var event := {"payload": edit, "context": plugin._editor_context([]), "targets": []}
	_check(plugin._preview_scene_edit(event)["success"], "Live preview for the notice check")
	var before := _transcript(plugin._dock).count("Change accepted")
	plugin._file_review = {"transaction_id": "layout-notice", "changes": []}
	plugin._file_accept_live_id = plugin._edits.transaction_id
	await plugin._on_file_decision(
		{"transaction_id": "layout-notice", "accepted": true, "changes": []}
	)
	await get_tree().process_frame
	_check(not plugin._edits.pending(), "The live edit is committed with the files")
	_check(scene.has_node("LayoutBox"), "The accepted box stays")
	var after := _transcript(plugin._dock).count("Change accepted")
	_check(after == before + 1, "One accepted notice, not two (%d)" % (after - before))
	_check(not plugin._review_open, "The Review panel closes after the decision")
	var manager := plugin.get_undo_redo()
	manager.get_history_undo_redo(manager.get_object_history_id(scene)).undo()


func _transcript(dock: Control) -> String:
	var text := ""
	for node: Node in dock.find_children("*", "", true, false):
		if node is RichTextLabel:
			text += (node as RichTextLabel).get_parsed_text() + "\n"
		elif node is Label:
			text += (node as Label).text + "\n"
	return text


func _button_starting(root: Node, prefix: String) -> Button:
	for node: Node in root.find_children("*", "Button", true, false):
		if (node as Button).text.begins_with(prefix):
			return node as Button
	return null


func _capture(path: String) -> void:
	if DisplayServer.get_name() == "headless":
		return
	RenderingServer.force_draw()
	get_tree().root.get_texture().get_image().save_png(path)
