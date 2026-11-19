@tool
extends VBoxContainer
## A folding group of asset_list.gd: a header button over the section title (a JoinerySection
## label lined up with row text as before), the row count while folded and a chevron, then the
## rows. Click, Enter or Space folds it; right-click, the Menu key, Shift+F10 or its ⋯ button
## (a [member movable] header in a list with row menus) asks for its menu; rows and (when
## [member movable]) other headers can be dropped on it, and the header lights up while they
## would land. The title can be typed in place (F2 on a movable header), and a question can be
## asked in place under the header ([method ask]). Up and Down move through headers and rows,
## Left folds and Right opens.

const UI_COMPONENTS := preload("ui_components.gd")
const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const STYLE := preload("sidebar_style.gd")
const ROW_DRAG := "joinery_asset_row"
const SECTION_DRAG := "joinery_asset_section"

var key := ""
## A header that can be dragged onto another (a user's own category).
var movable := false
var open := true
var header: Button
var title: Label
var count: Label
var chevron: TextureRect
var body: VBoxContainer
## The faint line shown while the section has no rows ("Drag assets here"); null for none.
var empty: Label
var rename: LineEdit
## The header's ⋯ button (a movable header in a list with row menus), or null.
var more: Button
## The question asked under the header ([method ask]), or null.
var question: Control
## A dragged row or header would land here: the header is lit.
var drop_lit := false
var _list: Control
var _text := ""


## Build the section for [param list] titled [param text]; see asset_list.add_section.
func setup(list: Control, text: String, id: String, empty_text: String, menu := false) -> void:
	_list = list
	_text = text
	key = id
	set_process(false)
	theme_type_variation = "JoineryTight"
	header = Button.new()
	header.name = "Header"
	header.theme_type_variation = "JoineryRowButton"
	header.focus_mode = Control.FOCUS_ALL
	header.action_mode = BaseButton.ACTION_MODE_BUTTON_RELEASE
	header.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.pressed.connect(func() -> void: toggle())
	header.gui_input.connect(_header_input)
	header.set_drag_forwarding(
		_drag_header,
		func(_at: Vector2, data: Variant) -> bool: return list.call("_hover_drop", data, key),
		func(_at: Vector2, data: Variant) -> void: list.call("drop", data, key)
	)
	header.theme_changed.connect(_light)
	add_child(header)
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	inset.mouse_filter = Control.MOUSE_FILTER_IGNORE
	inset.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	header.add_child(inset)
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	line.mouse_filter = Control.MOUSE_FILTER_IGNORE
	inset.add_child(line)
	title = UI_COMPONENTS.label(text, "JoinerySection")
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	title.mouse_filter = Control.MOUSE_FILTER_IGNORE
	line.add_child(title)
	rename = LineEdit.new()
	rename.accessibility_name = "Category name"
	rename.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	rename.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	rename.max_length = 60
	rename.hide()
	rename.text_submitted.connect(func(_typed: String) -> void: _end_rename(true))
	rename.focus_exited.connect(func() -> void: _end_rename(true))
	rename.gui_input.connect(_rename_input)
	line.add_child(rename)
	count = UI_COMPONENTS.label("", "JoineryNumeric")
	count.mouse_filter = Control.MOUSE_FILTER_IGNORE
	count.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	count.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	count.hide()
	line.add_child(count)
	chevron = TextureRect.new()
	chevron.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	chevron.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	chevron.mouse_filter = Control.MOUSE_FILTER_IGNORE
	UI_COMPONENTS.bind_icon(chevron, "disclosure_open", "texture")
	if menu:
		more = more_button(header, "More for %s" % text)
		more.pressed.connect(
			func() -> void: _list.emit_signal("section_menu_requested", key, below(more))
		)
		line.add_child(more)
	line.add_child(chevron)
	# The header is as tall as its title line, never shorter than a small control.
	var fit := func() -> void:
		var tall := maxf(inset.get_combined_minimum_size().y, UI_THEME.control_h(header, "sm"))
		header.custom_minimum_size.y = tall
		chevron.self_modulate = header.get_theme_color("text_muted", "Joinery")
	inset.minimum_size_changed.connect(fit)
	header.theme_changed.connect(fit)
	body = VBoxContainer.new()
	body.name = "Rows"
	body.theme_type_variation = "JoineryTight"
	add_child(body)
	if not empty_text.is_empty():
		empty = UI_COMPONENTS.label(empty_text, "JoineryFaint")
		empty.name = "Empty"
		var room := MarginContainer.new()
		room.theme_type_variation = "JoineryInset"
		room.add_child(empty)
		body.add_child(room)
		room.set_drag_forwarding(
			Callable(),
			func(_at: Vector2, data: Variant) -> bool: return list.call("_hover_drop", data, key),
			func(_at: Vector2, data: Variant) -> void: list.call("drop", data, key)
		)
	update_count()


## Rows in this section (the empty line excluded).
func row_count() -> int:
	return body.get_child_count() - (1 if empty != null else 0)


## The count shows while folded; the empty line only while open with no rows.
func update_count() -> void:
	var rows := row_count()
	count.text = "%d" % rows
	count.visible = not open
	if empty != null:
		empty.get_parent().visible = rows == 0
	var state := "expanded" if open else "collapsed, %d" % rows
	header.accessibility_name = "%s, %s" % [_text, state]
	header.tooltip_text = "" if open else "%s · %d" % [_text, rows]


## Open or fold the rows; folding never forgets them.
func set_open(value: bool) -> void:
	open = value
	body.visible = value
	UI_COMPONENTS.bind_icon(chevron, "disclosure_open" if value else "disclosure_closed", "texture")
	update_count()


func toggle() -> void:
	set_open(not open)
	if open:
		UI_MOTION.reveal(body)
		UI_MOTION.stagger(body.get_children())
	_list.emit_signal("section_toggled", key, open)


## Type a new title in place of the header's.
func begin_rename() -> void:
	rename.text = _text
	title.hide()
	rename.show()
	rename.grab_focus()
	rename.select_all()


func _end_rename(keep: bool) -> void:
	if not rename.visible:
		return
	rename.hide()
	title.show()
	var typed := rename.text.strip_edges() if keep else ""
	_list.emit_signal("section_renamed", key, typed)


func _rename_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and (event as InputEventKey).keycode == KEY_ESCAPE:
		rename.accept_event()
		_end_rename(false)


func _header_input(event: InputEvent) -> void:
	var at := menu_point(header, event)
	if at.x >= 0.0:
		header.accept_event()
		_list.emit_signal("section_menu_requested", key, at)
		return
	var press := event as InputEventKey
	if press == null or not press.pressed or press.is_command_or_control_pressed():
		return
	if press.keycode == KEY_F2 and movable and not press.echo:
		header.accept_event()
		begin_rename()
	elif press.keycode in [KEY_DELETE, KEY_BACKSPACE] and movable and not press.echo:
		header.accept_event()
		_list.emit_signal("section_delete_requested", key)
	elif (press.keycode == KEY_LEFT and open) or (press.keycode == KEY_RIGHT and not open):
		header.accept_event()
		toggle()
	elif _list.call("_move_focus", header, press):
		header.accept_event()


## Ask [param text] in place under the header, with [param yes] (runs [param on_yes]) and
## Cancel; Escape cancels too. Focus starts on [param yes] and goes back to the header after
## Cancel. Asking again replaces the question.
func ask(text: String, yes: String, on_yes: Callable) -> void:
	if question != null:
		question.queue_free()
	var room := MarginContainer.new()
	room.name = "Question"
	room.theme_type_variation = "JoineryInset"
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	room.add_child(column)
	var words := UI_COMPONENTS.label(text, "", true)
	words.name = "Text"
	column.add_child(words)
	var buttons := HBoxContainer.new()
	buttons.theme_type_variation = "JoineryRow"
	column.add_child(buttons)
	var confirm := UI_COMPONENTS.secondary(yes)
	confirm.name = "Yes"
	buttons.add_child(confirm)
	var cancel := UI_COMPONENTS.quiet("Cancel")
	cancel.name = "Cancel"
	buttons.add_child(cancel)
	add_child(room)
	move_child(room, header.get_index() + 1)
	question = room
	var close := func(answered: bool) -> void:
		if question != room:
			return
		question = null
		room.queue_free()
		if answered:
			on_yes.call()
		elif is_instance_valid(header) and header.is_inside_tree():
			header.grab_focus()
	confirm.pressed.connect(close.bind(true))
	cancel.pressed.connect(close.bind(false))
	var escape := func(event: InputEvent) -> void:
		var press := event as InputEventKey
		if press != null and press.pressed and press.keycode == KEY_ESCAPE:
			room.get_viewport().set_input_as_handled()
			close.call(false)
	confirm.gui_input.connect(escape)
	cancel.gui_input.connect(escape)
	UI_MOTION.reveal(room)
	confirm.grab_focus()


## Light the header while a drag would land on it ([param on]), or put it back.
func set_drop_lit(on: bool) -> void:
	if drop_lit == on:
		return
	drop_lit = on
	set_process(on)
	_light()


## The lit header: the accent tint with an accent hairline, on the header's own shape.
func _light() -> void:
	if header.has_meta("_joinery_lighting"):
		return
	header.set_meta("_joinery_lighting", true)
	header.begin_bulk_theme_override()
	for state: String in ["normal", "hover"]:
		header.remove_theme_stylebox_override(state)
	if drop_lit:
		var source := header.get_theme_stylebox("hover")
		var box := (
			source.duplicate() as StyleBoxFlat if source is StyleBoxFlat else StyleBoxFlat.new()
		)
		box.bg_color = header.get_theme_color("accent_tint", "Joinery")
		box.border_color = header.get_theme_color("accent", "Joinery")
		box.set_border_width_all(maxi(roundi(UI_THEME.unit(header)), 1))
		for state: String in ["normal", "hover"]:
			header.add_theme_stylebox_override(state, box)
	header.end_bulk_theme_override()
	header.remove_meta("_joinery_lighting")


## A drag that left the section without landing puts the light out.
func _process(_delta: float) -> void:
	if drop_lit and not get_global_rect().has_point(get_global_mouse_position()):
		set_drop_lit(false)


func _drag_header(_at: Vector2) -> Variant:
	if not movable:
		return null
	header.set_drag_preview(UI_COMPONENTS.chip(_text))
	return {"type": SECTION_DRAG, "key": key, "list": _list.get_instance_id()}


## Dragging a row carries its index; its chip follows the pointer.
static func drag_row(_at: Vector2, row: Button, index: int) -> Variant:
	row.set_drag_preview(UI_COMPONENTS.chip(str(row.get("text_value"))))
	var list := row.get_parent()
	while list != null and not list.has_method("drop"):
		list = list.get_parent()
	return {"type": ROW_DRAG, "index": index, "list": list.get_instance_id() if list else 0}


## A small ⋯ button for [param host] (a row or a header) that asks for its menu: shown while
## the host is pointed at or has keyboard focus, never focused itself (the Menu key and
## Shift+F10 reach the same menu from the keyboard). [param tooltip] doubles as its name.
static func more_button(host: Control, tooltip: String) -> Button:
	var button := UI_COMPONENTS.icon_button("menu", tooltip)
	button.name = "More"
	button.focus_mode = Control.FOCUS_NONE
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	button.mouse_filter = Control.MOUSE_FILTER_STOP
	STYLE.on_theme_overrides(
		button,
		func() -> void:
			var side := float(UI_THEME.control_h(button, "xs"))
			button.custom_minimum_size = Vector2(side, side)
			# Centred in the xs square, with the corner of a shape nested in the host's
			# padding: the host's radius less the gap around the button (rule 14).
			for state: String in ["normal", "hover", "pressed", "hover_pressed", "focus"]:
				button.remove_theme_stylebox_override(state)
				var source := button.get_theme_stylebox(state)
				if source == null:
					continue
				var box := source.duplicate() as StyleBox
				box.set_content_margin_all(0.0)
				if box is StyleBoxFlat:
					(box as StyleBoxFlat).set_corner_radius_all(UI_THEME.radius(button, "xs"))
				button.add_theme_stylebox_override(state, box)
	)
	button.self_modulate.a = 0.0
	var update := func() -> void:
		if not is_instance_valid(button) or not button.is_inside_tree():
			return
		var pointed := host.get_global_rect().has_point(host.get_global_mouse_position())
		var shown := pointed or host.has_focus()
		UI_MOTION.tween_to(button, button, "self_modulate:a", 1.0 if shown else 0.0, "fast")
	host.mouse_entered.connect(update)
	host.mouse_exited.connect(update)
	host.focus_entered.connect(update)
	host.focus_exited.connect(update)
	button.mouse_entered.connect(update)
	button.mouse_exited.connect(update)
	return button


## The screen point under [param control], where its menu opens.
static func below(control: Control) -> Vector2:
	return control.get_screen_position() + Vector2(0.0, control.size.y)


## Where a context menu opens for [param event] on [param control]: under the pointer for a
## right-click, under the control for the Menu key or Shift+F10; (-1, -1) for anything else.
static func menu_point(control: Control, event: InputEvent) -> Vector2:
	var mouse := event as InputEventMouseButton
	if mouse != null and mouse.pressed and mouse.button_index == MOUSE_BUTTON_RIGHT:
		return control.get_screen_position() + mouse.position
	var key_event := event as InputEventKey
	if key_event == null or not key_event.pressed or key_event.echo:
		return Vector2(-1, -1)
	var menu_key := key_event.keycode == KEY_MENU
	if menu_key or (key_event.keycode == KEY_F10 and key_event.shift_pressed):
		return below(control)
	return Vector2(-1, -1)
