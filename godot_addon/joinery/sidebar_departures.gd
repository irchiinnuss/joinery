@tool
extends VBoxContainer
## What the agent guessed and where it didn't do as asked, on a change card
## (submit_for_review's guessed and not_as_asked). One compact line, "Differs from your request
## · 1 guess" (review_glance.gd), opens to every line: departures first, each after the warning
## glyph, then guesses lined up beside them. Nothing to say hides it.

const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const GLANCE := preload("review_glance.gd")

var _toggle: Button
var _body: VBoxContainer


func _init() -> void:
	theme_type_variation = "JoineryTight"
	accessibility_name = "What was guessed and what isn't as asked"
	_toggle = UI.disclosure("")
	_toggle.name = "Departures"
	_toggle.tooltip_text = "Show what was guessed and what isn't as asked"
	UI.bind_height(_toggle, "xs")
	_toggle.toggled.connect(
		func(open: bool) -> void:
			_body.visible = open
			if open:
				UI_MOTION.reveal(_body)
	)
	add_child(_toggle)
	_body = VBoxContainer.new()
	_body.theme_type_variation = "JoineryTight"
	_body.hide()
	add_child(_body)
	hide()


## Show [param guessed] and [param not_as_asked] (lines of words), closed. Both empty hide it.
func set_lines(guessed: Array, not_as_asked: Array) -> void:
	for child: Node in _body.get_children():
		_body.remove_child(child)
		child.queue_free()
	for pair: Array in [[not_as_asked, "Not as asked: ", true], [guessed, "Guessed: ", false]]:
		for item: Variant in pair[0]:
			var line := str(item).strip_edges()
			if not line.is_empty():
				_add_row(str(pair[1]) + line, bool(pair[2]))
	var text := GLANCE.departures_line(guessed, not_as_asked)
	_toggle.text = text
	_toggle.set_pressed_no_signal(false)
	UI.bind_icon(_toggle, "disclosure_closed")
	_body.hide()
	visible = _body.get_child_count() > 0 and not text.is_empty()
	if visible:
		UI_MOTION.fade_in(self)


## The compact line ("" when hidden).
func line_text() -> String:
	return _toggle.text if visible else ""


## Each line it opens to, in order.
func lines() -> PackedStringArray:
	var found := PackedStringArray()
	for row: Node in _body.get_children():
		found.append((row.get_child(1) as Label).text)
	return found


## Open or close the lines, as the line's own toggle does.
func set_open(open: bool) -> void:
	if _toggle.button_pressed != open:
		_toggle.button_pressed = open


func is_open() -> bool:
	return _body.visible


func _add_row(text: String, warn: bool) -> void:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	_body.add_child(row)
	# The design system has no warning colour: the glyph's shape says it, in muted ink.
	var glyph := STYLE.glyph("text_muted")
	if warn:
		STYLE.set_glyph(glyph, "warning", "text_muted")
	row.add_child(glyph)
	var label := UI.label(text, "JoineryCaption", true)
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(label)
