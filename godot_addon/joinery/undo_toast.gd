@tool
extends PanelContainer
## A short line above the message box right after a revert: "Reverted “Hedge has small
## leaves…”" with Undo, for the mistake noticed at once. It goes by itself after
## [constant SECONDS] (held while the pointer or the keyboard is on it), never takes focus, and
## Undo runs Undo revert for that change (transactions unrevert). One at a time: a newer
## revert replaces it. The change's card and Review row keep their own Undo revert after it
## goes.

## Undo was pressed for [param transaction_id].
signal undo_pressed(transaction_id: String)

const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const STYLE := preload("sidebar_style.gd")
const REVERT_ASK := preload("revert_ask.gd")
## How long it stays, in seconds.
const SECONDS := 8.0

## The change it offers to put back, "" while hidden.
var transaction_id := ""
var text: Label
var undo: Button
var _timer: Timer


func _init() -> void:
	name = "UndoToast"
	theme_type_variation = "JoineryCard"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Revert done"
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	add_child(row)
	var icon := STYLE.glyph()
	STYLE.set_glyph(icon, "reload", "text_muted")
	icon.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(icon)
	text = UI.label("", "JoineryCaption")
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	row.add_child(text)
	undo = UI.secondary("Undo")
	undo.name = "Undo"
	undo.accessibility_name = "Undo the revert"
	undo.tooltip_text = "Put the change back as it was accepted"
	undo.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	undo.pressed.connect(_on_undo)
	UI.bind_height(undo, "xs")
	row.add_child(undo)
	_timer = Timer.new()
	_timer.one_shot = true
	_timer.timeout.connect(_on_timeout)
	add_child(_timer)
	mouse_entered.connect(_hold.bind(true))
	mouse_exited.connect(_hold.bind(false))
	undo.focus_entered.connect(_hold.bind(true))
	undo.focus_exited.connect(_hold.bind(false))
	hide()


## Offer Undo for the revert of [param id], named [param summary].
func offer(id: String, summary: String) -> void:
	transaction_id = id
	var name_words := REVERT_ASK.short_name(summary)
	text.text = "Reverted “%s”" % name_words if not name_words.is_empty() else "Reverted"
	text.tooltip_text = summary
	undo.disabled = false
	if not visible:
		UI_MOTION.fade_in(self)
	show()
	if _timer.is_inside_tree():
		_timer.start(SECONDS)


## Take it away now (the change was put back elsewhere, or another action started).
func dismiss() -> void:
	_timer.stop()
	transaction_id = ""
	hide()


## Seconds left before it goes (0 while held or hidden).
func time_left() -> float:
	return _timer.time_left if _timer.is_inside_tree() and not _timer.is_stopped() else 0.0


func _hold(on: bool) -> void:
	if not visible or not _timer.is_inside_tree():
		return
	if on:
		_timer.stop()
	elif not undo.has_focus():
		_timer.start(SECONDS / 2.0)


func _on_timeout() -> void:
	if visible:
		UI_MOTION.exit(self, null, Callable(), false)
	transaction_id = ""


func _on_undo() -> void:
	var id := transaction_id
	if id.is_empty():
		return
	dismiss()
	undo_pressed.emit(id)
