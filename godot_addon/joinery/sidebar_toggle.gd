@tool
extends Node
## Shows or hides the whole Journeyman sidebar: Joinery › Show or Hide Journeyman, Ctrl+Alt+J
## (Cmd+Option+J on macOS), or the Journeyman icon on the rail, which stays on screen while the
## sidebar is hidden so it can always come back without the menu. Shown by default; the choice
## is an editor preference (SETTING), like the viewport toolbar's.
##
## Hiding hides the dock column that holds the sidebar, so the viewport takes its width. The
## dock itself is never closed: a closed dock leaves the scene tree, which would drop the chat's
## connection and any running command. Godot shows a dock column again by itself when its docks
## change or a layout loads, so a hidden column is hidden again whenever that happens.

## Editor setting: false while the sidebar is hidden. Absent means shown.
const SETTING := "joinery/show_sidebar"
const SELF_PATH := "res://addons/joinery/sidebar_toggle.gd"
const LABEL := "Show or Hide Journeyman"

var _dock: Control
var _settings: EditorSettings
var _column: Control
var _shown := true
var _shortcut := Shortcut.new()


## Add the toggle to [param plugin]: its Joinery menu entry, its shortcut, and the saved
## preference, applied once the editor has placed its docks.
static func install(plugin: EditorPlugin, dock: Control) -> Node:
	var toggle: Node = load(SELF_PATH).new()
	toggle.set("_dock", dock)
	toggle.set("_settings", EditorInterface.get_editor_settings())
	plugin.add_child(toggle)
	plugin.call("add_command", LABEL, toggle.toggle)
	toggle.call_deferred("apply")
	# The editor loads the saved layout after its first file scan, which can show the column.
	EditorInterface.get_resource_filesystem().sources_changed.connect(
		func(_exist: bool) -> void:
			if is_instance_valid(toggle):
				toggle.call_deferred("apply"),
		CONNECT_ONE_SHOT
	)
	return toggle


func _init() -> void:
	name = "JoinerySidebarToggle"
	var key := InputEventKey.new()
	key.keycode = KEY_J
	key.alt_pressed = true
	key.command_or_control_autoremap = true
	_shortcut.events = [key]


## True while the sidebar is shown (the saved preference).
func is_shown() -> bool:
	return _shown


func shortcut() -> Shortcut:
	return _shortcut


## Show or hide the sidebar as the preference says.
func apply() -> void:
	if _settings != null and _settings.has_setting(SETTING):
		_shown = bool(_settings.get_setting(SETTING))
	_place()


## Hide the sidebar when it shows, show it when it is hidden, and remember the choice.
func toggle() -> void:
	set_shown(not _shown)


func set_shown(on: bool) -> void:
	var reveal := on and not _shown
	_shown = on
	if _settings != null:
		_settings.set_setting(SETTING, on)
	_place(reveal)


## Hide the dock column holding the sidebar while it should be hidden, following it if it
## moved; with [param reveal], show it again (Godot keeps an empty column hidden).
func _place(reveal := false) -> void:
	var column := _find_column()
	if column != _column:
		_unwatch()
		_column = column
		if _column != null:
			_column.visibility_changed.connect(_on_column_visibility)
	if _column == null:
		return
	if not _shown:
		_column.visible = false
	elif reveal:
		_column.visible = (_column as TabContainer).get_tab_count() > 0


## The tab container (dock slot) that holds the sidebar's dock, or null.
func _find_column() -> Control:
	if not is_instance_valid(_dock):
		return null
	var wrapper := _dock.get_parent()
	if wrapper == null:
		return null
	return wrapper.get_parent() as TabContainer


func _on_column_visibility() -> void:
	if not _shown and is_instance_valid(_column) and _column.visible:
		_column.hide.call_deferred()


func _unwatch() -> void:
	if (
		is_instance_valid(_column)
		and _column.visibility_changed.is_connected(_on_column_visibility)
	):
		_column.visibility_changed.disconnect(_on_column_visibility)


func _shortcut_input(event: InputEvent) -> void:
	if event.is_pressed() and not event.is_echo() and _shortcut.matches_event(event):
		toggle()
		get_viewport().set_input_as_handled()


## Put the column back as Godot had it when the plugin goes away.
func _exit_tree() -> void:
	_unwatch()
	if is_instance_valid(_column):
		_column.visible = (_column as TabContainer).get_tab_count() > 0
	_column = null
