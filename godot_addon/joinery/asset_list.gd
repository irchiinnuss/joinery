@tool
extends ScrollContainer
## A selectable list of rows grouped under section titles, for the asset browser and its
## app filter. Rows are real controls, so they can stagger in and carry a kind icon, a
## rolling count and a readiness dot. The item API mirrors ItemList (add_item, select,
## get_selected_items, item_selected), and indexes count rows only, never section titles.
## A section added with a key has a header that folds it (click, Enter or Space; a count shows
## while folded), a right-click or Menu-key menu, takes dropped rows and can be renamed in
## place. With [member row_menus] each row and movable header also has a ⋯ button for its menu.
## Up and Down move the keyboard focus through headers and rows (Home and End to either end);
## a header lights up while a dragged row or header would land on it. Whoever fills the list
## decides what each menu offers and what a drop means.

signal item_selected(index: int)
## The header of section [param key] was clicked: it is now [param open] or folded.
signal section_toggled(key: String, open: bool)
## Right-click or the Menu key on a section header, at [param at] (global position).
signal section_menu_requested(key: String, at: Vector2)
## Right-click or the Menu key on row [param index].
signal item_menu_requested(index: int, at: Vector2)
## Row [param index] was dropped on section [param key].
signal item_dropped(index: int, key: String)
## Section [param key]'s header was dropped on section [param target]'s.
signal section_dropped(key: String, target: String)
## The title typed for section [param key] (begin_rename); "" when cancelled.
signal section_renamed(key: String, title: String)
## Delete (or Backspace) on movable section [param key]'s header.
signal section_delete_requested(key: String)

const UI_COMPONENTS := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const SECTION_SCRIPT := preload("asset_list_section.gd")
const _SELECTED_STATES := ["pressed", "hover_pressed"]

## Number of rows (section titles excluded).
var item_count: int:
	get:
		return _rows.size()

## True to grow with the rows instead of scrolling (short lists such as the app filter).
var fit_content := false:
	set(value):
		fit_content = value
		vertical_scroll_mode = (
			ScrollContainer.SCROLL_MODE_DISABLED if value else ScrollContainer.SCROLL_MODE_AUTO
		)

## Rows can be dragged onto section headers ([signal item_dropped]).
var drag_rows := false
## Rows and movable headers carry a ⋯ button that asks for their menu.
var row_menus := false
## (data: Dictionary, key: String) -> bool: whether a dragged row or header may land on [param key].
var can_drop := Callable()

var _box: VBoxContainer
var _group: VBoxContainer
var _rows: Array[Button] = []
var _sections: Dictionary = {}
var _selected := -1
var _styling := false


## One row: a flat button with a left icon, a label, an optional count and a status dot.
class Row:
	extends Button
	var picture: TextureRect
	var title: Label
	var count: Label
	var dot: Control
	## The ⋯ button ([member row_menus]), or null.
	var more: Button
	var metadata: Variant = null
	var text_value := ""
	var section := ""


func _init() -> void:
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_box = VBoxContainer.new()
	_box.theme_type_variation = "JoineryStack"
	_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(_box)


func _ready() -> void:
	UI_MOTION.rubber_band(self)


## Remove every row and section title.
func clear() -> void:
	for child: Node in _box.get_children():
		_box.remove_child(child)
		child.queue_free()
	_rows.clear()
	_sections.clear()
	_group = null
	_selected = -1


## Start a new group under [param title] (a JoinerySection label lined up with row text).
## With a [param key] the header folds the group ([param open] says how it starts), and
## [param empty_text] is shown faintly while it has no rows; a [param movable] header can be
## dragged onto another ([signal section_dropped]).
func add_section(
	title: String, key := "", open := true, empty_text := "", movable := false
) -> void:
	if key.is_empty():
		_group = VBoxContainer.new()
		_group.theme_type_variation = "JoineryTight"
		_box.add_child(_group)
		var inset := MarginContainer.new()
		inset.theme_type_variation = "JoineryInset"
		inset.add_child(UI_COMPONENTS.label(title, "JoinerySection"))
		_group.add_child(inset)
		return
	var section: SECTION_SCRIPT = SECTION_SCRIPT.new()
	section.setup(self, title, key, empty_text, row_menus and movable)
	section.movable = movable
	_sections[key] = section
	_box.add_child(section)
	_group = section.body
	section.set_open(open)


## The keys of the folding sections, in list order.
func section_keys() -> PackedStringArray:
	return PackedStringArray(_sections.keys())


## Folding section [param key] (asset_list_section.gd), or null.
func get_section(key: String) -> SECTION_SCRIPT:
	return _sections.get(key) as SECTION_SCRIPT


## Whether section [param key] is open (true for a list without it).
func is_section_open(key: String) -> bool:
	return not _sections.has(key) or (_sections[key] as SECTION_SCRIPT).open


## The header button of section [param key], or null.
func section_header(key: String) -> Button:
	return (_sections[key] as SECTION_SCRIPT).header if _sections.has(key) else null


## Type a new title for section [param key] in place; [signal section_renamed] says what.
func begin_rename(key: String) -> void:
	if _sections.has(key):
		(_sections[key] as SECTION_SCRIPT).begin_rename()


## Add a row and return its index. [param icon_name] is a ui_theme icon ("" for none).
func add_item(text: String, icon_name := "") -> int:
	if _group == null:
		_group = VBoxContainer.new()
		_group.theme_type_variation = "JoineryTight"
		_box.add_child(_group)
	var row := Row.new()
	row.theme_type_variation = "JoineryRowButton"
	row.toggle_mode = true
	row.focus_mode = Control.FOCUS_ALL
	row.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.text_value = text
	row.accessibility_name = text
	if _group.get_parent() is SECTION_SCRIPT:
		row.section = (_group.get_parent() as SECTION_SCRIPT).key
	UI_COMPONENTS.bind_height(row, "md", false)
	UI_MOTION.press_feedback(row)
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	line.mouse_filter = Control.MOUSE_FILTER_IGNORE
	line.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	row.add_child(line)
	row.picture = TextureRect.new()
	row.picture.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	row.picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
	row.picture.visible = not icon_name.is_empty()
	if not icon_name.is_empty():
		UI_COMPONENTS.bind_icon(row.picture, icon_name, "texture")
	line.add_child(row.picture)
	row.title = UI_COMPONENTS.label(text)
	row.title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.title.mouse_filter = Control.MOUSE_FILTER_IGNORE
	line.add_child(row.title)
	row.count = UI_COMPONENTS.label("", "JoineryNumeric")
	row.count.mouse_filter = Control.MOUSE_FILTER_IGNORE
	# Never trimmed: a trimming label has no minimum width and the title would squeeze it out.
	row.count.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	row.count.hide()
	line.add_child(row.count)
	row.dot = UI_COMPONENTS.status_dot()
	row.dot.mouse_filter = Control.MOUSE_FILTER_IGNORE
	row.dot.hide()
	line.add_child(row.dot)
	var index := _rows.size()
	if row_menus:
		row.more = SECTION_SCRIPT.more_button(row, "More for %s" % text)
		row.more.pressed.connect(
			func() -> void:
				select(index)
				item_menu_requested.emit(index, SECTION_SCRIPT.below(row.more))
		)
		line.add_child(row.more)
	row.pressed.connect(_on_row_pressed.bind(index))
	row.theme_changed.connect(_style_row.bind(row))
	row.gui_input.connect(_row_input.bind(row, index))
	if drag_rows:
		row.set_drag_forwarding(
			SECTION_SCRIPT.drag_row.bind(row, index),
			func(_at: Vector2, data: Variant) -> bool: return _hover_drop(data, row.section),
			func(_at: Vector2, data: Variant) -> void: drop(data, row.section)
		)
	_group.add_child(row)
	if _group.get_parent() is SECTION_SCRIPT:
		(_group.get_parent() as SECTION_SCRIPT).update_count()
	_rows.append(row)
	return index


func get_item_text(index: int) -> String:
	return (_rows[index] as Row).text_value


func set_item_metadata(index: int, value: Variant) -> void:
	(_rows[index] as Row).metadata = value


func get_item_metadata(index: int) -> Variant:
	return (_rows[index] as Row).metadata


func set_item_tooltip(index: int, text: String) -> void:
	_rows[index].tooltip_text = text


## A disabled row cannot be chosen; its title turns faint.
func set_item_disabled(index: int, disabled: bool) -> void:
	var row := _rows[index] as Row
	row.disabled = disabled
	row.title.theme_type_variation = "JoineryFaint" if disabled else ""


## Show [param value] at the row's end as a rolling count (tabular digits).
func set_item_count(index: int, value: int) -> void:
	var row := _rows[index] as Row
	row.count.show()
	if row.count.text.is_empty():
		# First value: set it plainly (roll_number would start an empty tween for it).
		row.count.text = "%d" % value
		row.count.set_meta("_joinery_value", float(value))
		return
	UI_MOTION.roll_number(row.count, value, "%d")


## Show a readiness dot ("ready", "working", "attention", "off"); "" hides it.
func set_item_status(index: int, state: String) -> void:
	var row := _rows[index] as Row
	row.dot.visible = not state.is_empty()
	if not state.is_empty():
		row.dot.set("state", state)


## Select a row without emitting [signal item_selected], like ItemList.select.
func select(index: int, _single := true) -> void:
	_selected = index if index >= 0 and index < _rows.size() else -1
	for position: int in _rows.size():
		_rows[position].set_pressed_no_signal(position == _selected)
	if _selected >= 0 and is_inside_tree():
		_reveal.call_deferred(_rows[_selected])


func deselect_all() -> void:
	select(-1)


func get_selected_items() -> PackedInt32Array:
	return PackedInt32Array([_selected] if _selected >= 0 else [])


## Bring every row and section title in one after another (motion rule 3).
func stagger() -> void:
	var nodes: Array = []
	for group: Node in _box.get_children():
		if group is SECTION_SCRIPT:
			nodes.append((group as SECTION_SCRIPT).header)
			if (group as SECTION_SCRIPT).open:
				nodes.append_array((group as SECTION_SCRIPT).body.get_children())
		else:
			nodes.append_array(group.get_children())
	UI_MOTION.stagger(nodes)


## Whether [param data] (a dragged row or header) may land on section [param key].
func _accepts(data: Variant, key: String) -> bool:
	if not data is Dictionary or key.is_empty() or not can_drop.is_valid():
		return false
	if (data as Dictionary).get("list") != get_instance_id():
		return false
	return bool(can_drop.call(data, key))


## Whether [param data] may land on [param key], lighting that section's header while it may.
func _hover_drop(data: Variant, key: String) -> bool:
	var ok := _accepts(data, key)
	for id: String in _sections:
		(_sections[id] as SECTION_SCRIPT).set_drop_lit(ok and id == key)
	return ok


func _notification(what: int) -> void:
	if what == NOTIFICATION_DRAG_END:
		_put_lights_out()


func _put_lights_out() -> void:
	for id: String in _sections:
		(_sections[id] as SECTION_SCRIPT).set_drop_lit(false)


## Land a dragged row or header on section [param key].
func drop(data: Variant, key: String) -> void:
	_put_lights_out()
	if not _accepts(data, key):
		return
	var payload := data as Dictionary
	if payload.get("type") == SECTION_SCRIPT.ROW_DRAG:
		item_dropped.emit(int(payload["index"]), key)
	elif payload.get("type") == SECTION_SCRIPT.SECTION_DRAG:
		section_dropped.emit(str(payload["key"]), key)


## Right-click, or the Menu key (Shift+F10) while focused, asks for the row's menu.
func _row_input(event: InputEvent, row: Row, index: int) -> void:
	var at := SECTION_SCRIPT.menu_point(row, event)
	if at.x >= 0.0:
		row.accept_event()
		select(index)
		item_menu_requested.emit(index, at)
		return
	if _move_focus(row, event as InputEventKey):
		row.accept_event()


## The headers and rows the keyboard moves through, in list order (folded rows left out).
func _focus_stops() -> Array[Control]:
	var stops: Array[Control] = []
	for group: Node in _box.get_children():
		if group is SECTION_SCRIPT:
			var section := group as SECTION_SCRIPT
			stops.append(section.header)
			if not section.open:
				continue
			for child: Node in section.body.get_children():
				if child is Row:
					stops.append(child as Control)
		else:
			for child: Node in group.get_children():
				if child is Row:
					stops.append(child as Control)
	return stops.filter(
		func(stop: Control) -> bool: return stop.visible and stop.focus_mode != FOCUS_NONE
	)


## Up, Down, Home or End on [param from] moves the focus to the next header or row; true when
## [param press] was one of them.
func _move_focus(from: Control, press: InputEventKey) -> bool:
	if press == null or not press.pressed or press.is_command_or_control_pressed():
		return false
	var steps := {KEY_UP: -1, KEY_DOWN: 1, KEY_HOME: -100000, KEY_END: 100000}
	if not steps.has(press.keycode) or press.shift_pressed or press.alt_pressed:
		return false
	var stops := _focus_stops()
	var at := stops.find(from)
	if at < 0 or stops.is_empty():
		return false
	var target := clampi(at + int(steps[press.keycode]), 0, stops.size() - 1)
	stops[target].grab_focus()
	ensure_control_visible(stops[target])
	return true


## Scroll [param row] into view, unless the list was rebuilt before this deferred call ran.
func _reveal(row: Variant) -> void:
	if not is_instance_valid(row):
		return
	var control := row as Control
	if not control.is_queued_for_deletion() and is_ancestor_of(control):
		ensure_control_visible(control)


func _on_row_pressed(index: int) -> void:
	select(index)
	item_selected.emit(index)


## Selected rows take the accent tint (spec A1). The fill is read from the live theme each time
## it changes, so it follows the theme and accent; the guard stops the override from
## re-entering this handler through its own theme notification.
func _style_row(row: Row) -> void:
	if _styling or not row.is_inside_tree():
		return
	_styling = true
	row.begin_bulk_theme_override()
	for state: String in _SELECTED_STATES:
		row.remove_theme_stylebox_override(state)
	row.end_bulk_theme_override()
	var tint := row.get_theme_color("accent_tint", "Joinery")
	row.begin_bulk_theme_override()
	for state: String in _SELECTED_STATES:
		var box := row.get_theme_stylebox(state).duplicate() as StyleBox
		if box is StyleBoxFlat:
			(box as StyleBoxFlat).bg_color = tint
		row.add_theme_stylebox_override(state, box)
	row.end_bulk_theme_override()
	var inner := row.get_theme_stylebox("normal")
	var line := row.get_child(0) as Control
	line.offset_left = inner.get_margin(SIDE_LEFT)
	line.offset_right = -inner.get_margin(SIDE_RIGHT)
	row.picture.self_modulate = row.get_theme_color("text_muted", "Joinery")
	var side := float(row.get_theme_constant("icon_max_width"))
	row.picture.custom_minimum_size = Vector2(side, side)
	_styling = false
