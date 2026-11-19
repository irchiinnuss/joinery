@tool
extends RefCounted
## Undo revert in the sidebar (journeyman_dock.gd). Right after a revert, the toast above the
## message box offers Undo for a few seconds (undo_toast.gd); the change's card and the
## feature plan card offer Undo revert for as long as the change stays reverted. Each Undo runs
## `joinery transactions unrevert` through the slash commands, and the result goes back to the
## card ("Restored", or the question about files edited since the revert), the editor rescans
## the files that came back, and a failure with no card to show it is said in the chat. Every
## `joinery transactions list` also names each card's change for its Revert question, and
## brings a card up to date with a revert or an undo made elsewhere (the Timeline, a terminal).

const UNDO_TOAST := preload("undo_toast.gd")
const STYLE := preload("sidebar_style.gd")

## "Reverted “…”" with Undo, above the message box for a few seconds after a revert.
var toast: UNDO_TOAST
var _dock: Control
var _slash: Node
## Each change's summary, by transaction id, from the last `joinery transactions list`.
var _summaries := {}
## The change the toast's Undo is putting back without a card to show it, or "".
var _from_toast := ""


## [param composer] is the message box: the toast sits right above it.
func _init(dock: Control, slash: Node, composer: Control) -> void:
	_dock = dock
	_slash = slash
	toast = UNDO_TOAST.new()
	toast.undo_pressed.connect(undo)
	# Into the sidebar's own column, right before the message box's (centred) row.
	var content: Control = dock.get("_content")
	var row: Node = composer
	while row.get_parent() != content:
		row = row.get_parent()
	content.add_child(toast)
	content.move_child(toast, row.get_index())
	STYLE.centered_column(toast)
	_slash.connect("reverted", on_reverted)
	_slash.connect("unreverted", _on_unreverted)
	_slash.connect("changes_listed", _on_listed)


## A revert finished: the card and the plan card say so; on success the editor reloads what
## came back ([signal journeyman_dock.change_reverted]) and Undo is offered for a few seconds.
func on_reverted(id: String, ok: bool, message: String) -> void:
	var card := _card(id)
	if card != null:
		card.call("revert_finished", ok, message)
	var plan := _plan(id)
	if plan != null:
		plan.call("set_reverted", ok, message)
	if not ok:
		return
	_dock.emit_signal("change_reverted", id)
	var summary := str(_summaries.get(id, ""))
	if card != null:
		summary = str(card.call("display_name"))
	elif plan != null:
		summary = (plan.get("title_label") as Label).text
	toast.offer(id, summary)


## Undo revert for [param id] (the toast's Undo): through its card when the chat shows one,
## so the card says "Putting back…".
func undo(id: String) -> void:
	var card := _card(id)
	if card != null and str(card.get("state")) == "reverted":
		card.call("undo_revert")
		return
	_from_toast = id
	_slash.call("unrevert", id)


func _on_unreverted(
	id: String, ok: bool, message: String, conflict: Array, files: PackedStringArray
) -> void:
	var card := _card(id)
	if card != null:
		card.call("unrevert_finished", ok, message, conflict)
	var plan := _plan(id)
	if plan != null:
		plan.call("set_unreverted", ok, message, conflict)
	if toast.transaction_id == id:
		toast.dismiss()
	if ok and not _dock.get_signal_connection_list("files_changed").is_empty():
		# The change's files are back on disk: the editor scans them.
		_dock.emit_signal("files_changed", id, files)
	elif ok and Engine.is_editor_hint():
		EditorInterface.get_resource_filesystem().scan()
	elif not ok and id == _from_toast and card == null and plan == null:
		var choose := (
			" Undo revert in the Review tab lets you choose." if not conflict.is_empty() else ""
		)
		var why := ("Couldn't undo the revert. " + message).strip_edges()
		_dock.get("_transcript").call("add_notice", why + choose, "error")
	if id == _from_toast:
		_from_toast = ""


func _on_listed(entries: Array) -> void:
	for entry: Variant in entries:
		if not entry is Dictionary:
			continue
		var id := str((entry as Dictionary).get("id", ""))
		_summaries[id] = str((entry as Dictionary).get("summary", ""))
		var card := _card(id)
		if card == null:
			continue
		card.call("describe", entry)
		# Reverted or put back elsewhere (the Timeline, the Review tab, a terminal).
		var now := str((entry as Dictionary).get("state", ""))
		var state := str(card.get("state"))
		if bool(card.call("reverting")):
			continue
		if state == "accepted" and now == "reverted":
			card.call("revert_finished", true)
		elif state == "reverted" and now == "accepted":
			card.call("unrevert_finished", true)


## The change card of [param id] in the chat on screen, or in one kept out of sight (a revert
## may finish after the user went to another chat).
func _card(id: String) -> Control:
	return _dock.get("_chats").call("card_for", id) as Control


func _plan(id: String) -> Control:
	var plan: Variant = _dock.get("_plan_card")
	if is_instance_valid(plan) and plan is Control and str(plan.get("transaction_id")) == id:
		return plan as Control
	return null
