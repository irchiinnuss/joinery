@tool
extends PanelContainer
## "/cut": what is left to finish the game, at a glance. One headline ("12 to polish · 3 high ·
## 2 cuts proposed"), the sources it read in one line, then the polish list by priority (each
## row's glyph says how much it matters, its caption where it came from), the proposed cuts
## with their reasons, and, only for a game made to sell, the ship estimate.
##
## Every cut is a ticked box the user can untick; features a Keep line covers are named apart
## and can never be ticked. "Write polish list" sends one change to Review (design/polish.md
## and the ticked cuts moved to the brief's Out of scope); nothing is deleted here or there.

signal write_requested(cuts: Array)
signal recheck_requested
signal close_pressed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const BLOCKS := preload("ui_blocks.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
## Polish rows shown before "Show all".
const FIRST_ROWS := 3
## Of the window's height, the most the card's list takes before it scrolls.
const MAX_LIST_SHARE := 0.4
const REASONS := {
	"broken": "Built but broken",
	"scope": "Promised but not built",
	"risk": "Not built yet, and the worst findings point at it",
}
const SOURCES := {
	"errors": "console errors",
	"playtest": "playtest",
	"brief": "brief",
	"design": "design check",
	"access": "accessibility",
	"perf": "performance",
	"tidy": "tidy",
}

## gathering, ready, sent (the change waits in Review) or failed.
var state := "gathering"
var report: Dictionary = {}
var title_label: Label
var again: Button
var close: Button
var write: Button
var _dots: Control
var _status: TextureRect
var _step: Label
var _scroll: ScrollContainer
var _list: VBoxContainer
var _more: Button
## Proposed cut id -> its CheckBox.
var _boxes: Dictionary = {}


func _init() -> void:
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Polish list"
	STYLE.task_card(self)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	add_child(column)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	column.add_child(head)
	title_label = STYLE.card_title("Gathering what's left to finish")
	title_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	title_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	head.add_child(title_label)
	again = _icon("reload", "Gather again")
	again.pressed.connect(func() -> void: recheck_requested.emit())
	again.hide()
	head.add_child(again)
	close = _icon("close", "Dismiss")
	close.pressed.connect(func() -> void: close_pressed.emit())
	head.add_child(close)
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRowTight"
	UI.bind_height(line, "xs", false)
	column.add_child(line)
	_dots = INDICATOR.new()
	_dots.set("running", true)
	line.add_child(_dots)
	_status = UI.status_icon("done")
	_status.hide()
	line.add_child(_status)
	_step = UI.label("Reading every check's last findings…", "JoineryCaption")
	_step.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_step.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_step.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_step.mouse_filter = Control.MOUSE_FILTER_PASS
	line.add_child(_step)
	_scroll = ScrollContainer.new()
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_scroll.hide()
	column.add_child(_scroll)
	_list = VBoxContainer.new()
	_list.theme_type_variation = "JoineryTight"
	_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_list.minimum_size_changed.connect(_fit_list)
	_scroll.add_child(_list)
	write = UI.primary("Write polish list")
	write.tooltip_text = "One change in Review: design/polish.md and the ticked cuts"
	write.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	UI.bind_height(write, "sm")
	UI_MOTION.press_feedback(write)
	write.pressed.connect(_on_write)
	write.hide()
	column.add_child(write)


func _icon(name: String, tip: String) -> Button:
	var button := UI.icon_button(name, tip)
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_MOTION.press_feedback(button)
	STYLE.on_theme_overrides(
		button,
		func() -> void:
			var side := float(UI_THEME.control_h(button, "sm"))
			button.custom_minimum_size = Vector2(side, side)
	)
	return button


## The findings are being read (no model; nothing changes).
func set_gathering() -> void:
	state = "gathering"
	_dots.show()
	_dots.set("running", true)
	_status.hide()
	_step.text = "Reading every check's last findings…"
	_scroll.hide()
	write.hide()
	again.hide()


## `joinery cut --json`: the headline, the polish rows, the cuts and the estimate.
func show_report(data: Dictionary) -> void:
	report = data
	state = "ready"
	again.show()
	title_label.text = str(data.get("headline", "Nothing to polish"))
	title_label.tooltip_text = title_label.text
	var read: Array = data.get("read", [])
	_settle(
		"done",
		"From " + ", ".join(PackedStringArray(read)) if not read.is_empty() else "Nothing read"
	)
	_step.tooltip_text = _step.text
	_redraw()


func apply_failed(message: String) -> void:
	state = "failed"
	_settle("warning", message)
	again.show()


## The change went to Review: nothing is written or cut until it is accepted there.
func set_sent(cuts: Array) -> void:
	state = "sent"
	write.hide()
	for box: CheckBox in _boxes.values():
		box.disabled = true
	title_label.text = (
		"Polish list waits in Review"
		if cuts.is_empty()
		else (
			"Polish list and %d cut%s wait in Review"
			% [cuts.size(), "" if cuts.size() == 1 else "s"]
		)
	)
	title_label.tooltip_text = title_label.text
	_settle("done", "Nothing changes until you accept it")
	UI_MOTION.pulse(_step)


## Writing didn't go through (not connected): the boxes come back, with why.
func restore(message: String) -> void:
	state = "ready"
	write.show()
	for box: CheckBox in _boxes.values():
		box.disabled = false
	_settle("warning", message)


## The ticked cuts, in their order.
func ticked() -> Array:
	var ids: Array = []
	for id: String in _boxes:
		if (_boxes[id] as CheckBox).button_pressed:
			ids.append(id)
	return ids


func step_text() -> String:
	return _step.text


## The polish rows on the card, in order.
func polish_rows() -> Array[Control]:
	var rows: Array[Control] = []
	for child: Node in _list.get_children():
		if child.has_meta("row") and not child.is_queued_for_deletion():
			rows.append(child as Control)
	return rows


func _redraw() -> void:
	for child: Node in _list.get_children():
		_list.remove_child(child)
		child.queue_free()
	_boxes.clear()
	var shown: Array = []
	var rows: Array = report.get("rows", [])
	if not rows.is_empty():
		_list.add_child(_caption("Polish", ""))
	for index: int in rows.size():
		var row := _row(rows[index] as Dictionary)
		row.visible = index < FIRST_ROWS
		_list.add_child(row)
		if row.visible:
			shown.append(row)
	_more = UI.quiet_link("Show all %d" % rows.size())
	UI.bind_height(_more, "xs")
	_more.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	STYLE.hug_left(_more)
	_more.pressed.connect(_show_all)
	_more.visible = rows.size() > FIRST_ROWS
	_list.add_child(_more)
	var cuts: Array = report.get("cuts", [])
	var kept: Array = report.get("kept", [])
	if not cuts.is_empty() or not kept.is_empty():
		_list.add_child(
			_caption(
				"Proposed cuts", "Ticked cuts move to the brief's Out of scope when you accept"
			)
		)
	for cut: Variant in cuts:
		var entry := _cut(cut as Dictionary)
		_list.add_child(entry)
		shown.append(entry)
	if not kept.is_empty():
		var never := UI.label(
			"Never cut: " + ", ".join(PackedStringArray(kept)) + " (a Keep line covers it)",
			"JoineryCaption",
			true
		)
		never.name = "Kept"
		_list.add_child(never)
	var ship: Variant = report.get("ship")
	if ship is Dictionary:
		_list.add_child(_caption("Ship estimate", ""))
		var words := UI.label(str((ship as Dictionary).get("text", "")), "JoineryCaption", true)
		words.name = "Ship"
		_list.add_child(words)
	_scroll.visible = _list.get_child_count() > 1
	write.visible = true
	_sync_write()
	UI_MOTION.stagger(shown)


func _caption(text: String, tip: String) -> Label:
	var words := UI.label(text, "JoinerySection")
	words.name = text.replace(" ", "")
	words.tooltip_text = tip
	words.mouse_filter = Control.MOUSE_FILTER_PASS
	return words


## One polish row: its priority as a glyph that reads without colour, the title, and where it
## came from; the place and what the player meets are in its tooltip.
func _row(item: Dictionary) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	row.set_meta("row", str(item.get("id", "")))
	row.mouse_filter = Control.MOUSE_FILTER_PASS
	UI.bind_height(row, "xs", false)
	var glyph := BLOCKS.severity_glyph(str(item.get("priority", "low")))
	row.add_child(glyph)
	var title := UI.label(str(item.get("title", "")), "")
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	title.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	title.mouse_filter = Control.MOUSE_FILTER_PASS
	row.add_child(title)
	var source := str(item.get("source", ""))
	var origin := UI.label(str(SOURCES.get(source, source)), "JoineryCaption")
	origin.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	origin.mouse_filter = Control.MOUSE_FILTER_PASS
	row.add_child(origin)
	var tip := PackedStringArray([str(item.get("title", ""))])
	for key: String in ["where", "detail"]:
		if not str(item.get(key, "")).is_empty():
			tip.append(str(item[key]))
	row.tooltip_text = "\n".join(tip)
	title.tooltip_text = row.tooltip_text
	return row


## One proposed cut: a ticked box with the feature, and its reason and evidence under it.
func _cut(item: Dictionary) -> VBoxContainer:
	var entry := VBoxContainer.new()
	entry.theme_type_variation = "JoineryFlush"
	entry.set_meta("cut", str(item.get("id", "")))
	var box := CheckBox.new()
	box.text = str(item.get("text", ""))
	box.button_pressed = true
	box.focus_mode = Control.FOCUS_ALL
	box.accessibility_name = "Cut " + box.text
	box.clip_text = true
	box.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	UI.bind_height(box, "sm")
	box.toggled.connect(func(_on: bool) -> void: _sync_write())
	entry.add_child(box)
	_boxes[str(item.get("id", ""))] = box
	var reason := str(REASONS.get(str(item.get("reason", "")), ""))
	var why := str(item.get("why", ""))
	var words := UI.label(
		reason + (": " + why if not why.is_empty() else ""), "JoineryCaption", true
	)
	words.max_lines_visible = 2
	words.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	words.tooltip_text = words.text
	words.mouse_filter = Control.MOUSE_FILTER_PASS
	# Under the box's text, not under the box.
	var under := MarginContainer.new()
	under.add_child(words)
	entry.add_child(under)
	STYLE.on_theme_overrides(
		under,
		func() -> void:
			var icon := box.get_theme_icon("unchecked")
			var inset := (
				(icon.get_width() if icon != null else 0) + box.get_theme_constant("h_separation")
			)
			inset += int(box.get_theme_stylebox("normal").content_margin_left)
			under.add_theme_constant_override("margin_left", inset)
	)
	return entry


func _sync_write() -> void:
	var count := ticked().size()
	write.text = (
		"Write polish list"
		if count == 0
		else "Write polish list and %d cut%s" % [count, "" if count == 1 else "s"]
	)
	write.accessibility_name = write.text


func _on_write() -> void:
	write_requested.emit(ticked())


func _show_all() -> void:
	var shown: Array = []
	for child: Node in _list.get_children():
		if child.has_meta("row") and not (child as Control).visible:
			(child as Control).show()
			shown.append(child)
	_more.hide()
	UI_MOTION.stagger(shown)


func _fit_list() -> void:
	if _scroll == null or not is_inside_tree():
		return
	var limit := get_viewport_rect().size.y * MAX_LIST_SHARE
	_scroll.custom_minimum_size.y = minf(_list.get_combined_minimum_size().y, limit)


func _notification(what: int) -> void:
	if what == NOTIFICATION_ENTER_TREE:
		_fit_list.call_deferred()


func _settle(kind: String, words: String) -> void:
	_dots.set("running", false)
	_dots.hide()
	UI.set_status_icon(_status, kind)
	_status.show()
	_step.text = words
	_step.tooltip_text = words
