@tool
extends PanelContainer
## The console errors Joinery is watching (ADR 0075), with the agents' cards in the sidebar: the
## one place an error and its fix show. One compact row per distinct error (same message, same
## place): a short title ("window.cpp:2138 — !is_inside_tree()", the whole message on hover)
## and one caption line with what is happening to it: fixing (the fix's current step and a
## quiet timer), a fix ready for review, fixed, no fix needed, not a game error (an editor
## message the host set aside before any agent), a Joinery bug, stopped after two tries, or
## ignored. The cause a debugger found sits behind the row's disclosure, never inline.
## "Fix" on a row, or "Fix all" when the project asks first ([agents] fix_errors = "ask"),
## starts the debugger and a worker; the fix is a normal change the user reviews. The host sends
## the whole list each time it changes (chat_bridge.gd's errors_event); the fix task's own events
## come here too ([method track]), so it needs no card of its own. While the project asks first,
## "Always fix errors like this" under the rows turns on fixing them without asking; the
## conversation never offers the same error a second time while this card shows it.

signal fix_requested(ids: Array)
## "Always fix errors like this": [param id] is the error to start with.
signal always_fix_requested(id: String)
signal ignore_requested(id: String, ignore: bool)
signal review_requested(transaction_id: String)
signal stop_requested(task_id: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
## Rows shown before "Show all".
const FOLDED := 4
## status -> its row's status icon (ui_components.gd STATUS_ICONS); the rest show "failed".
const MARKS := {
	"in_review": "done",
	"fixed": "done",
	"explained": "stopped",
	"editor": "stopped",
	"joinery": "warning",
	"ignored": "stopped",
	"from_change": "warning",
}
## status -> what the row says about it.
const WORDS := {
	"open": "",
	"queued": "Waiting for a free agent",
	"fixing": "Fixing…",
	"in_review": "Fix ready for review",
	"fixed": "Fixed",
	"explained": "No fix needed",
	"editor": "Not a game error",
	"joinery": "Joinery bug",
	"stopped": "Stopped after 2 tries",
	"from_change": "Came with a change in review",
	"ignored": "Ignored",
}
## States that are settled: listed, not counted as errors to deal with.
const SETTLED := ["fixed", "explained", "editor", "joinery", "ignored"]

var mode := "ask"
var headline: Label
var fix_all: Button
## "Always fix errors like this", while the project asks first and an error waits.
var always: Button
var _rows: VBoxContainer
var _more: Button
var _groups: Array = []
var _open := false
## Rows already on screen: only a new one enters with motion.
var _shown: Dictionary = {}
## Rows whose cause is open, by group id: a new list keeps them open.
var _expanded: Dictionary = {}
## Fix tasks' progress, by task id: {step, started (msec)}.
var _tasks: Dictionary = {}
## Fixing rows' captions, by task id, so the step and timer change in place.
var _live: Dictionary = {}
var _last_second := -1
## The rows' ids when the user closed the card: it stays away until a new error comes.
var _dismissed: Dictionary = {}
var _close: Button


func _init() -> void:
	name = "ErrorsCard"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Console errors"
	STYLE.on_theme_overrides(
		self,
		func() -> void:
			var source := get_theme_stylebox("panel", "JoineryChangeCard")
			if source == null:
				return
			var box := source.duplicate() as StyleBox
			box.set_content_margin_all(float(UI_THEME.space(self, 2)))
			add_theme_stylebox_override("panel", box)
	)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	add_child(column)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	column.add_child(head)
	headline = STYLE.card_title("")
	headline.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	headline.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	headline.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	head.add_child(headline)
	fix_all = UI.secondary("Fix all")
	STYLE.on_card(fix_all)
	fix_all.accessibility_name = "Fix all console errors"
	UI.bind_height(fix_all, "sm")
	fix_all.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_MOTION.press_feedback(fix_all)
	fix_all.pressed.connect(_on_fix_all)
	head.add_child(fix_all)
	# Once nothing is left to deal with, the card can be closed until a new error comes.
	_close = UI.icon_button("close", "Close until a new error comes")
	_close.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_close.pressed.connect(dismiss)
	head.add_child(_close)
	_rows = VBoxContainer.new()
	_rows.theme_type_variation = "JoineryTight"
	column.add_child(_rows)
	_more = UI.quiet_link("")
	UI.bind_height(_more, "xs")
	STYLE.hug_left(_more)
	_more.pressed.connect(_toggle_more)
	_more.hide()
	column.add_child(_more)
	always = UI.quiet_link("Always fix errors like this")
	always.name = "AlwaysFix"
	always.tooltip_text = "Fix new console errors without asking; each fix is still yours to review"
	UI.bind_height(always, "xs")
	always.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	STYLE.hug_left(always)
	always.pressed.connect(_on_always)
	always.hide()
	column.add_child(always)
	set_process(false)


## The host's list: {mode, groups: [{id, title, label, message, count, status, cause, …}]}.
func apply(event: Dictionary) -> void:
	mode = str(event.get("mode", mode))
	var groups: Variant = event.get("groups", [])
	_groups = groups if groups is Array else []
	_render()


## A console fix task's event (chat_bridge.gd's task_event, "console": true): its step and
## timer go on the rows it fixes.
func track(event: Dictionary) -> void:
	var id := str(event.get("task_id", ""))
	if id.is_empty():
		return
	var entry: Dictionary = _tasks.get(id, {"step": "", "started": Time.get_ticks_msec()})
	if event.has("elapsed_s") and not _tasks.has(id):
		entry["started"] = Time.get_ticks_msec() - int(event["elapsed_s"]) * 1000
	var step := str(event.get("step", event.get("text", "")))
	if not step.is_empty():
		entry["step"] = step
	_tasks[id] = entry
	if str(event.get("kind", "task")) == "task" and str(event.get("state", "working")) != "working":
		_tasks.erase(id)
	_refresh_live()


## Rows that count as errors still to deal with (settled and ignored ones are listed, not
## counted).
func open_count() -> int:
	var count := 0
	for group: Dictionary in _groups:
		if not str(group.get("status", "")) in SETTLED:
			count += 1
	return count


func has_rows() -> bool:
	if _groups.is_empty() or mode == "off":
		return false
	for group: Dictionary in _groups:
		if not _dismissed.has(str(group.get("id", ""))):
			return true
	return false


## Close the card; it comes back with the next error it has not shown.
func dismiss() -> void:
	for group: Dictionary in _groups:
		_dismissed[str(group.get("id", ""))] = true
	hide()
	var tasks := get_parent()
	if tasks != null and tasks.has_method("sync_visible"):
		tasks.call("sync_visible")


## The rows' titles as shown, for tests.
func row_texts() -> PackedStringArray:
	var texts := PackedStringArray()
	for row: Node in _rows.get_children():
		if not row.is_queued_for_deletion():
			texts.append(str(row.get_meta("joinery_error", "")))
	return texts


## The rows' captions as shown ("Fixing… · 0:09", "Not a game error"), for tests.
func caption_texts() -> PackedStringArray:
	var texts := PackedStringArray()
	for row: Node in _rows.get_children():
		if not row.is_queued_for_deletion():
			var caption: Label = row.get_meta("joinery_caption", null)
			texts.append(caption.text if caption != null else "")
	return texts


func _render() -> void:
	var count := open_count()
	headline.text = (
		_settled_headline()
		if count == 0
		else "1 console error" if count == 1 else "%d console errors" % count
	)
	headline.tooltip_text = "Errors from Output, the game's log, playtests and tests"
	var fixable := 0
	for group: Dictionary in _groups:
		if str(group.get("status", "")) in ["open", "stopped"]:
			fixable += 1
	fix_all.visible = mode == "ask" and fixable > 1
	always.visible = mode == "ask" and fixable > 0
	_close.visible = count == 0
	for row: Node in _rows.get_children():
		row.queue_free()
	_live.clear()
	var shown := _groups if _open else _groups.slice(0, FOLDED)
	var entering: Array = []
	var now: Dictionary = {}
	for group: Dictionary in shown:
		var row := _row(group)
		_rows.add_child(row)
		var id := str(group.get("id", ""))
		now[id] = true
		if not _shown.has(id):
			entering.append(row)
	_shown = now
	if not entering.is_empty():
		UI_MOTION.stagger(entering)
	var hidden := _groups.size() - FOLDED
	_more.visible = hidden > 0
	_more.text = "Show fewer" if _open else "Show all %d" % _groups.size()
	_refresh_live()


## The headline once nothing is left to deal with, saying what happened to the rows under it:
## "Fixed 1 error", "Fixed 2 of 3 errors", or "Nothing left to fix" when none needed a fix.
func _settled_headline() -> String:
	var fixed := 0
	for group: Dictionary in _groups:
		if str(group.get("status", "")) == "fixed":
			fixed += 1
	if fixed == 0:
		return "Nothing left to fix" if not _groups.is_empty() else "Nothing to fix"
	var noun := "error" if _groups.size() == 1 else "errors"
	if fixed == _groups.size():
		return "Fixed %d %s" % [fixed, noun]
	return "Fixed %d of %d %s" % [fixed, _groups.size(), noun]


## One error: [mark] title / caption · action [›], and the cause under it when open.
func _row(group: Dictionary) -> Control:
	var id := str(group.get("id", ""))
	var status := str(group.get("status", "open"))
	var title := str(group.get("title", group.get("label", "")))
	var holder := VBoxContainer.new()
	holder.theme_type_variation = "JoineryTight"
	holder.set_meta("joinery_error", title)
	holder.accessibility_name = "Error: " + title
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	holder.add_child(row)
	var mark: Control
	if status in ["fixing", "queued"]:
		mark = INDICATOR.new()
		mark.set("running", status == "fixing")
	else:
		mark = UI.status_icon(str(MARKS.get(status, "failed")))
	mark.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	row.add_child(mark)
	var text := VBoxContainer.new()
	text.theme_type_variation = "JoineryTight"
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(text)
	# One line: the short title, the whole message on hover.
	var name_label := UI.label(title)
	name_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	name_label.tooltip_text = str(group.get("message", title))
	name_label.mouse_filter = Control.MOUSE_FILTER_PASS
	if status in SETTLED:
		name_label.theme_type_variation = "JoineryCaption"
	text.add_child(name_label)
	# What is happening, then what can be done about it, on one line under the title: the
	# action ends on the trailing edge, the words give way first.
	var meta := HBoxContainer.new()
	meta.theme_type_variation = "JoineryRow"
	text.add_child(meta)
	var caption := UI.label(_caption(group), "JoineryCaption", false)
	caption.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	caption.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	caption.mouse_filter = Control.MOUSE_FILTER_PASS
	var note := str(group.get("note", ""))
	caption.tooltip_text = note if not note.is_empty() else caption.text
	meta.add_child(caption)
	holder.set_meta("joinery_caption", caption)
	var task := str(group.get("task_id", ""))
	if status == "fixing" and not task.is_empty():
		caption.set_meta("joinery_group", group)
		var captions: Array = _live.get(task, [])
		captions.append(caption)
		_live[task] = captions
	var action := _action(group)
	if action != null:
		action.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		meta.add_child(action)
	var cause := str(group.get("cause", ""))
	if not cause.is_empty():
		meta.add_child(_disclosure(text, id, cause))
	var corner := _corner(group, row)
	if corner != null:
		row.add_child(corner)
	return holder


## The cause behind a ›, under the row's words: closed by default, kept open across new lists.
func _disclosure(holder: VBoxContainer, id: String, cause: String) -> Button:
	var more := UI.icon_button("disclosure_closed", "Why")
	more.toggle_mode = true
	more.flat = true
	more.accessibility_name = "Show the cause"
	more.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	STYLE.on_theme_overrides(
		more,
		func() -> void:
			var side := float(UI_THEME.control_h(more, "xs"))
			more.custom_minimum_size = Vector2(side, side)
	)
	var detail := UI.label(cause, "JoineryCaption", true)
	detail.name = "Cause"
	detail.visible = _expanded.has(id)
	holder.add_child(detail)
	more.set_pressed_no_signal(detail.visible)
	UI.bind_icon(more, "disclosure_open" if detail.visible else "disclosure_closed")
	more.toggled.connect(
		func(open: bool) -> void:
			UI.bind_icon(more, "disclosure_open" if open else "disclosure_closed")
			if open:
				_expanded[id] = true
				UI_MOTION.reveal(detail)
			else:
				_expanded.erase(id)
				detail.hide()
	)
	return more


## Ignore on a row that waits, Stop on one being fixed: an icon on the row's trailing edge.
func _corner(group: Dictionary, row: Control) -> Button:
	var id := str(group.get("id", ""))
	var status := str(group.get("status", "open"))
	var label := str(group.get("title", group.get("label", "")))
	var button: Button
	if status in ["open", "stopped", "from_change"]:
		button = UI.icon_button("close", "Ignore this error")
		button.accessibility_name = "Ignore: " + label
		button.pressed.connect(_on_ignore.bind(id, row.get_parent()))
		STYLE.reveal_on_hover(row, button)
	elif status == "fixing" and (_live.get(str(group.get("task_id", "")), []) as Array).size() == 1:
		# One Stop per fix: a fix working on several errors has it on its first row.
		button = UI.icon_button("stop", "Stop this fix")
		# Framed like the composer's Stop, so it reads as a button.
		button.theme_type_variation = "JoineryStopButton"
		button.accessibility_name = "Stop fixing: " + label
		var task := str(group.get("task_id", ""))
		button.pressed.connect(
			func() -> void:
				button.disabled = true
				stop_requested.emit(task)
		)
	else:
		return null
	button.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	STYLE.on_theme_overrides(
		button,
		func() -> void:
			var side := float(UI_THEME.control_h(button, "sm"))
			button.custom_minimum_size = Vector2(side, side)
	)
	return button


## "Fixing · Reading the error… · 0:09", "3 times · Fix ready for review", "Not a game error".
func _caption(group: Dictionary, step := "", seconds := -1) -> String:
	var parts := PackedStringArray()
	var status := str(group.get("status", "open"))
	var words := str(WORDS.get(status, ""))
	if status == "open" and bool(group.get("declined", false)):
		words = "Fix discarded"
	if status == "fixing" and not step.is_empty():
		words = step
	var count := int(group.get("count", 1))
	if count > 1:
		parts.append("%d times" % count)
	if not words.is_empty():
		parts.append(words)
	if seconds >= 3:
		parts.append(UI_MOTION.format_clock(seconds))
	return " · ".join(parts)


## The fixing rows' captions follow their task: its step, then its timer, each second.
func _refresh_live() -> void:
	for task: String in _live:
		var entry: Dictionary = _tasks.get(task, {})
		var seconds := -1
		if not entry.is_empty():
			seconds = int((Time.get_ticks_msec() - int(entry["started"])) / 1000.0)
		for caption: Label in _live[task]:
			if is_instance_valid(caption):
				var group: Dictionary = caption.get_meta("joinery_group")
				caption.text = _caption(group, str(entry.get("step", "")), seconds)
	set_process(not _live.is_empty() and not _tasks.is_empty())


func _process(_delta: float) -> void:
	var second := int(Time.get_ticks_msec() / 1000.0)
	if second != _last_second:
		_last_second = second
		_refresh_live()


func _action(group: Dictionary) -> Button:
	var id := str(group.get("id", ""))
	var label := str(group.get("title", group.get("label", "")))
	var button: Button
	match str(group.get("status", "open")):
		"open", "stopped":
			button = UI.quiet_link("Fix" if str(group.get("status")) == "open" else "Try again")
			button.accessibility_name = "Fix: " + label
			button.pressed.connect(_on_fix.bind(id, button))
		"in_review":
			button = UI.quiet_link("Review")
			button.accessibility_name = "Review the fix for: " + label
			var transaction := str(group.get("transaction_id", ""))
			button.pressed.connect(func() -> void: review_requested.emit(transaction))
		"ignored":
			button = UI.quiet_link("Restore")
			button.accessibility_name = "Stop ignoring: " + label
			button.pressed.connect(func() -> void: ignore_requested.emit(id, false))
		_:
			return null
	UI.bind_height(button, "xs")
	# A short word ("Fix") is still a full target.
	UI.bind_width(button, "xs")
	UI_MOTION.press_feedback(button)
	return button


## Optimistic: the row says it is waiting at once; the host's next list confirms it.
func _on_fix(id: String, button: Button) -> void:
	button.disabled = true
	button.text = "Starting…"
	fix_requested.emit([id])


func _on_fix_all() -> void:
	fix_all.disabled = true
	var ids: Array = []
	for group: Dictionary in _groups:
		if str(group.get("status", "")) in ["open", "stopped"]:
			ids.append(str(group.get("id", "")))
	fix_requested.emit(ids)
	get_tree().create_timer(1.0).timeout.connect(func() -> void: fix_all.disabled = false)


## Starts with the first error still waiting for a fix.
func _on_always() -> void:
	for group: Dictionary in _groups:
		if str(group.get("status", "")) in ["open", "stopped"]:
			always_fix_requested.emit(str(group.get("id", "")))
			return


func _on_ignore(id: String, row: Control) -> void:
	UI_MOTION.exit(row, null, func() -> void: row.hide())
	ignore_requested.emit(id, true)


func _toggle_more() -> void:
	_open = not _open
	_render()


## Controls whose text follows the transcript text size, as [control, TYPE step] pairs.
func scalables() -> Array:
	return [[headline, "body"]]
