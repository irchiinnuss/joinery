@tool
extends RefCounted
## Revert and Undo revert on the Review tab's decided rows (review_tab.gd). An accepted row
## offers Revert while its change can still be put back ("Too old to revert" once the data is
## gone); a reverted row offers Undo revert. Revert asks first, in place under the row
## (revert_ask.gd), and so does Undo revert when files were edited since the revert. Progress
## reads "Reverting…" or "Putting back…". With the sidebar's slash commands attached, the
## sidebar runs both, so the chat's change card follows; the tab's [signal
## review_tab.revert_requested] and [signal review_tab.unrevert_requested] say so either way.
## Every button carries a focus key, so a re-render keeps the focus where it was.

const UI := preload("ui_components.gd")
const INDICATOR := preload("activity_indicator.gd")
const REVERT_ASK := preload("revert_ask.gd")
const TOO_OLD := "Too old to revert"

## Rows asking a question in place: {id: ["revert"] | ["conflict", Joinery's message]}.
var asking: Dictionary = {}
var _tab: Control


func _init(tab: Control) -> void:
	_tab = tab


## Add to the row's [param line] (and under it, to [param box]) what row [param id] offers.
func add_to(line: Control, box: Control, id: String, entry: Dictionary) -> void:
	var state := str(entry.get("state", ""))
	var busy := str(_busy().get(id, ""))
	var title := str(entry.get("summary", "")).strip_edges()
	if busy == "reverting" and state == "accepted":
		line.add_child(_working("Reverting…"))
	elif busy == "unreverting" and state == "reverted":
		line.add_child(_working("Putting back…"))
	elif state == "accepted" and bool(entry.get("revertible", true)):
		var revert := UI.quiet("Revert")
		revert.name = "Revert"
		revert.set_meta("focus_key", id + ":revert")
		revert.tooltip_text = "Put back the files from before this change"
		revert.accessibility_name = "Revert " + title
		revert.pressed.connect(ask_revert.bind(id))
		line.add_child(revert)
	elif state == "accepted":
		var old := UI.label(TOO_OLD, "JoineryFaint")
		old.name = "TooOld"
		old.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
		old.tooltip_text = "Its restore data was cleared. Ask Journeyman to change it back."
		old.mouse_filter = Control.MOUSE_FILTER_PASS
		old.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		line.add_child(old)
	elif state == "reverted":
		var undo := UI.quiet("Undo revert")
		undo.name = "UndoRevert"
		undo.set_meta("focus_key", id + ":unrevert")
		undo.tooltip_text = "Put this change back as it was accepted"
		undo.accessibility_name = "Undo the revert of " + title
		undo.pressed.connect(unrevert.bind(id, false))
		line.add_child(undo)
	if asking.has(id):
		box.add_child(_question(id, entry))


## Revert on row [param id]: the question opens under it; pressed again, it closes.
func ask_revert(id: String) -> void:
	if _busy().has(id):
		return
	if asking.has(id):
		asking.erase(id)
	else:
		asking[id] = ["revert"]
	_errors().erase(id)
	_tab.call("_render")
	_tab.call("_restore_focus", id + (":ask-cancel" if asking.has(id) else ":revert"))


## Revert row [param id] now (its question was answered Revert).
func revert(id: String) -> void:
	if _busy().has(id):
		return
	_busy()[id] = "reverting"
	_errors().erase(id)
	_tab.call("_render")
	_tab.emit_signal("revert_requested", id)
	var slash: Variant = _tab.get("_slash")
	if slash is Object:
		(slash as Object).call("revert", id)


## Undo revert on row [param id]; [param overwrite] replaces files edited since the revert.
func unrevert(id: String, overwrite: bool) -> void:
	if _busy().has(id):
		return
	asking.erase(id)
	_busy()[id] = "unreverting"
	_errors().erase(id)
	_tab.call("_render")
	_tab.emit_signal("unrevert_requested", id, overwrite)
	var slash: Variant = _tab.get("_slash")
	if slash is Object and (slash as Object).has_method("unrevert"):
		(slash as Object).call("unrevert", id, overwrite)


## A revert finished (started here or elsewhere).
func reverted(id: String, ok: bool, message: String) -> void:
	_busy().erase(id)
	_errors().erase(id)
	if not ok:
		_errors()[id] = ("Couldn't revert. " + message).strip_edges()
	_tab.call("_render")
	_tab.call("refresh")


## Undo revert finished. One started here that found files edited since the revert
## ([param conflict]) asks about them under its row: Keep my files or Replace them.
func unreverted(id: String, ok: bool, message: String, conflict: Array) -> void:
	var mine := str(_busy().get(id, "")) == "unreverting"
	_busy().erase(id)
	_errors().erase(id)
	if mine and not ok and not conflict.is_empty():
		asking[id] = ["conflict", message]
	elif mine and not ok:
		_errors()[id] = ("Couldn't undo the revert. " + message).strip_edges()
	_tab.call("_render")
	_tab.call("refresh")


func _question(id: String, entry: Dictionary) -> Control:
	var asked: Array = asking[id]
	var conflict := str(asked[0]) == "conflict"
	var question: Control = (
		REVERT_ASK.for_conflict(str(asked[1]))
		if conflict
		else REVERT_ASK.for_revert(str(entry.get("summary", "")), int(entry.get("file_count", 0)))
	)
	question.get("cancel").set_meta("focus_key", id + ":ask-cancel")
	question.get("action").set_meta("focus_key", id + ":ask-action")
	question.connect(
		"answered",
		func(ok: bool) -> void:
			asking.erase(id)
			if ok and conflict:
				unrevert(id, true)
			elif ok:
				revert(id)
			else:
				_tab.call("_render")
				_tab.call("_restore_focus", id + (":unrevert" if conflict else ":revert"))
	)
	return question


func _working(words: String) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	row.name = "Working"
	var dots := INDICATOR.new()
	dots.running = true
	row.add_child(dots)
	row.add_child(UI.label(words, "JoineryCaption"))
	row.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	return row


func _busy() -> Dictionary:
	return _tab.get("_busy")


func _errors() -> Dictionary:
	return _tab.get("_errors")
