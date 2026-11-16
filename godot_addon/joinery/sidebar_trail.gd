@tool
extends VBoxContainer
## Journeyman's work trail on a request's card (sidebar_change_card.gd): what it is doing, as
## it happens. Every agent's tool calls arrive as the host's "trail" events (bridge/trail.py),
## so the lines read the same whichever agent runs: "Reading level.gd", "Searching the code for
## Spawn", "Modelling the mug in Blender: bevel, 2 segments". Joinery's own progress
## ("Checking the change…") is a line of its own, or, while a call runs, the quiet line under it.
## A test run (joinery/test_progress.py) is one line that says the test it checks now, then the
## run's summary, with every result under it.
##
## While the work runs, the trail is ONE live line that replaces itself: the current step with
## its breathing dot (the card puts its clock at the end), and under it, faint, the call's
## progress or the agent's newest short thought, also replaced. Nothing stacks up.
##
## The whole record is kept: when the card's fold is opened ([method show_all]), every line
## shows, newest first, each with its tick or its error, an edit's +/− line count, a picture
## that landed and a test run's results.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const RESULTS := preload("sidebar_trail_results.gd")
## How a line ends -> [icon, tint role].
const ENDS := {
	"success": ["success", "success"],
	"stopped": ["stop", "text_muted"],
	"error": ["error", "danger"],
}
## Progress that only repeats what the live call already says ("Modelling … in Blender").
const ECHOES := ["Working in ", "Using "]
## Step verbs whose past tense is not "-ed".
const IRREGULAR := {
	"beginning": "began",
	"bringing": "brought",
	"building": "built",
	"choosing": "chose",
	"cutting": "cut",
	"drawing": "drew",
	"finding": "found",
	"getting": "got",
	"growing": "grew",
	"giving": "gave",
	"holding": "held",
	"keeping": "kept",
	"laying": "laid",
	"leaving": "left",
	"making": "made",
	"putting": "put",
	"reading": "read",
	"running": "ran",
	"seeing": "saw",
	"sending": "sent",
	"setting": "set",
	"splitting": "split",
	"taking": "took",
	"thinking": "thought",
	"writing": "wrote",
}
## Height of a picture that landed, in units.
const PICTURE_UNITS := 40.0

## The one live line while the work runs.
var _live: VBoxContainer
## Every line, newest first: shown when the fold is opened.
var _history: VBoxContainer
var _all := false
## What the live line said last ([row, words, note]), so only a real change fades in.
var _said: Array = []


func _init() -> void:
	name = "Trail"
	theme_type_variation = "JoineryTight"
	accessibility_name = "What Journeyman is doing"
	_live = _build_live()
	add_child(_live)
	_history = VBoxContainer.new()
	_history.name = "History"
	_history.theme_type_variation = "JoineryTight"
	_history.hide()
	add_child(_history)


## A progress line from Joinery or the agent: the step before it gets its tick and this one
## is the live line. [param detail] is the whole text on hover.
func add_line(text: String, detail := "") -> void:
	_settle_kind("step", "success")
	var row := _add_row("step", text, detail)
	_set_live(row, true)
	_sync_live()


## The host's trail event: {id, text, state: running|done|failed, note: tool|thought, file,
## added, removed, picture}.
func add_call(event: Dictionary) -> void:
	var text := str(event.get("text", "")).strip_edges()
	if str(event.get("note", "")) == "thought":
		if not text.is_empty():
			_add_row("thought", text, text)
			_sync_live()
		return
	var id := str(event.get("id", ""))
	var row := _find(id)
	var state := str(event.get("state", "running"))
	if row == null:
		if text.is_empty():
			return
		# A call says it started; steps before it are done.
		_settle_kind("step", "success")
		row = _add_row("call", text, text)
		row.set_meta("trail_id", id)
		_set_live(row, true)
		_show_size(
			row,
			str(event.get("file", "")),
			int(event.get("added", 0)),
			int(event.get("removed", 0))
		)
	if state in ["done", "failed"]:
		_end(row, "success" if state == "done" else "error")
		_show_picture(row, str(event.get("picture", "")))
	_sync_live()


## A test run's event ({id, label, now, total, results, done, summary}): its line says the test
## it checks now ("Checking mower starts on lawn") with how far it is and any failure by name
## under it; done, the line is the summary, ticked, or marked when a test failed.
func add_tests(event: Dictionary) -> void:
	var id := str(event.get("id", ""))
	var label := str(event.get("label", "Running the tests"))
	var results: Array = event.get("results", []) if event.get("results") is Array else []
	var row := _find(id)
	if row == null:
		_settle_kind("step", "success")
		row = _add_row("call", label, label)
		row.set_meta("trail_id", id)
		row.set_meta("tests", true)
		_set_live(row, true)
	var failed := results.filter(
		func(each: Variant) -> bool: return not bool((each as Dictionary).get("passed", true))
	)
	var words := _words(row)
	var note := row.find_child("Note", true, false) as Label
	if bool(event.get("done", false)):
		words.text = str(event.get("summary", label))
		words.tooltip_text = words.text
		_end(row, "error" if not failed.is_empty() else "success")
	elif bool(row.get_meta("live", false)):
		var now := str(event.get("now", ""))
		words.text = now if not now.is_empty() else label
		words.tooltip_text = words.text
		var far := "%d of %d done" % [results.size(), int(event.get("total", 0))]
		if not failed.is_empty():
			var last: Dictionary = failed[-1]
			far += " · %d failed: %s" % [failed.size(), str(last.get("name", ""))]
		note.text = far
		note.tooltip_text = far
		note.show()
		(row.get_node("Under") as Control).show()
	var box := row.find_child("Results", true, false) as RESULTS
	if box == null and not results.is_empty():
		box = RESULTS.new()
		note.get_parent().add_child(box)
	if box != null:
		box.show_results(results)
		(row.get_node("Under") as Control).visible = note.visible or box.visible
	_sync_live()


## Progress while a call runs goes under its line ("Importing the result into Godot"). False
## when no call is live, so [param text] becomes a line of its own.
func on_progress(text: String) -> bool:
	var row := _newest_live()
	if row == null or str(row.get_meta("kind")) != "call":
		return false
	# A test run says its own progress; the ticking "Running scene tests: 3 of 8" repeats it.
	if row.has_meta("tests"):
		return true
	for echo: String in ECHOES:
		if text.begins_with(echo):
			return true
	var note := row.find_child("Note", true, false) as Label
	note.text = text
	note.tooltip_text = text
	note.show()
	(row.get_node("Under") as Control).show()
	_sync_live()
	return true


## Every live line ends as [param kind] ("success", "stopped" or "error"); the art gate's line
## keeps waiting.
func settle(kind: String) -> void:
	for row: Node in _rows():
		if str(row.get_meta("kind", "")) != "gate":
			_end(row as Control, kind)
	_sync_live()


## The whole record ([param all], when the card's fold is opened), or the one live line.
func show_all(all: bool) -> void:
	_all = all
	_history.visible = all
	for row: Node in _rows():
		(row as Control).visible = true
	if all:
		UI_MOTION.stagger(_rows())
	_sync_live()


## The live line's top row, where the card's clock rides while the work runs.
func live_line() -> HBoxContainer:
	return _live.get_node("Line") as HBoxContainer if _live.visible else null


## The words on show: the live line, or every line newest first when opened.
func texts() -> PackedStringArray:
	var shown := PackedStringArray()
	if _live.visible:
		shown.append(_live_words().text)
	elif _history.visible:
		for row: Node in _rows():
			shown.append(_words(row).text)
	return shown


## The faint line under the live line ("" when none shows).
func live_note() -> String:
	var note := _live.get_node("Under/Note") as Label
	return note.text if _live.visible and note.visible else ""


## True once the trail has any line.
func has_lines() -> bool:
	return not _rows().is_empty()


## Lines that are steps or calls (thoughts aside), for "5 steps".
func step_count() -> int:
	var count := 0
	for row: Node in _rows():
		if str(row.get_meta("kind", "")) != "thought":
			count += 1
	return count


## True while [param text] is on show as the live line or the quiet line under it.
func shows(text: String) -> bool:
	if _newest_live() == null:
		return false
	return _live.visible and (_live_words().text == text or live_note() == text)


## The art gate's line ("Waiting for the look"); it pulses until [method end_gate].
func add_gate(text: String) -> Control:
	var row := _add_row("gate", text, text)
	row.name = "GateStep"
	_set_live(row, true)
	_sync_live()
	return row


## The gate's line is done: [param text] with its tick.
func end_gate(row: Control, text: String) -> void:
	row.set_meta("kind", "step")
	_end(row, "success")
	_words(row).text = text
	_sync_live()


func _rows() -> Array:
	return _history.get_children().filter(func(row: Node) -> bool: return not _gone(row))


func _build_live() -> VBoxContainer:
	var live := VBoxContainer.new()
	live.name = "Live"
	live.theme_type_variation = "JoineryFlush"
	live.hide()
	var line := HBoxContainer.new()
	line.name = "Line"
	line.theme_type_variation = "JoineryRowTight"
	live.add_child(line)
	var cell := _cell()
	cell.name = "Cell"
	line.add_child(cell)
	var dot := UI.status_dot("working")
	dot.name = "Dot"
	cell.add_child(dot)
	var mark := STYLE.glyph("success")
	mark.name = "Mark"
	mark.hide()
	cell.add_child(mark)
	var words := UI.label("", "JoineryCaption")
	words.name = "Words"
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	words.mouse_filter = Control.MOUSE_FILTER_PASS
	line.add_child(words)
	var under := HBoxContainer.new()
	under.name = "Under"
	under.theme_type_variation = "JoineryRowTight"
	under.hide()
	live.add_child(under)
	under.add_child(_cell())
	var note := UI.label("", "JoineryFaint")
	note.name = "Note"
	note.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	note.mouse_filter = Control.MOUSE_FILTER_PASS
	under.add_child(note)
	return live


## The live line follows the newest step: its words, its dot (breathing while it runs) or
## its end mark, an edit's +/− lines; under it the call's progress, or a thought newer than
## the step. Only a real change fades in, so a steady line stays still.
func _sync_live() -> void:
	var step: Control = null
	var thought: Control = null
	for row: Node in _rows():
		if str(row.get_meta("kind", "")) == "thought":
			if thought == null:
				thought = row as Control
		else:
			step = row as Control
			break
	_live.visible = not _all and (step != null or thought != null)
	if not _live.visible:
		_stop_dot()
		return
	var words := _live_words()
	var cell := _live.get_node("Line/Cell") as Control
	var dot := cell.get_node("Dot") as CanvasItem
	var mark := cell.get_node("Mark") as TextureRect
	var said := ""
	var under := ""
	if step == null:
		# Before the first step, the agent's thought is the line, from the trail's own edge.
		said = _words(thought).text
		words.theme_type_variation = "JoineryFaint"
		cell.hide()
	else:
		said = _words(step).text
		words.theme_type_variation = "JoineryCaption"
		cell.show()
		var running := bool(step.get_meta("live", false))
		dot.visible = running
		mark.visible = not running
		if running:
			if not dot.has_meta("breathing"):
				dot.set_meta("breathing", true)
				UI_MOTION.breathe(dot)
		else:
			_stop_dot()
			var spec: Array = ENDS.get(str(step.get_meta("end", "success")), ENDS["success"])
			STYLE.set_glyph(mark, spec[0], spec[1])
		var note := step.find_child("Note", true, false) as Label
		if note.visible and not note.text.is_empty() and running:
			under = note.text
		elif thought != null:
			under = _words(thought).text
	_sync_size(step)
	words.text = said
	words.tooltip_text = said
	var note_label := _live.get_node("Under/Note") as Label
	note_label.text = under
	note_label.tooltip_text = under
	var under_row := _live.get_node("Under") as Control
	var was := _said.duplicate()
	_said = [step, said, under]
	under_row.visible = not under.is_empty()
	if was.is_empty():
		UI_MOTION.fade_in(_live)
		return
	if was[0] != step or str(was[1]) != said:
		UI_MOTION.fade_in(words)
	if under_row.visible and str(was[2]) != under:
		UI_MOTION.fade_in(under_row)


## The live line's "+12 −3", copied from its step.
func _sync_size(step: Control) -> void:
	var line := _live.get_node("Line") as HBoxContainer
	var old := line.get_node_or_null("Size")
	if old != null:
		line.remove_child(old)
		old.queue_free()
	if step == null or not step.has_meta("size"):
		return
	var counts: Array = step.get_meta("size")
	var size_row := _size_row(str(step.get_meta("file", "")), int(counts[0]), int(counts[1]))
	line.add_child(size_row)
	# The clock (the card's) stays last.
	for child: Node in line.get_children():
		if child is Label and child.name != "Words" and child != size_row:
			line.move_child(child, -1)


func _stop_dot() -> void:
	var dot := _live.get_node("Line/Cell/Dot") as CanvasItem
	if dot.has_meta("breathing"):
		dot.remove_meta("breathing")
		UI_MOTION.stop(dot)


func _live_words() -> Label:
	return _live.get_node("Line/Words") as Label


func _add_row(kind: String, text: String, detail: String) -> VBoxContainer:
	var row := VBoxContainer.new()
	row.theme_type_variation = "JoineryFlush"
	row.set_meta("kind", kind)
	row.set_meta("said", text)
	row.mouse_filter = Control.MOUSE_FILTER_PASS
	var line := HBoxContainer.new()
	line.name = "Line"
	line.theme_type_variation = "JoineryRowTight"
	row.add_child(line)
	var cell := _cell()
	cell.name = "Cell"
	line.add_child(cell)
	if kind == "thought":
		# A thought reads as narration: from the trail's own edge, no dot.
		cell.hide()
	else:
		cell.add_child(UI.status_dot("working"))
	var words := UI.label(text, "JoineryFaint" if kind == "thought" else "JoineryCaption")
	words.name = "Words"
	words.tooltip_text = detail if not detail.is_empty() else text
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	words.mouse_filter = Control.MOUSE_FILTER_PASS
	line.add_child(words)
	# Under the line, after the dot's column: the call's progress, a picture it made, a test
	# run's results.
	var under := HBoxContainer.new()
	under.name = "Under"
	under.theme_type_variation = "JoineryRowTight"
	under.hide()
	row.add_child(under)
	under.add_child(_cell())
	var body := VBoxContainer.new()
	body.theme_type_variation = "JoineryFlush"
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	under.add_child(body)
	var note := UI.label("", "JoineryFaint")
	note.name = "Note"
	note.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	note.mouse_filter = Control.MOUSE_FILTER_PASS
	note.hide()
	body.add_child(note)
	_history.add_child(row)
	_history.move_child(row, 0)
	return row


## The dot's column: as wide as a caption line is tall.
func _cell() -> CenterContainer:
	var cell := CenterContainer.new()
	STYLE.on_theme(
		cell,
		func() -> void:
			var side := float(UI_THEME.space(cell, 4))
			cell.custom_minimum_size = Vector2(side, side)
	)
	return cell


## "+12 −3" after an edit's words: what it added in the success colour, what it took out in
## the danger colour, in tabular digits.
func _show_size(row: Control, file: String, added: int, removed: int) -> void:
	if added == 0 and removed == 0:
		return
	var line := row.get_node("Line") as HBoxContainer
	line.add_child(_size_row(file, added, removed))
	row.set_meta("size", [added, removed])
	row.set_meta("file", file)


func _size_row(file: String, added: int, removed: int) -> HBoxContainer:
	var size_row := HBoxContainer.new()
	size_row.name = "Size"
	size_row.theme_type_variation = "JoineryRowTight"
	size_row.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	size_row.tooltip_text = "%s: %d lines added, %d removed" % [file, added, removed]
	size_row.mouse_filter = Control.MOUSE_FILTER_PASS
	for part: Array in [
		["+%d" % added, "JoineryAddedNumeric", added],
		["−%d" % removed, "JoineryRemovedNumeric", removed],
	]:
		if int(part[2]) == 0:
			continue
		var count := UI.label(str(part[0]), str(part[1]))
		count.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
		size_row.add_child(count)
	return size_row


## A render or texture the call made, small under its line. A picture that won't load shows
## nothing.
func _show_picture(row: Control, path: String) -> void:
	if path.is_empty() or row.find_child("Picture", true, false) != null:
		return
	var file := ProjectSettings.globalize_path(path) if path.begins_with("res://") else path
	if not FileAccess.file_exists(file):
		return
	var image := Image.load_from_file(file)
	if image == null or image.is_empty():
		return
	var picture := TextureRect.new()
	picture.name = "Picture"
	picture.texture = ImageTexture.create_from_image(image)
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT
	picture.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	picture.tooltip_text = path.get_file()
	picture.accessibility_name = "Picture: " + path.get_file()
	var ratio := float(image.get_width()) / maxf(1.0, float(image.get_height()))
	STYLE.on_theme(
		picture,
		func() -> void:
			var tall := UI_THEME.unit(picture) * PICTURE_UNITS
			picture.custom_minimum_size = Vector2(tall * ratio, tall)
	)
	var note := row.find_child("Note", true, false) as Label
	note.hide()
	note.get_parent().add_child(picture)
	(row.get_node("Under") as Control).show()


func _find(id: String) -> Control:
	if id.is_empty():
		return null
	for row: Node in _rows():
		if str(row.get_meta("trail_id", "")) == id:
			return row as Control
	return null


func _newest_live() -> Control:
	for row: Node in _rows():
		if bool(row.get_meta("live", false)):
			return row as Control
	return null


func _set_live(row: Control, on: bool) -> void:
	row.set_meta("live", on)


## Every live line of [param kind] ends as [param end].
func _settle_kind(kind: String, end: String) -> void:
	for row: Node in _rows():
		if str(row.get_meta("kind", "")) == kind:
			_end(row as Control, end)


## A live line ends: its dot becomes the tick (or the stop, or the error), and its words read
## as done: past tense on success, never a trailing ellipsis.
func _end(row: Control, kind: String) -> void:
	if not bool(row.get_meta("live", false)):
		return
	_set_live(row, false)
	row.set_meta("end", kind)
	var cell := row.get_node("Line/Cell") as CenterContainer
	for dot: Node in cell.get_children():
		cell.remove_child(dot)
		dot.queue_free()
	var spec: Array = ENDS.get(kind, ENDS["success"])
	var picture := STYLE.glyph(spec[1])
	STYLE.set_glyph(picture, spec[0], spec[1])
	cell.add_child(picture)
	var words := _words(row)
	words.text = past_tense(words.text) if kind == "success" else settled(words.text)
	words.tooltip_text = words.text if words.tooltip_text.is_empty() else words.tooltip_text
	# The progress under a finished call has said its piece; a picture and results stay.
	(row.find_child("Note", true, false) as Control).hide()
	var keeps := (
		row.find_child("Picture", true, false) != null
		or row.find_child("Results", true, false) != null
	)
	(row.get_node("Under") as Control).visible = keeps


static func _words(row: Node) -> Label:
	return row.get_node("Line/Words") as Label


static func _gone(row: Node) -> bool:
	return row.is_queued_for_deletion()


## "Exporting the model…" -> "Exported the model". Lines that don't start with a verb in
## "-ing" only lose their trailing ellipsis.
static func past_tense(text: String) -> String:
	var line := settled(text)
	var space := line.find(" ")
	var word := line if space < 0 else line.substr(0, space)
	var lower := word.to_lower()
	var stem := lower.trim_suffix("ing")
	if stem == lower or stem.length() < 2 or not RegEx.create_from_string("[aeiouy]").search(stem):
		return line
	var done: String = IRREGULAR.get(lower, "")
	if done.is_empty():
		if stem.ends_with("y") and not "aeiou".contains(stem[-2]):
			done = stem.left(-1) + "ied"
		elif stem.ends_with("e"):
			done = stem + "d"
		else:
			done = stem + "ed"
	if word[0] != lower[0]:
		done = done[0].to_upper() + done.substr(1)
	return done + line.substr(word.length())


## [param text] without its trailing ellipsis.
static func settled(text: String) -> String:
	var line := text.strip_edges()
	for tail: String in ["…", "..."]:
		line = line.trim_suffix(tail)
	return line.strip_edges()
