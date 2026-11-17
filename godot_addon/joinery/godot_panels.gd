@tool
extends Node
## Godot's own panels, opened from the rail, in Joinery's layout.
##
## Joinery does not rebuild what Godot already has: Scene, Inspector, FileSystem, Output,
## Audio, Animation, Debugger and the Shader Editor are Godot's own docks, and the rail lists
## them with Godot's icons. The viewport and the Journeyman sidebar are the main surfaces, so
## the docks start hidden and show on demand:
##
## - Scene, Inspector and FileSystem sit in the side columns left of the viewport. A rail icon
##   opens its dock there and closes it again, the way the dock's own Close does, so Godot
##   hides an empty column and the viewport gets its width. Which ones are open is an editor
##   preference (SIDE_SETTING): opened once, a dock stays open in every project.
## - Every other dock opens in the bottom panel, alone: the panel has no tab strip of its own,
##   only the shown dock's title, ⋮ and ×. The icon again, ×, or Esc (in the panel) hides it.
##   Godot still opens a dock there by itself when it needs to (the debugger on Run, the
##   animation editor for an AnimationPlayer); Output never does, its icon shows a dot.
## - Both side columns, the viewport and the sidebar need more room than a small window has:
##   rather than push the title bar's and the sidebar's buttons past the window's right edge,
##   the column opened first closes (make_room), and the one just opened stays.
##
## Scenes, right after FileSystem, is not a dock: it shows Godot's own Quick Open Scene picker
## (scene_finder.gd), every scene in the project with the ones picked before first.
##
## Ctrl+Alt+I (Cmd+Option+I on macOS) shows or hides Scene and Inspector together, and Inspect
## in the 2D view's right-click menu shows the Inspector. Godot's classic view is never
## touched: the addon only calls this in Joinery's layout.

## A dock opened, closed, shown or hidden, or Output's mark changed: the rail updates.
signal changed

## Editor setting: the side docks the user has open, by name. Absent (a fresh editor, or one
## from before the docks could hide) means none, so existing layouts lose the column once.
const SIDE_SETTING := "joinery/layout/side_panels"
## The side columns' dock slot stacks, left to right (the fork's DockVSplitLeftL and -R).
const SIDE_SPLITS := ["DockVSplitLeftL", "DockVSplitLeftR"]
## Godot's panels the rail always lists, in this order, by dock name.
const CORE := [
	"Scene", "Inspector", "FileSystem", "Output", "Debugger", "Audio", "Animation", "Shader Editor"
]
## The rail's entry for Godot's Quick Open Scene picker, listed after FileSystem.
const SCENES := "Scenes"
## The side docks Ctrl+Alt+I shows and hides together.
const COLUMN := ["Scene", "Inspector"]
## Joinery's own docks, listed with Joinery's pages instead: the Journeyman sidebar, then the
## Review panel in the bottom panel.
const OWN := ["Journeyman", "Review"]
## The sidebar's dock: its icon shows and hides the whole sidebar (sidebar_toggle.gd), and stays
## on the rail while the sidebar is hidden, so the sidebar can come back without the menu.
const SIDEBAR := "Journeyman"
## Joinery's glyph (ui_theme icon) for each of its own docks, which have no Godot icon.
const GLYPHS := {"Journeyman": "chats", "Review": "check"}
## Marks the bottom panel while it shows one tab at a time (review_tab.gd leaves it be).
const ONE_TAB := "joinery_one_tab"
## Seconds between looks at Output's mark.
const MARK_INTERVAL := 0.5
## How long after its first file scan the editor may still reopen docks on its own (ms).
const SETTLE_MS := 20000
## The longest start-up waits for that first scan before settling anyway (ms).
const SCAN_WAIT_MS := 180000
const SCENE_FINDER := preload("scene_finder.gd")

## Opens Godot's Quick Open Scene picker for the Scenes entry; tests replace its editor.
var finder: RefCounted = SCENE_FINDER.new()

var _base: Control
var _settings: EditorSettings
var _bottom: TabContainer
var _close: Button
var _shortcut := Shortcut.new()
var _menu: EditorContextMenuPlugin
var _rail: Control
## True once the user opened a panel, so the start-up passes leave their choice alone.
var _touched := false
## When attach ran (ms). Until SETTLE_MS later, docks the editor itself reopens (its default
## layout on a slow first launch) are closed again rather than saved as the user's choice.
var _attached_at := -1
## When the editor's first file scan ended (ms), or -1 before; settling counts from it,
## since a slow machine's first launch loads its layout only after that scan.
var _scanned_at := -1
## What the rail last showed (marks and shown panels), to update it only on a change.
var _state: Array = []
var _since_mark := 0.0
## Every dock by id, from the last look at the editor (_all_docks).
var _docks: Dictionary = {}
## The side columns (SIDE_SPLITS) that show, in the order they opened: the latest last.
var _columns: Array[String] = []
## The editor's content below its base: the title bar and every column.
var _content: Control


## Shows the Inspector for the node under the pointer, from the 2D view's right-click menu.
class InspectMenu:
	extends EditorContextMenuPlugin
	var panels: Node

	func _popup_menu(paths: PackedStringArray) -> void:
		if not paths.is_empty():
			add_context_menu_item("Inspect", panels.inspect)


func _init() -> void:
	name = "JoineryGodotPanels"
	var key := InputEventKey.new()
	key.keycode = KEY_I
	key.alt_pressed = true
	key.command_or_control_autoremap = true
	_shortcut.events = [key]
	set_process(false)
	set_process_shortcut_input(false)


## Hide the bottom panel's tab strip, open the side docks the preference names and close the
## rest. Call once the editor has loaded its layout.
func apply(base: Control, settings: EditorSettings) -> void:
	_base = base
	_settings = settings
	for node: Node in base.find_children("*", "EditorBottomPanel", true, false):
		_bottom = node as TabContainer
	reapply()
	if _bottom != null:
		_bottom.tab_changed.connect(_on_bottom_changed)
		_bottom.child_order_changed.connect(_on_docks_moved)
		_add_close_button()
		_on_bottom_changed(-1)
	for split_name: String in SIDE_SPLITS:
		var split := base.find_child(split_name, true, false)
		for slot: Node in [] if split == null else split.get_children():
			if slot is TabContainer:
				slot.child_order_changed.connect(_on_docks_moved)
				(slot as TabContainer).tab_changed.connect(_on_side_tab)
	for bar: Node in base.find_children("*", "EditorTitleBar", true, false):
		_content = bar.get_parent() as Control
	base.resized.connect(make_room, CONNECT_DEFERRED)
	set_process(true)
	set_process_shortcut_input(true)


## Open the side docks the preference names, close the rest and collapse the bottom panel.
## Again whenever the editor loads a saved layout (EditorPlugin._set_window_layout), which
## reopens the docks it lists.
func reapply(bottom := true) -> void:
	if _base == null:
		return
	var wanted: PackedStringArray = (
		_settings.get_setting(SIDE_SETTING) if _settings.has_setting(SIDE_SETTING) else []
	)
	for dock: Control in _side_docks():
		if not wanted.has(_key(dock)):
			dock.call("close")
	for id: String in wanted:
		var dock := _dock(id)
		if dock != null and not _is_open(dock):
			dock.call("open")
	if bottom and _bottom != null:
		_bottom.set_meta(ONE_TAB, true)
		_bottom.current_tab = -1
		_on_bottom_changed(-1)


## Wire the panels to [param rail] (null without the rail), the 2D view's menu and the
## Joinery menu on [param plugin], and apply the saved preference.
func attach(plugin: EditorPlugin, rail: Control) -> void:
	plugin.add_child(self)
	_attached_at = Time.get_ticks_msec()
	apply(EditorInterface.get_base_control(), EditorInterface.get_editor_settings())
	# The editor loads the saved layout (or Joinery's default) after its first file scan,
	# which reopens the docks it lists: apply the preference again then.
	EditorInterface.get_resource_filesystem().sources_changed.connect(
		func(_exist: bool) -> void: reapply.call_deferred(), CONNECT_ONE_SHOT
	)
	# A first launch has no saved layout and its scan may report no change, so neither
	# hook fires after Godot's default layout reopens the docks, and a slow machine gets
	# there late. Apply again when the first scan ends, and once a second while the editor
	# settles (up to 15 s), unless the user has opened a panel meanwhile. These passes only
	# close side docks: the bottom panel may already hold an opening review.
	EditorInterface.get_resource_filesystem().filesystem_changed.connect(
		func() -> void:
			_scanned_at = Time.get_ticks_msec()
			if not _touched:
				reapply.call_deferred(false),
		CONNECT_ONE_SHOT
	)
	for delay: int in range(1, 16):
		get_tree().create_timer(float(delay)).timeout.connect(
			func() -> void:
				if not _touched:
					reapply(false)
		)
	add_inspect_menu(plugin)
	plugin.call("add_command", "Show or Hide Scene and Inspector", toggle_column)
	_rail = rail
	if rail != null:
		rail.connect("panel_toggled", toggle)
		changed.connect(_update_rail)
		_update_rail()


func detach(plugin: EditorPlugin) -> void:
	remove_inspect_menu(plugin)
	restore()
	queue_free()


## Put everything back as Godot had it: the bottom panel's tab strip and every tab.
func restore() -> void:
	if is_instance_valid(_bottom):
		var bar := _bottom.get_tab_bar()
		for index: int in bar.tab_count:
			bar.set_tab_hidden(index, false)
		_bottom.tabs_visible = true
		_bottom.remove_meta(ONE_TAB)
	if is_instance_valid(_close):
		_close.queue_free()
	_close = null


## Add "Inspect" to the 2D view's right-click menu on [param plugin].
func add_inspect_menu(plugin: EditorPlugin) -> void:
	_menu = InspectMenu.new()
	_menu.panels = self
	plugin.add_context_menu_plugin(EditorContextMenuPlugin.CONTEXT_SLOT_2D_EDITOR, _menu)


func remove_inspect_menu(plugin: EditorPlugin) -> void:
	if _menu != null:
		plugin.remove_context_menu_plugin(_menu)
	_menu = null


## The panels for the rail, in order: {"id", "title", "icon" (editor icon name), "own"}.
## Godot's CORE panels that exist, then any other dock open now (a context dock Godot opened,
## one the user opened from Editor > Editor Docks), then Joinery's own (own: true).
func panels() -> Array[Dictionary]:
	var out: Array[Dictionary] = []
	if _base == null:
		return out
	var docks := _docks if not _docks.is_empty() else _all_docks()
	for id: String in CORE:
		if docks.has(id):
			out.append(_entry(docks[id], false))
		if id == "FileSystem":
			out.append(_scenes_entry())
	var placed := _side_docks()
	if _bottom != null:
		for index: int in _bottom.get_tab_count():
			placed.append(_bottom.get_tab_control(index))
	for dock: Control in placed:
		var id := _key(dock)
		if not CORE.has(id) and not OWN.has(id):
			out.append(_entry(dock, false))
	for id: String in OWN:
		if docks.has(id):
			out.append(_entry(docks[id], true))
	return out


## True while panel [param id] shows on screen.
func is_shown(id: String) -> bool:
	if id == SCENES:
		return finder.call("picker_open")
	var dock := _dock(id)
	return dock != null and dock.is_visible_in_tree()


## Output's mark: "error" or "warning" while Godot marks its tab, else "".
func output_mark() -> String:
	var dock := _dock("Output")
	var icon := dock.get("dock_icon") as Texture2D if dock != null else null
	if icon == null:
		return ""
	return "error" if icon == dock.get_theme_icon("Error", "EditorIcons") else "warning"


## Show panel [param id], or hide it when it already shows.
func toggle(id: String) -> void:
	if id == SIDEBAR:
		var sidebar := get_parent().get_node_or_null("JoinerySidebarToggle")
		if sidebar != null:
			sidebar.call("toggle")
			changed.emit()
		return
	if id == SCENES:
		finder.call("pick")
		changed.emit()
		return
	var dock := _dock(id)
	if dock == null:
		return
	if dock.is_visible_in_tree():
		hide_panel(id)
	else:
		show_panel(id)


## Open panel [param id] where it lives and bring it to the front.
func show_panel(id: String) -> void:
	var dock := _dock(id)
	if dock == null:
		return
	_touched = true
	if _bottom != null and dock.get_parent() == _bottom:
		_bottom.current_tab = _bottom.get_tab_idx_from_control(dock)
	else:
		dock.call("make_visible")
		var column := _column_of(dock)
		_columns.erase(column)
		_columns.append(column)
		make_room.call_deferred()
	_remember_side()
	changed.emit()


## Hide panel [param id]: a bottom dock collapses the panel, a side dock closes.
func hide_panel(id: String) -> void:
	var dock := _dock(id)
	if dock == null:
		return
	_touched = true
	if _bottom != null and dock.get_parent() == _bottom:
		if _bottom.current_tab == _bottom.get_tab_idx_from_control(dock):
			_bottom.current_tab = -1
	elif _is_open(dock):
		dock.call("close")
	_remember_side()
	changed.emit()


## Show Scene and Inspector together, or hide both when either shows (Ctrl+Alt+I).
func toggle_column() -> void:
	var shown := false
	for id: String in COLUMN:
		shown = shown or is_shown(id)
	for id: String in COLUMN:
		if shown:
			hide_panel(id)
		else:
			show_panel(id)


## "Inspect" on [param nodes]: select the first and show the Inspector with its properties.
func inspect(nodes: Array = []) -> void:
	if not nodes.is_empty() and nodes[0] is Node:
		var selection := EditorInterface.get_selection()
		selection.clear()
		selection.add_node(nodes[0] as Node)
	show_panel("Inspector")


func shortcut() -> Shortcut:
	return _shortcut


func _shortcut_input(event: InputEvent) -> void:
	if not event.is_pressed() or event.is_echo():
		return
	if _shortcut.matches_event(event):
		toggle_column()
		get_viewport().set_input_as_handled()
	elif event.is_action("ui_cancel") and _bottom_has_attention():
		_bottom.current_tab = -1
		get_viewport().set_input_as_handled()


## Output's mark, and panels Godot showed or hid by itself (Focus mode, the debugger on Run).
func _process(delta: float) -> void:
	_since_mark += delta
	if _since_mark >= MARK_INTERVAL:
		_since_mark = 0.0
		_update_rail()
		make_room()


## How far the editor's content reaches past the window's right edge, in pixels: what its
## columns need at their narrowest beyond the window's width, or 0 when everything fits.
func overflow() -> float:
	if not is_instance_valid(_content) or not is_instance_valid(_base):
		return 0.0
	var need := (
		_content.get_combined_minimum_size().x + _content.offset_left - _content.offset_right
	)
	return maxf(0.0, need - _base.size.x)


## While the content is wider than the window, close the side column that opened first, so
## the one just opened stays and nothing is pushed past the window's edge. Only a column is
## closed: the viewport and the sidebar keep their place.
func make_room() -> void:
	_track_columns()
	while overflow() > 0.5 and _columns.size() > 1:
		var oldest: String = _columns.pop_front()
		for dock: Control in _side_docks():
			if _column_of(dock) == oldest and _is_open(dock):
				hide_panel(_key(dock))


## Note a side column that shows now and was not shown before (opened by Godot, a layout, or a
## dock dragged there), and forget one that no longer shows.
func _track_columns() -> void:
	if not is_instance_valid(_base):
		return
	for split_name: String in SIDE_SPLITS:
		var split := _base.find_child(split_name, true, false) as Control
		var shown := split != null and split.is_visible_in_tree()
		if shown and not _columns.has(split_name):
			_columns.append(split_name)
		elif not shown:
			_columns.erase(split_name)


## The side column (a SIDE_SPLITS name) [param dock] sits in, or "".
static func _column_of(dock: Control) -> String:
	var node := dock.get_parent()
	while node != null:
		if SIDE_SPLITS.has(str(node.name)):
			return str(node.name)
		node = node.get_parent()
	return ""


func _update_rail() -> void:
	# A dock Godot moved or retitled can bring its tab back; show one tab again.
	if is_instance_valid(_bottom) and _bottom.has_meta(ONE_TAB):
		var bar := _bottom.get_tab_bar()
		for index: int in bar.tab_count:
			if bar.is_tab_hidden(index) == (index == _bottom.current_tab):
				_on_bottom_changed(-1)
				return
	if not is_instance_valid(_rail):
		return
	_docks = _all_docks()
	var list := panels()
	var shown := {}
	for panel: Dictionary in list:
		shown[panel["id"]] = is_shown(str(panel["id"]))
	var state: Array = [list, shown, output_mark()]
	if state == _state:
		return
	_state = state
	_rail.call("set_panels", list)
	_rail.call("set_panel_states", shown, state[2])


## The bottom panel shows only its current dock, with that dock's tab as its title; collapsed,
## it takes no room at all.
func _on_bottom_changed(_tab: int) -> void:
	if not is_instance_valid(_bottom):
		return
	var current := _bottom.current_tab
	# On the tab bar itself: TabContainer.set_tab_hidden also hides the dock, which would
	# collapse the panel again.
	var bar := _bottom.get_tab_bar()
	for index: int in bar.tab_count:
		bar.set_tab_hidden(index, index != current)
	_bottom.tabs_visible = current >= 0
	if is_instance_valid(_close):
		_close.visible = current >= 0
		var title := _bottom.get_tab_title(current) if current >= 0 else ""
		_close.tooltip_text = "Hide %s (Esc)" % title
		_close.accessibility_name = "Hide " + title
	changed.emit()


func _on_side_tab(_tab: int) -> void:
	changed.emit()


func _on_docks_moved() -> void:
	_on_bottom_changed.call_deferred(-1)
	if _settling():
		reapply.call_deferred(false)
	else:
		_remember_side.call_deferred()


## Still starting up: the user has not opened or hidden a panel and the editor may yet load
## its layout, so a dock it moves is not a choice to remember.
func _settling() -> bool:
	if _touched or _attached_at < 0:
		return false
	var now := Time.get_ticks_msec()
	if _scanned_at < 0:
		return now - _attached_at < SCAN_WAIT_MS
	return now - maxi(_attached_at, _scanned_at) < SETTLE_MS


## The × at the right end of the bottom panel's title row.
func _add_close_button() -> void:
	_close = Button.new()
	_close.name = "JoineryHidePanel"
	_close.flat = true
	_close.focus_mode = Control.FOCUS_ACCESSIBILITY
	_close.icon = _bottom.get_theme_icon("Close", "EditorIcons")
	_close.pressed.connect(func() -> void: _bottom.current_tab = -1)
	# On the tab bar: every child of the panel itself, internal or not, becomes a tab.
	_bottom.get_tab_bar().add_child(_close)
	_bottom.get_tab_bar().resized.connect(_place_close)
	_place_close.call_deferred()


## At the right end of the tab strip, left of the panel's ⋮ menu button.
func _place_close() -> void:
	if not is_instance_valid(_close) or not is_instance_valid(_bottom):
		return
	var bar := _bottom.get_tab_bar()
	var side := bar.size.y
	_close.size = Vector2(side, side)
	_close.position = Vector2(bar.size.x - side, 0.0)


func _bottom_has_attention() -> bool:
	if not is_instance_valid(_bottom) or _bottom.current_tab < 0:
		return false
	var focused := _bottom.get_viewport().gui_get_focus_owner()
	var inside := focused != null and _bottom.is_ancestor_of(focused)
	return inside or _bottom.get_global_rect().has_point(_bottom.get_global_mouse_position())


## Save which side docks are open, as the editor preference.
func _remember_side() -> void:
	if _settings == null:
		return
	var open := PackedStringArray()
	for dock: Control in _side_docks():
		open.append(_key(dock))
	var saved: Variant = (
		_settings.get_setting(SIDE_SETTING) if _settings.has_setting(SIDE_SETTING) else null
	)
	if saved == null or PackedStringArray(saved) != open:
		_settings.set_setting(SIDE_SETTING, open)
		changed.emit()


func _entry(dock: Control, own: bool) -> Dictionary:
	var title := str(dock.get("title"))
	return {
		"id": _key(dock),
		"title": _key(dock) if title.is_empty() else title,
		"icon": "" if own else str(dock.get("icon_name")),
		"glyph": str(GLYPHS.get(_key(dock), "check")),
		"own": own,
	}


## The Scenes entry: Godot's search icon, and its shortcut in the tooltip.
func _scenes_entry() -> Dictionary:
	var keys := str(finder.call("shortcut_text"))
	return {
		"id": SCENES,
		"title": SCENES,
		"icon": "Search",
		"glyph": "check",
		"own": false,
		"tip": "Open a scene" + (" (%s)" % keys if not keys.is_empty() else ""),
	}


## Every dock in the side columns.
func _side_docks() -> Array[Control]:
	var docks: Array[Control] = []
	for split_name: String in SIDE_SPLITS:
		var split := _base.find_child(split_name, true, false)
		for slot: Node in [] if split == null else split.get_children():
			if slot is TabContainer:
				for child: Node in slot.get_children():
					if child.is_class("EditorDock"):
						docks.append(child as Control)
	return docks


## Every dock by name, open or closed. Closed docks are parked outside every slot.
func _all_docks() -> Dictionary:
	var docks := {}
	for node: Node in _base.find_children("*", "EditorDock", true, false):
		docks[_key(node as Control)] = node
	return docks


## A dock's id: its layout key ("FileSystem", "Debugger"), else its name ("Scene"), else, for a
## dock a plugin added (Review), its title.
static func _key(dock: Control) -> String:
	var key := str(dock.get("layout_key"))
	if key.is_empty():
		key = str(dock.name)
	if key.begins_with("@"):
		key = str(dock.get("title"))
	return key


func _dock(id: String) -> Control:
	if not is_instance_valid(_docks.get(id)):
		_docks = _all_docks()
	return _docks.get(id) as Control


## Open: in a dock slot's tabs, or floating in its own window.
static func _is_open(dock: Control) -> bool:
	var parent := dock.get_parent()
	return parent is TabContainer or (parent != null and parent.get_parent() is Window)
