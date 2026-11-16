@tool
extends VBoxContainer
## Journeyman's work trail on a request's card (sidebar_change_card.gd): what it is doing, as
## it happens, newest first. Every agent's tool calls arrive as the host's "trail" events
## (bridge/trail.py), so the lines read the same whichever agent runs: "Reading level.gd",
## "Searching for Spawn", "Modelling the mug in Blender: bevel, 2 segments". Joinery's own
## progress ("Checking the change…") is a line of its own, or, while a call runs, the quiet
## line under it ("Running scene tests: 3 of 8"). The agent's short thoughts between the steps
## sit among them in the faint colour.
##
## Only the newest live line pulses (its dot breathes); every other line is still, with its tick
## or its error. An edit shows its file's +/− line count; a call that made a picture shows it
## small under its line. While the work runs the newest [constant LIVE_ROWS] lines show; the
## whole trail shows when the card's fold is opened.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
## Lines on show while the work runs, newest first.
const LIVE_ROWS := 5
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


func _init() -> void:
	name = "Trail"
	theme_type_variation = "JoineryTight"
	accessibility_name = "What Journeyman is doing"


## A progress line from Joinery or the agent: the step before it gets its tick and this one
## is the live line. [param detail] is the whole text on hover.
func add_line(text: String, detail := "") -> void:
	_settle_kind("step", "success")
	var row := _add_row("step", text, detail)
	_set_live(row, true)
	_sync_pulse()


## The host's trail event: {id, text, state: running|done|failed, note: tool|thought, file,
## added, removed, picture}.
func add_call(event: Dictionary) -> void:
	var text := str(event.get("text", "")).strip_edges()
	if str(event.get("note", "")) == "thought":
		if not text.is_empty():
			_add_row("thought", text, text)
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
	_sync_pulse()


## Progress while a call runs goes under its line ("Running scene tests: 3 of 8"). False when
## no call is live, so [param text] becomes a line of its own.
func on_progress(text: String) -> bool:
	var row := _newest_live()
	if row == null or str(row.get_meta("kind")) != "call":
		return false
	for echo: String in ECHOES:
		if text.begins_with(echo):
			return true
	var note := row.find_child("Note", true, false) as Label
	note.text = text
	note.tooltip_text = text
	var under := row.get_node("Under") as Control
	if not under.visible:
		under.show()
		UI_MOTION.fade_in(under)
	return true


## Every live line ends as [param kind] ("success", "stopped" or "error"); the art gate's line
## keeps waiting.
func settle(kind: String) -> void:
	for row: Node in get_children():
		if str(row.get_meta("kind", "")) != "gate":
			_end(row as Control, kind)
	_sync_pulse()


## All the lines ([param all], when the card's fold is opened), or the newest few.
func show_all(all: bool) -> void:
	var shown := 0
	for row: Node in get_children():
		if _gone(row):
			continue
		(row as Control).visible = all or shown < LIVE_ROWS
		shown += 1


## The newest line's top row, where the card's clock rides while the work runs.
func live_line() -> HBoxContainer:
	for row: Node in get_children():
		if not _gone(row):
			return row.get_node("Line") as HBoxContainer
	return null


## The words of the lines on show, newest first.
func texts() -> PackedStringArray:
	var shown := PackedStringArray()
	for row: Node in get_children():
		if (row as Control).visible and not _gone(row):
			shown.append(_words(row).text)
	return shown


## Lines that are steps or calls (thoughts aside), for "5 steps".
func step_count() -> int:
	var count := 0
	for row: Node in get_children():
		if str(row.get_meta("kind", "")) != "thought" and not _gone(row):
			count += 1
	return count


## True while [param text] is on show as the newest live line or the quiet line under it.
func shows(text: String) -> bool:
	var row := _newest_live()
	if row == null:
		return false
	var note := row.find_child("Note", true, false) as Label
	return _words(row).text == text or (note.is_visible_in_tree() and note.text == text)


## The art gate's line ("Waiting for the look"); it pulses until [method end_gate].
func add_gate(text: String) -> Control:
	var row := _add_row("gate", text, text)
	row.name = "GateStep"
	_set_live(row, true)
	_sync_pulse()
	return row


## The gate's line is done: [param text] with its tick.
func end_gate(row: Control, text: String) -> void:
	row.set_meta("kind", "step")
	_end(row, "success")
	_words(row).text = text
	_sync_pulse()


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
	# Under the line, after the dot's column: the call's progress and a picture it made.
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
	body.add_child(note)
	add_child(row)
	move_child(row, 0)
	UI_MOTION.stagger([row])
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
	line.add_child(size_row)
	row.set_meta("size", [added, removed])


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
	UI_MOTION.fade_in(picture)


func _find(id: String) -> Control:
	if id.is_empty():
		return null
	for row: Node in get_children():
		if str(row.get_meta("trail_id", "")) == id:
			return row as Control
	return null


func _newest_live() -> Control:
	for row: Node in get_children():
		if bool(row.get_meta("live", false)):
			return row as Control
	return null


func _set_live(row: Control, on: bool) -> void:
	row.set_meta("live", on)


## Every live line of [param kind] ends as [param end].
func _settle_kind(kind: String, end: String) -> void:
	for row: Node in get_children():
		if str(row.get_meta("kind", "")) == kind:
			_end(row as Control, end)


## A live line ends: its dot becomes the tick (or the stop, or the error), and its words read
## as done: past tense on success, never a trailing ellipsis.
func _end(row: Control, kind: String) -> void:
	if not bool(row.get_meta("live", false)):
		return
	_set_live(row, false)
	var cell := row.get_node("Line/Cell") as CenterContainer
	for dot: Node in cell.get_children():
		UI_MOTION.stop(dot as CanvasItem)
		cell.remove_child(dot)
		dot.queue_free()
	var spec: Array = ENDS.get(kind, ENDS["success"])
	var picture := STYLE.glyph(spec[1])
	STYLE.set_glyph(picture, spec[0], spec[1])
	cell.add_child(picture)
	var words := _words(row)
	words.text = past_tense(words.text) if kind == "success" else settled(words.text)
	words.tooltip_text = words.text if words.tooltip_text.is_empty() else words.tooltip_text
	if row.find_child("Picture", true, false) == null:
		# The progress under a finished call has said its piece.
		(row.get_node("Under") as Control).hide()


## Only the newest live line pulses; every other dot stays still.
func _sync_pulse() -> void:
	var newest := _newest_live()
	for row: Node in get_children():
		var cell := row.get_node("Line/Cell")
		for dot: Node in cell.get_children():
			if not dot is UI.StatusDot:
				continue
			if row == newest:
				if not dot.has_meta("breathing"):
					dot.set_meta("breathing", true)
					UI_MOTION.breathe(dot as CanvasItem)
			elif dot.has_meta("breathing"):
				dot.remove_meta("breathing")
				UI_MOTION.stop(dot as CanvasItem)


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
