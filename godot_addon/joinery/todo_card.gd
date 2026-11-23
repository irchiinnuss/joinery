@tool
extends PanelContainer
## A request's to-do (ADR 0060), above the footer with the agents at work. For a request with
## several steps Journeyman shows its steps and what "done" means before it changes anything.
## Each step is one compact row (todo_step_row.gd): a status circle, a short title and, behind
## its chevron, the detail the build reads. While it waits for Go the list is the user's to
## edit: titles and details in place, a remove on every line, Alt+Up / Alt+Down to move a step,
## and "Add a step"; a step that says what another already says is refused. Go starts the work
## with the list as left (the build reads exactly that), Cancel drops it.
##
## Once the work runs the list is read-only and moves on as the steps are done: the step being
## worked on pulses and reads in the medium weight, done steps turn to a check and go quiet.
## The "done when" items are checked after the change passes Joinery's checks: each row turns
## to Pass, Fail or Inconclusive with its evidence under it. A Fail goes back to Journeyman once
## ("Fixing what didn't pass…") before the change opens.
##
## The chevron in the header minimises the card to its one line ("To-do · 3 of 7 done") and
## opens it again; the sidebar (sidebar_todo.gd) remembers which way each chat left it. While
## Journeyman works on the to-do that line carries a small live light (still with Reduce
## motion); waiting for Go, or finished, it has none.

signal go_pressed(line: Dictionary)
signal cancel_pressed
signal close_pressed
## The user minimised the card ([param on]) or opened it again.
signal minimised_changed(on: bool)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const ROW := preload("todo_step_row.gd")
## As many steps as a to-do holds (done_when.MAX_STEPS).
const MAX_STEPS := 8
## Words that don't change what a step says (done_when._GLUE).
const GLUE := [
	"a",
	"an",
	"the",
	"and",
	"of",
	"to",
	"in",
	"on",
	"at",
	"by",
	"beside",
	"next",
	"near",
	"for",
	"with",
	"its",
	"it"
]
## State -> the step line's words.
const WORDS := {
	"waiting": "Edit anything, then press Go.",
	"going": "Working through the steps…",
	"steps": "Making the steps that need nothing first at once…",
	"checking": "Checking what done means…",
	"fixing": "Fixing what didn't pass…",
	"cancelled": "Cancelled",
	"dropped": "Set aside for your new message",
}
## The states in which Journeyman is at work on the to-do.
const WORKING := ["going", "steps", "checking", "fixing"]
## Result status -> the glyph (ui_components.gd status_icon) and the words for screen readers.
const LOOKS := {
	"pass": ["done", "Pass"],
	"fail": ["failed", "Fail"],
	"inconclusive": ["warning", "Inconclusive"],
}

var todo_id := ""
## waiting, going, checking, fixing, checked, cancelled or dropped.
var state := ""
## Only the header line shows ("To-do · 3 of 7 done").
var minimised := false
var title_label: Label
## Minimise or open the card.
var fold: Button
var close: Button
var go: Button
var cancel: Button
## Add a step (while waiting for Go).
var add: Button
## The minimised line's live light while Journeyman works.
var live: Control
var _dots: Control
var _status: TextureRect
var _line: HBoxContainer
## Everything under the header line but the step line: steps, done when, Go / Cancel.
var _body: VBoxContainer
var _step: Label
var _steps: VBoxContainer
var _done_title: Label
var _done: VBoxContainer
var _footer: HBoxContainer
## The agent's done-when items by id, with what was checked: {id: {text, kind, result?}}.
var _items: Dictionary = {}
var _order: Array[String] = []
## The steps as the host sent them: [{title, detail, state}].
var _step_data: Array[Dictionary] = []


func _init(identifier := "") -> void:
	todo_id = identifier
	name = "Todo"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "To-do"
	STYLE.task_card(self)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryStack"
	add_child(column)
	var top := VBoxContainer.new()
	top.theme_type_variation = "JoineryTight"
	column.add_child(top)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	top.add_child(head)
	live = UI.status_dot("working")
	live.name = "Live"
	live.tooltip_text = "Journeyman is working on it"
	live.hide()
	head.add_child(live)
	title_label = STYLE.card_title("To-do")
	title_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(title_label)
	fold = UI.icon_button("disclosure_open", "Minimise the to-do")
	fold.name = "Fold"
	fold.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	fold.pressed.connect(func() -> void: set_minimised(not minimised, true))
	UI_MOTION.press_feedback(fold)
	head.add_child(fold)
	close = UI.icon_button("close", "Dismiss the to-do")
	close.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	close.pressed.connect(func() -> void: close_pressed.emit())
	UI_MOTION.press_feedback(close)
	close.hide()
	head.add_child(close)
	var line := HBoxContainer.new()
	_line = line
	line.theme_type_variation = "JoineryRowTight"
	UI.bind_height(line, "xs", false)
	top.add_child(line)
	_dots = INDICATOR.new()
	_dots.hide()
	line.add_child(_dots)
	_status = UI.status_icon("done")
	_status.hide()
	line.add_child(_status)
	_step = UI.label("", "JoineryCaption")
	_step.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_step.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(_step)
	_body = VBoxContainer.new()
	_body.theme_type_variation = "JoineryStack"
	column.add_child(_body)
	var steps := VBoxContainer.new()
	steps.theme_type_variation = "JoineryTight"
	_body.add_child(steps)
	_steps = VBoxContainer.new()
	_steps.theme_type_variation = "JoineryTight"
	_steps.accessibility_name = "Steps"
	steps.add_child(_steps)
	add = UI.quiet("Add a step")
	add.name = "Add"
	add.accessibility_name = "Add a step"
	UI.bind_height(add, "sm")
	STYLE.hug_left(add)
	UI_MOTION.press_feedback(add)
	add.pressed.connect(add_step)
	add.hide()
	steps.add_child(add)
	var done := VBoxContainer.new()
	done.theme_type_variation = "JoineryTight"
	_body.add_child(done)
	_done_title = UI.label("Done when", "JoineryCaption")
	done.add_child(_done_title)
	_done = VBoxContainer.new()
	_done.theme_type_variation = "JoineryTight"
	_done.accessibility_name = "Done when"
	done.add_child(_done)
	_footer = HBoxContainer.new()
	_footer.theme_type_variation = "JoineryRow"
	_body.add_child(_footer)
	go = UI.primary("Go")
	go.accessibility_name = "Go: start the work"
	UI.bind_height(go, "sm")
	UI_MOTION.press_feedback(go)
	go.pressed.connect(_on_go)
	_footer.add_child(go)
	cancel = UI.quiet("Cancel")
	UI.bind_height(cancel, "sm")
	UI_MOTION.press_feedback(cancel)
	cancel.pressed.connect(_on_cancel)
	_footer.add_child(cancel)
	_footer.hide()


func _notification(what: int) -> void:
	if what == NOTIFICATION_ENTER_TREE:
		_sync_live()


## Go and Cancel, while they are offered: the sidebar keeps them in sight when the cards scroll
## (sidebar_tasks.gd).
func pinned_actions() -> Control:
	return _footer if _footer.visible and not minimised else null


## The host's to-do event: {todo_id, state?, steps?, details?, step_states?, step?,
## done_when?, results?, summary?}. A step's progress alone ({step, step_states}) moves the
## rows on in place.
func apply(event: Dictionary) -> void:
	var details: Array = event.get("details", []) if event.get("details") is Array else []
	var marks: Array = event.get("step_states", []) if event.get("step_states") is Array else []
	if event.get("steps") is Array:
		_step_data.clear()
		var index := 0
		for step: Variant in event["steps"]:
			(
				_step_data
				. append(
					{
						"title": str(step),
						"detail": str(details[index]) if index < details.size() else "",
						"state": str(marks[index]) if index < marks.size() else "",
					}
				)
			)
			index += 1
	elif not marks.is_empty():
		for index: int in mini(marks.size(), _step_data.size()):
			_step_data[index]["state"] = str(marks[index])
	if event.get("done_when") is Array:
		_items.clear()
		_order.clear()
		for item: Variant in event["done_when"]:
			var entry := item as Dictionary
			var id := str(entry.get("id", ""))
			_order.append(id)
			_items[id] = {"text": str(entry.get("text", "")), "kind": str(entry.get("kind", ""))}
	if event.get("results") is Array:
		for result: Variant in event["results"]:
			var entry := result as Dictionary
			var id := str(entry.get("id", ""))
			if _items.has(id):
				(_items[id] as Dictionary)["result"] = entry
	var next := str(event.get("state", state))
	var rebuilt := next != state or event.has("steps") or event.has("results")
	state = next
	if rebuilt:
		_build()
	else:
		_move_rows()
	match state:
		"checked":
			var summary := str(event.get("summary", ""))
			_settle(
				"done" if _fails() == 0 else "", summary if not summary.is_empty() else _summary()
			)
			UI_MOTION.pulse(self)
		"cancelled", "dropped":
			_settle("stopped", str(WORDS[state]))
		_:
			_dots.visible = state in WORKING
			_dots.set("running", _dots.visible)
			_status.hide()
			_step.text = str(WORDS.get(state, ""))
			if state == "steps" and event.get("together") is Array:
				_step.text = "Making %s at once…" % _step_list(event["together"] as Array)
			close.visible = false
	_footer.visible = state == "waiting"
	add.visible = state == "waiting"
	if state == "waiting":
		go.disabled = steps().is_empty()
		cancel.disabled = false
	_sync_title()


## Show only the header line ("To-do · 3 of 7 done"), or the whole card again; [param animate]
## reveals the opened card from the top. Says so through [signal minimised_changed] when the
## user did it ([param animate]).
func set_minimised(on: bool, animate := false) -> void:
	var changed := on != minimised
	minimised = on
	_line.visible = not on
	_body.visible = not on
	UI.bind_icon(fold, "disclosure_closed" if on else "disclosure_open")
	fold.tooltip_text = "Show the to-do" if on else "Minimise the to-do"
	fold.accessibility_name = fold.tooltip_text
	_sync_title()
	if animate and not on:
		UI_MOTION.reveal(_body)
		UI_MOTION.fade_in(_line)
	if changed and animate:
		minimised_changed.emit(on)


## [done, total]: each step as it is done or skipped (all of them once the work is checked),
## the "done when" items as each one passes.
func progress() -> Array[int]:
	var steps_done := 0
	for row: Control in step_rows():
		if str(row.get("progress")) in ["done", "skipped"]:
			steps_done += 1
	if state in ["checking", "fixing", "checked"]:
		steps_done = step_rows().size()
	var passed := 0
	for id: String in _order:
		var result: Variant = (_items[id] as Dictionary).get("result")
		if result is Dictionary and str((result as Dictionary).get("status", "")) == "pass":
			passed += 1
	return [steps_done + passed, step_rows().size() + _order.size()]


func _sync_title() -> void:
	_sync_live()
	if not minimised:
		title_label.text = "To-do"
		title_label.tooltip_text = ""
		return
	var counts := progress()
	title_label.text = "To-do · %d of %d done" % [counts[0], counts[1]]
	title_label.tooltip_text = _step.text


## The minimised line's light: on, and pulsing slowly, while Journeyman works on the to-do.
func _sync_live() -> void:
	var on := minimised and state in WORKING
	if on == live.visible and (not on or live.has_meta("_todo_breathing")):
		return
	live.visible = on
	if on and is_inside_tree():
		UI_MOTION.breathe(live)
		live.set_meta("_todo_breathing", true)
	elif not on:
		UI_MOTION.stop(live)
		live.remove_meta("_todo_breathing")


## Still waiting for Go, or working.
func working() -> bool:
	return state in ["waiting", "going", "steps", "checking", "fixing"]


## "step 2", "steps 1 and 3", "steps 1, 2 and 4".
static func _step_list(numbers: Array) -> String:
	var words := PackedStringArray()
	for number: Variant in numbers:
		words.append(str(int(number)))
	if words.size() == 1:
		return "step " + words[0]
	var last := words[words.size() - 1]
	words.resize(words.size() - 1)
	return "steps %s and %s" % [", ".join(words), last]


## Whether two step titles say the same thing (done_when.same_step): equal once case and
## punctuation are set aside, the same words but for "the", "by" and the like, or nearly the
## same letters; titles that differ in a number ("Room 1", "Room 2") never are.
static func same_step(first: String, second: String) -> bool:
	var a := _key(first)
	var b := _key(second)
	if a.is_empty() or b.is_empty():
		return a == b
	if a == b:
		return true
	if _numbers(a) != _numbers(b):
		return false
	var words_a := _content(a)
	if not words_a.is_empty() and words_a == _content(b):
		return true
	return a.similarity(b) >= 0.92


static func _key(text: String) -> String:
	var kept := ""
	for character: String in text.to_lower():
		kept += character if character.is_valid_identifier() or character.is_valid_int() else " "
	return " ".join(kept.split(" ", false))


static func _numbers(text: String) -> PackedStringArray:
	var found := PackedStringArray()
	for word: String in text.split(" ", false):
		if word.is_valid_int():
			found.append(word)
	return found


static func _content(text: String) -> Array:
	var words: Array = []
	for word: String in text.split(" ", false):
		if word not in GLUE and word not in words:
			words.append(word)
	words.sort()
	return words


func step_text() -> String:
	return _step.text


## The steps' rows as shown, in order (todo_step_row.gd).
func step_rows() -> Array[Control]:
	var found: Array[Control] = []
	for row: Node in _steps.get_children():
		if not _gone(row):
			found.append(row as Control)
	return found


## The steps' titles as shown (edited while waiting).
func steps() -> PackedStringArray:
	var found := PackedStringArray()
	for row: Control in step_rows():
		found.append(str(row.call("title_text")))
	return found


## The done-when rows as shown: [{id, text, status?, evidence?}].
func done_rows() -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	for row: Node in _done.get_children():
		if _gone(row):
			continue
		var id := str(row.get_meta("item_id", ""))
		var entry := {"id": id, "text": _row_text(row)}
		var result: Variant = (_items.get(id, {}) as Dictionary).get("result")
		if result is Dictionary:
			entry["status"] = str((result as Dictionary).get("status", ""))
			entry["evidence"] = str((result as Dictionary).get("evidence", ""))
		found.append(entry)
	return found


## The rows' remove buttons (while waiting), steps first.
func remove_buttons() -> Array[Button]:
	var found: Array[Button] = []
	for list: Node in [_steps, _done]:
		for row: Node in list.get_children():
			var remove := row.find_child("Remove", true, false) as Button
			if remove != null and not _gone(row):
				found.append(remove)
	return found


## A new, empty step at the end of the list, its title ready to type (while waiting).
func add_step() -> Control:
	if state != "waiting" or step_rows().size() >= MAX_STEPS:
		return null
	var row := _step_row({"title": "", "detail": "", "state": ""}, 0, true)
	_steps.add_child(row)
	_renumber()
	UI_MOTION.enter(row)
	row.call("focus_title")
	return row


func _build() -> void:
	var editable := state == "waiting"
	for list: Node in [_steps, _done]:
		for child: Node in list.get_children():
			list.remove_child(child)
			child.queue_free()
	var rows: Array = []
	for index: int in _step_data.size():
		var row := _step_row(_step_data[index], index + 1, editable)
		_steps.add_child(row)
		rows.append(row)
	_renumber()
	for id: String in _order:
		var item: Dictionary = _items[id]
		var row := _done_row(id, item, editable)
		_done.add_child(row)
		rows.append(row)
	_done_title.visible = not _order.is_empty()
	UI_MOTION.stagger(rows)


func _step_row(data: Dictionary, origin: int, editable: bool) -> Control:
	var row: Control = ROW.new(str(data["title"]), str(data["detail"]), editable, origin)
	row.call("set_progress", str(data.get("state", "")))
	row.connect("remove_requested", _remove)
	row.connect("move_requested", _move)
	row.connect("committed", _check_repeat)
	return row


## Each row's place in the list, for its accessible names.
func _renumber() -> void:
	var number := 1
	for row: Control in step_rows():
		row.set("number", number)
		row.call("sync_view")
		number += 1
	add.disabled = number > MAX_STEPS


## The steps' progress, in place: the rows keep their open details and their focus.
func _move_rows() -> void:
	var rows := step_rows()
	for index: int in mini(rows.size(), _step_data.size()):
		rows[index].call("set_progress", str(_step_data[index].get("state", "")))


## A step whose title now says what another step says is refused: a new one goes, an edited
## one goes back to its last words; the step it repeats is flashed, and the line says which.
func _check_repeat(row: Control) -> void:
	var words := str(row.call("title_text"))
	var rows := step_rows()
	for other: Control in rows:
		if other == row or not same_step(str(other.call("title_text")), words):
			continue
		var number := rows.find(other) + 1
		_step.text = "Step %d already says that." % number
		UI_MOTION.flash(other)
		if int(row.get("origin")) == 0 and str(row.call("committed_title")).is_empty():
			_remove(row)
		else:
			row.call("undo_title")
		return
	if state == "waiting":
		_step.text = str(WORDS["waiting"])
	go.disabled = steps().is_empty()


## Move [param row] [param by] places up (-1) or down (+1) the list (while waiting).
func _move(row: Control, by: int) -> void:
	var to := row.get_index() + by
	if to < 0 or to >= _steps.get_child_count():
		return
	_steps.move_child(row, to)
	_renumber()
	row.call("focus_title")


## A done-when item: its state (an empty dot until checked, then Pass, Fail or Inconclusive),
## its words and, once checked, the evidence under them.
func _done_row(id: String, item: Dictionary, editable: bool) -> VBoxContainer:
	var block := VBoxContainer.new()
	block.theme_type_variation = "JoineryTight"
	block.set_meta("item_id", id)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	block.add_child(row)
	var result: Variant = item.get("result")
	var status := str((result as Dictionary).get("status", "")) if result is Dictionary else ""
	var mark: Control
	if LOOKS.has(status):
		var icon := UI.status_icon(str(LOOKS[status][0]))
		icon.name = "Result"
		icon.tooltip_text = str(LOOKS[status][1])
		icon.accessibility_name = str(LOOKS[status][1])
		mark = icon
	else:
		var dot := UI.status_dot("working" if state == "checking" else "off")
		dot.tooltip_text = "Checked after the change"
		mark = dot
	STYLE.on_theme(mark, func() -> void: mark.custom_minimum_size.x = UI_THEME.icon_px(mark))
	row.add_child(mark)
	var words := _words(row, str(item.get("text", "")), editable, "Done when", block)
	if not status.is_empty():
		words.accessibility_name = "%s: %s" % [str(item.get("text", "")), str(LOOKS[status][1])]
		var evidence := UI.label(
			str((result as Dictionary).get("evidence", "")), "JoineryCaption", true
		)
		evidence.name = "Evidence"
		evidence.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		var indent := MarginContainer.new()
		STYLE.on_theme_overrides(
			indent,
			func() -> void:
				indent.add_theme_constant_override(
					"margin_left", UI_THEME.icon_px(indent) + UI_THEME.space(indent, 1)
				)
		)
		indent.add_child(evidence)
		block.add_child(indent)
	return block


## A done-when item's words: a borderless field to edit in place while waiting, else a label;
## and a remove button while waiting.
func _words(
	row: HBoxContainer, text: String, editable: bool, label: String, target: Control
) -> Control:
	var words: Control
	if editable:
		var field := LineEdit.new()
		field.name = "Words"
		field.text = text
		field.flat = true
		field.accessibility_name = label
		field.tooltip_text = text
		field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		UI.bind_height(field, "sm")
		words = field
	else:
		var shown := UI.label(text, "", true)
		shown.name = "Words"
		shown.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		shown.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		words = shown
	row.add_child(words)
	if editable:
		var remove := UI.icon_button("close", "Remove")
		remove.name = "Remove"
		remove.accessibility_name = "Remove: " + text
		remove.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		UI_MOTION.press_feedback(remove)
		STYLE.on_theme_overrides(
			remove,
			func() -> void:
				var side := float(UI_THEME.control_h(remove, "xs"))
				remove.custom_minimum_size = Vector2(side, side)
		)
		remove.pressed.connect(_remove.bind(target))
		row.add_child(remove)
	return words


## Remove [param target] (a step's row or a done-when item's block); Go needs a step left.
func _remove(target: Control) -> void:
	target.set_meta("removed", true)
	UI_MOTION.exit(target, null, target.queue_free)
	_renumber()
	go.disabled = steps().is_empty()


static func _gone(row: Node) -> bool:
	return row.is_queued_for_deletion() or row.has_meta("removed")


static func _row_text(row: Node) -> String:
	var words := row.find_child("Words", true, false)
	if words is LineEdit:
		return (words as LineEdit).text.strip_edges()
	if words is Label:
		return (words as Label).text
	return ""


func _on_go() -> void:
	var left := line()
	if left.is_empty():
		return
	set_starting()
	go_pressed.emit(left)


## Go pressed: Go rests and the line says the work is starting; Cancel stays.
func set_starting() -> void:
	go.disabled = true
	_step.text = "Starting…"


## The to-do as the user left it, {todo_id, steps, done_when} ({} with no step left).
func line() -> Dictionary:
	var done: Array = []
	for row: Dictionary in done_rows():
		if not str(row["text"]).is_empty():
			done.append({"id": row["id"], "text": row["text"]})
	var kept: Array = []
	var seen := PackedStringArray()
	for row: Control in step_rows():
		var words := str(row.call("title_text"))
		var repeat := false
		for earlier: String in seen:
			repeat = repeat or same_step(earlier, words)
		if words.is_empty() or repeat:
			continue
		seen.append(words)
		kept.append(
			{"title": words, "detail": str(row.call("detail_text")), "from": int(row.get("origin"))}
		)
	if kept.is_empty():
		return {}
	return {"todo_id": todo_id, "steps": kept, "done_when": done}


func _on_cancel() -> void:
	cancel.disabled = true
	cancel_pressed.emit()


func _settle(kind: String, words: String) -> void:
	_dots.set("running", false)
	_dots.hide()
	# "" is a plain fact (how many of each severity): no icon claims it is good or bad.
	if not kind.is_empty():
		UI.set_status_icon(_status, kind)
	_status.visible = not kind.is_empty()
	_step.text = words
	_step.tooltip_text = words
	close.show()


func _fails() -> int:
	var count := 0
	for id: String in _order:
		var result: Variant = (_items[id] as Dictionary).get("result")
		if result is Dictionary and str((result as Dictionary).get("status", "")) == "fail":
			count += 1
	return count


func _summary() -> String:
	var passed := 0
	for id: String in _order:
		var result: Variant = (_items[id] as Dictionary).get("result")
		if result is Dictionary and str((result as Dictionary).get("status", "")) == "pass":
			passed += 1
	return "%d of %d done" % [passed, _order.size()]
