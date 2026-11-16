@tool
extends VBoxContainer
## One step of a request's to-do (todo_card.gd), as one compact row: a status circle, the
## step's short title and, behind its chevron, the detail the build reads (names, sizes,
## files). The circle, the title's first line and the buttons share one centre line, however
## many lines the title wraps to. The circle is empty before the step starts, an accent ring
## with a slow pulse while it is worked on (still with Reduce motion), a check once done and a
## dash when skipped; the step being worked on reads in the medium weight, done ones muted.
##
## While the to-do waits for Go the title and the detail edit in place, the step has its own
## remove, and Alt+Up / Alt+Down moves it up or down the list.

## The user asked to remove this step.
signal remove_requested(row: Control)
## Alt+Up (-1) or Alt+Down (+1) on the title: move this step.
signal move_requested(row: Control, by: int)
## The title was edited and left (Enter, or focus moved on).
signal committed(row: Control)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
## Progress -> the words a screen reader hears after the title.
const SAID := {"": "", "started": "in progress", "done": "done", "skipped": "skipped"}

## The step's place in the list (1-based), for the accessible names.
var number := 0
## The agent's number for this step (`from` on Go), 0 for a step the user added.
var origin := 0
## "", started, done or skipped.
var progress := ""
## The detail shows under the title.
var open := false
var editable := false
var mark: Mark
## A Label, or a borderless TextEdit that wraps like it while editable (Enter leaves it).
var title: Control
var chevron: Button
var remove: Button
## A wrapped caption Label, or a TextEdit while editable.
var detail: Control
var _detail_box: MarginContainer
var _title_box: MarginContainer
var _top: HBoxContainer
var _committed_text := ""


## The status circle, centred on the title's first line.
class Mark:
	extends Control
	var progress := "":
		set(value):
			progress = value
			queue_redraw()

	func _init() -> void:
		mouse_filter = Control.MOUSE_FILTER_IGNORE
		size_flags_vertical = Control.SIZE_SHRINK_BEGIN

	func _notification(what: int) -> void:
		# The live pulse starts once the circle is on screen (a tween needs the tree).
		if what == NOTIFICATION_ENTER_TREE and progress == "started":
			UI_MOTION.breathe(self)

	func _draw() -> void:
		var unit := UI_THEME.unit(self)
		var radius := roundf(unit * 6.5)
		var center := Vector2(size.x / 2.0, size.y / 2.0)
		var width := maxf(1.0, roundf(unit * 1.25))
		match progress:
			"done":
				draw_circle(center, radius, UI_THEME.color(self, "success"), true, -1.0, true)
				var ink := UI_THEME.color(self, "surface")
				var tick := PackedVector2Array(
					[
						center + Vector2(-0.45, 0.02) * radius,
						center + Vector2(-0.12, 0.34) * radius,
						center + Vector2(0.46, -0.3) * radius,
					]
				)
				draw_polyline(tick, ink, maxf(1.5, roundf(unit * 1.5)), true)
			"started":
				var accent := UI_THEME.color(self, "accent")
				draw_circle(center, radius - width / 2.0, accent, false, width, true)
				draw_circle(center, radius * 0.42, accent, true, -1.0, true)
			"skipped":
				var faint := UI_THEME.color(self, "text_faint")
				draw_circle(center, radius - width / 2.0, faint, false, width, true)
				var half := radius * 0.42
				draw_line(center - Vector2(half, 0), center + Vector2(half, 0), faint, width, true)
			_:
				var faint := UI_THEME.color(self, "text_faint")
				draw_circle(center, radius - width / 2.0, faint, false, width, true)


func _init(text := "", more := "", can_edit := false, from := 0) -> void:
	name = "Step"
	editable = can_edit
	origin = from
	theme_type_variation = "JoineryTight"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_top = HBoxContainer.new()
	_top.theme_type_variation = "JoineryRowTight"
	add_child(_top)
	mark = Mark.new()
	mark.name = "Mark"
	_top.add_child(mark)
	_title_box = MarginContainer.new()
	_title_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_top.add_child(_title_box)
	if editable:
		var field := TextEdit.new()
		field.name = "Words"
		field.text = text
		field.placeholder_text = "What to do"
		field.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
		field.scroll_fit_content_height = true
		field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		field.focus_exited.connect(_commit)
		field.gui_input.connect(_on_title_key)
		field.text_changed.connect(_keep_one_line)
		title = field
	else:
		var words := UI.label(text, "", true)
		words.name = "Words"
		words.mouse_filter = Control.MOUSE_FILTER_PASS
		words.gui_input.connect(_on_title_click)
		# From the top of its box: the lift puts its first line on the row's line.
		words.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
		title = words
	_committed_text = text
	_title_box.add_child(title)
	chevron = UI.icon_button("disclosure_closed", "Show the detail")
	chevron.name = "Open"
	chevron.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	chevron.pressed.connect(func() -> void: set_open(not open, true))
	UI_MOTION.press_feedback(chevron)
	_top.add_child(chevron)
	if editable:
		remove = UI.icon_button("close", "Remove")
		remove.name = "Remove"
		remove.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
		remove.pressed.connect(func() -> void: remove_requested.emit(self))
		UI_MOTION.press_feedback(remove)
		_top.add_child(remove)
	_detail_box = MarginContainer.new()
	_detail_box.name = "Detail"
	add_child(_detail_box)
	if editable:
		var box := TextEdit.new()
		box.name = "DetailWords"
		box.text = more
		box.placeholder_text = "Names, sizes, files: what building it needs"
		box.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
		box.scroll_fit_content_height = true
		box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		detail = box
	else:
		var words := UI.label(more, "JoineryCaption", true)
		words.name = "DetailWords"
		detail = words
	_detail_box.add_child(detail)
	_detail_box.visible = false
	if title is TextEdit:
		STYLE.on_theme_overrides(title, _flatten)
	STYLE.on_theme_overrides(self, _fit)
	_top.sort_children.connect(_align)
	sync_view()


## The step's title as it stands.
func title_text() -> String:
	if title is TextEdit:
		return (title as TextEdit).text.strip_edges()
	return (title as Label).text


func detail_text() -> String:
	if detail is TextEdit:
		return (detail as TextEdit).text.strip_edges()
	return (detail as Label).text


## Show the detail under the title ([param on]); [param animate] reveals it from the top.
func set_open(on: bool, animate := false) -> void:
	open = on
	_detail_box.visible = on
	if animate and on:
		UI_MOTION.reveal(_detail_box)
	sync_view()


## Move the step on: "", started, done or skipped.
func set_progress(value: String) -> void:
	if value == progress:
		return
	progress = value
	mark.progress = value
	if value == "started":
		UI_MOTION.breathe(mark)
	else:
		UI_MOTION.stop(mark)
	if value == "done":
		UI_MOTION.pulse(mark)
	sync_view()


## Put the title back as it was last left (a repeat the card refused).
func undo_title() -> void:
	if title is TextEdit:
		(title as TextEdit).text = _committed_text


## The title as it was last left.
func committed_title() -> String:
	return _committed_text


func focus_title() -> void:
	if title is TextEdit:
		(title as TextEdit).grab_focus()


## The edited title is left: says so through [signal committed] when it changed.
func commit() -> void:
	_commit()


func _commit() -> void:
	var now := title_text()
	if now == _committed_text:
		return
	committed.emit(self)
	_committed_text = title_text()
	sync_view()


func _on_title_key(event: InputEvent) -> void:
	var key := event as InputEventKey
	if key == null or not key.pressed:
		return
	if key.keycode in [KEY_ENTER, KEY_KP_ENTER]:
		# A title is one line: Enter leaves it.
		title.accept_event()
		title.release_focus()
		_commit()
	elif key.alt_pressed and key.keycode in [KEY_UP, KEY_DOWN]:
		move_requested.emit(self, -1 if key.keycode == KEY_UP else 1)
		title.accept_event()


## A pasted title loses its line breaks, and stops at 80 characters.
func _keep_one_line() -> void:
	var field := title as TextEdit
	var flat := " ".join(field.text.split("\n", false)).left(80)
	if flat != field.text:
		field.text = flat
		field.set_caret_column(flat.length())


func _on_title_click(event: InputEvent) -> void:
	var click := event as InputEventMouseButton
	if click != null and click.pressed and click.button_index == MOUSE_BUTTON_LEFT:
		if not detail_text().is_empty():
			set_open(not open, true)


## The title field draws no box until it has focus, like a flat LineEdit, with a LineEdit's
## insets so its words sit where a field's do.
func _flatten() -> void:
	var field := title as TextEdit
	var source := field.get_theme_stylebox("normal", "LineEdit")
	var flat := StyleBoxEmpty.new()
	for side: int in [SIDE_TOP, SIDE_BOTTOM]:
		flat.set_content_margin(side, source.get_content_margin(side) if source != null else 4.0)
	# Narrow sides, so the words start about where a read-only title's do.
	for side: int in [SIDE_LEFT, SIDE_RIGHT]:
		flat.set_content_margin(side, float(UI_THEME.space(field, 1)))
	field.add_theme_stylebox_override("normal", flat)
	field.add_theme_stylebox_override("read_only", flat)


## Every part of the row on the title's first line: the circle and the buttons are one line
## tall, and a wrapping title starts half the difference lower so its first line centres there.
func _fit() -> void:
	var column := roundf(UI_THEME.unit(self) * 16.0)
	mark.custom_minimum_size.x = column
	var indent := int(column) + _top.get_theme_constant("separation")
	_detail_box.add_theme_constant_override("margin_left", indent)
	_align()
	_ink()


## One line is as tall as the tallest one-line part (a button, or the title's field): the
## circle is that tall, and a wrapping title starts half the difference lower. Run as the row
## lays out, so it follows the buttons' own sizes.
func _align() -> void:
	var line_h := float(UI_THEME.control_h(self, "sm" if editable else "xs"))
	for part: Control in [chevron, remove]:
		if part != null and part.visible:
			line_h = maxf(line_h, part.get_combined_minimum_size().y)
	if mark.custom_minimum_size.y != line_h:
		mark.custom_minimum_size.y = line_h
	var lift := 0
	var font := title.get_theme_font("font")
	if font != null:
		# The capitals' middle on the line's middle (capitals stand about 0.7 of the ascent).
		var ascent := font.get_ascent(title.get_theme_font_size("font_size"))
		var inset := title.get_theme_stylebox("normal").get_margin(SIDE_TOP)
		if title is TextEdit:
			var spacing := (
				(title as TextEdit).get_line_height()
				- font.get_height(title.get_theme_font_size("font_size"))
			)
			inset += spacing / 2.0
		lift = maxi(0, roundi(line_h / 2.0 - ascent * 0.65 - inset))
	if _title_box.get_theme_constant("margin_top") != lift:
		_title_box.add_theme_constant_override("margin_top", lift)
	var right := int(chevron.get_combined_minimum_size().x) if remove == null else 0
	if _detail_box.get_theme_constant("margin_right") != right:
		_detail_box.add_theme_constant_override("margin_right", right)


## The title's weight and ink, the chevron and every accessible name, for the step as it is.
func sync_view() -> void:
	var words := title_text()
	var has_detail := not detail_text().is_empty()
	chevron.visible = editable or has_detail
	UI.bind_icon(chevron, "disclosure_open" if open else "disclosure_closed")
	chevron.tooltip_text = ("Hide" if open else "Show") + " the detail"
	chevron.accessibility_name = "%s the detail: %s" % ["Hide" if open else "Show", words]
	if remove != null:
		remove.accessibility_name = "Remove: " + words
	var said := str(SAID.get(progress, ""))
	title.accessibility_name = (
		"Step %d: %s%s" % [number, words, (", " + said) if not said.is_empty() else ""]
	)
	title.tooltip_text = ""
	detail.accessibility_name = "Detail of step %d" % number
	if is_inside_tree():
		_ink()


func _ink() -> void:
	if title is Label:
		var label := title as Label
		var role := "text_muted" if progress in ["done", "skipped"] else "text"
		label.add_theme_color_override("font_color", UI_THEME.color(self, role))
		if progress == "started":
			label.add_theme_font_override("font", UI_THEME.medium_font(self))
		else:
			label.remove_theme_font_override("font")
