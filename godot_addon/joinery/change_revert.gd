@tool
extends HBoxContainer
## The trailing end of an accepted change's decision line (sidebar_change_card.gd): Revert (or
## Undo for a change kept without asking), "Reverting…", "Too old to revert", and on a
## reverted change Undo revert, then "Putting back…". Revert asks first, in place under the
## line (revert_ask.gd), and runs only on its answer ([signal revert_confirmed]). When Undo
## revert finds files edited since the revert, [method ask_conflict] asks Keep my files or
## Replace them under the line.

## The user answered Revert in the question.
signal revert_confirmed
## Undo revert was pressed, or Replace them answered ([param overwrite]).
signal unrevert_requested(overwrite: bool)

const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const INDICATOR := preload("activity_indicator.gd")
const REVERT_ASK := preload("revert_ask.gd")
## Shown instead of Revert once the change's restore data is gone.
const TOO_OLD := "Too old to revert"

var revert: Button
var undo_revert: Button
## "Reverting…" or "Putting back…" while Joinery works.
var working: HBoxContainer
var too_old: Label
## False once `joinery transactions list` says the restore data is gone.
var revertible := true
## Where a question goes while the line has no row around it.
var home: Control
var _working_text: Label
var _ask: Control


func _init() -> void:
	name = "RevertControls"
	theme_type_variation = "JoineryRowTight"
	revert = UI.quiet("Revert")
	revert.accessibility_name = "Revert this accepted change"
	revert.tooltip_text = "Put back the files from before this change"
	UI.bind_height(revert, "xs")
	add_child(revert)
	undo_revert = UI.quiet("Undo revert")
	undo_revert.name = "UndoRevert"
	undo_revert.accessibility_name = "Undo the revert: put this change back"
	undo_revert.tooltip_text = "Put this change back as it was accepted"
	undo_revert.pressed.connect(start_unrevert.bind(false))
	UI.bind_height(undo_revert, "xs")
	undo_revert.hide()
	add_child(undo_revert)
	working = HBoxContainer.new()
	working.theme_type_variation = "JoineryRowTight"
	var dots := INDICATOR.new()
	dots.set("running", true)
	working.add_child(dots)
	_working_text = UI.label("Reverting…", "JoineryCaption")
	_working_text.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	working.add_child(_working_text)
	working.hide()
	add_child(working)
	too_old = UI.label(TOO_OLD, "JoineryCaption")
	too_old.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	too_old.tooltip_text = "Its restore data was cleared. Ask Journeyman to change it back."
	too_old.mouse_filter = Control.MOUSE_FILTER_PASS
	too_old.hide()
	add_child(too_old)


func _notification(what: int) -> void:
	if what == NOTIFICATION_PREDELETE and is_instance_valid(_ask):
		_ask.queue_free()


## Revert on a [param file_change] the restore data still covers, or Undo of [param live]
## edits; Too old once the data is gone; Undo revert on a [param reverted] change.
func sync(file_change: bool, live: bool, reverted: bool) -> void:
	revert.visible = (file_change and revertible) or live
	revert.disabled = false
	too_old.visible = file_change and not revertible and not live
	undo_revert.visible = reverted
	working.hide()


## True while a revert or an Undo revert runs.
func busy() -> bool:
	return working.visible


## Joinery is working: [param words] ("Reverting…") replace the buttons until [method sync].
func start(words: String) -> void:
	close_ask()
	revert.hide()
	undo_revert.hide()
	_working_text.text = words
	working.accessibility_name = words
	working.show()
	UI_MOTION.fade_in(working)


## The question before Revert of [param summary] with [param files] files; asked again, it
## closes.
func ask_revert(summary: String, files: int) -> void:
	if asking() != null:
		close_ask()
		return
	_open(
		REVERT_ASK.for_revert(summary, files, revert.text),
		func(ok: bool) -> void:
			if ok:
				revert_confirmed.emit()
			elif revert.is_visible_in_tree():
				revert.grab_focus()
	)


## Undo revert found files edited since the revert; [param message] names them.
func ask_conflict(message: String) -> void:
	_open(
		REVERT_ASK.for_conflict(message),
		func(replace: bool) -> void:
			if replace:
				start_unrevert(true)
			elif undo_revert.is_visible_in_tree():
				undo_revert.grab_focus()
	)


## Undo revert; [param overwrite] replaces files edited since the revert.
func start_unrevert(overwrite: bool) -> void:
	start("Putting back…")
	unrevert_requested.emit(overwrite)


## The question open under the line, or null.
func asking() -> Control:
	return _ask if is_instance_valid(_ask) and not _ask.is_queued_for_deletion() else null


func close_ask() -> void:
	if is_instance_valid(_ask):
		_ask.queue_free()
	_ask = null


func _open(ask: Control, on_answer: Callable) -> void:
	close_ask()
	_ask = ask
	ask.connect("answered", on_answer)
	var row := get_parent() as Control
	var parent := row.get_parent() if row != null else null
	if parent == null:
		home.add_child(ask)
	else:
		parent.add_child(ask)
		parent.move_child(ask, row.get_index() + 1)
