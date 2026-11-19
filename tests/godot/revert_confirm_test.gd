extends SceneTree
## Revert is hard to press by mistake and easy to undo.
##
## Every Revert asks first, in place (revert_ask.gd): what goes back, with Cancel focused, and
## Escape answers Cancel. A reverted change offers Undo revert on its card, the feature plan
## card and the Timeline's revert entry; files edited since the revert are asked about, Keep
## my files or Replace them. Right after a revert, the toast above the message box offers
## Undo for a few seconds (undo_toast.gd).

const ASK := preload("res://addons/joinery/revert_ask.gd")
const CARD := preload("res://addons/joinery/sidebar_change_card.gd")
const PLAN := preload("res://addons/joinery/feature_plan_card.gd")
const TOAST := preload("res://addons/joinery/undo_toast.gd")
const TIMELINE := preload("res://addons/joinery/timeline_tab.gd")
const HEDGE := "Hedge has small leaves sticking out of its top and sides instead of a flat surface"
const ID := "d211d140527445bfa1d272820de134c6"

var _failed := false
var _host: VBoxContainer


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 3) -> void:
	for _i: int in count:
		await process_frame


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	_host = VBoxContainer.new()
	_host.size = Vector2(420, 900)
	root.add_child(_host)
	_check_words()
	await _check_card()
	await _check_conflict()
	await _check_plan()
	await _check_toast()
	await _check_timeline()
	_host.queue_free()
	await process_frame
	print("revert confirm: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_words() -> void:
	_check(
		(
			ASK.revert_words(HEDGE, 7)
			== (
				"Revert “Hedge has small leaves…”? Its 7 files go back to how"
				+ " they were before it. You can undo this."
			)
		),
		"Says plainly what goes back: " + ASK.revert_words(HEDGE, 7)
	)
	_check(ASK.revert_words("Add a crate", 1).contains("Its file goes back"), "One file")
	_check(ASK.revert_words("Add a crate", 0).contains("Its files go back"), "Count not known")
	_check(ASK.revert_words("", 2).begins_with("Revert this change?"), "A change with no name")
	_check(ASK.revert_words("Add a crate", 2, "Undo").begins_with("Undo “"), "Undo's own word")


## The chat's change card: Revert asks, Cancel and Escape keep it, Revert in the question runs
## it; once reverted, Undo revert puts it back.
func _check_card() -> void:
	var card: CARD = CARD.new("Make the hedge leafy")
	_host.add_child(card)
	card.transaction_id = ID
	card.describe({"summary": HEDGE, "file_count": 7})
	card.set_outcome("success", "Change accepted")
	var reverts: Array[String] = []
	var undos: Array = []
	card.revert_requested.connect(func(id: String) -> void: reverts.append(id))
	card.unrevert_requested.connect(func(id: String, o: bool) -> void: undos.append([id, o]))
	var revert: Button = card.get("_revert")
	revert.pressed.emit()
	await _frames()
	var ask: Control = card.get("_line").asking()
	_check(ask != null and ask.is_visible_in_tree(), "Revert asks first, in place")
	_check(ask != null and ask.get("text").text.contains("Its 7 files go back"), "Names the files")
	_check(ask != null and ask.get("cancel").has_focus(), "Focus starts on Cancel")
	_check(
		ask != null and ask.get_index() == card.get("_outcome").get_index() + 1,
		"The question sits right under the decision line"
	)
	_check(reverts.is_empty(), "Nothing runs before the answer")
	var escape := InputEventKey.new()
	escape.keycode = KEY_ESCAPE
	escape.physical_keycode = KEY_ESCAPE
	escape.pressed = true
	root.push_input(escape)
	await _frames()
	_check(card.get("_line").asking() == null, "Escape answers Cancel")
	_check(reverts.is_empty() and card.state == "accepted", "Escape reverts nothing")
	revert.pressed.emit()
	await _frames()
	card.get("_line").asking().get("cancel").pressed.emit()
	await _frames()
	_check(reverts.is_empty() and card.state == "accepted", "Cancel reverts nothing")
	revert.pressed.emit()
	await _frames()
	card.get("_line").asking().get("action").pressed.emit()
	_check(reverts == [ID], "Revert in the question reverts that change")
	_check(card.reverting(), "It says Reverting… until Joinery confirms")
	card.revert_finished(true)
	var undo: Button = card.get("_line").undo_revert
	_check(card.state == "reverted" and undo.is_visible_in_tree(), "Reverted offers Undo revert")
	undo.pressed.emit()
	_check(undos == [[ID, false]], "Undo revert asks Joinery to put it back")
	_check(card.reverting(), "It says Putting back… meanwhile")
	card.unrevert_finished(true)
	_check(card.state == "accepted", "Put back, the change is accepted again")
	_check(card.get("_outcome_text").text == "Restored", "And says Restored")
	_check(revert.is_visible_in_tree(), "With Revert again")
	card.queue_free()
	await _frames()


## Files edited since the revert are never replaced silently.
func _check_conflict() -> void:
	var card: CARD = CARD.new("Make the hedge leafy")
	_host.add_child(card)
	card.transaction_id = ID
	card.set_outcome("reverted", "Reverted")
	var undo: Button = card.get("_line").undo_revert
	_check(undo.is_visible_in_tree(), "A card restored as reverted offers Undo revert")
	var undos: Array = []
	card.unrevert_requested.connect(func(id: String, o: bool) -> void: undos.append([id, o]))
	undo.pressed.emit()
	var message := "joinery.lock was changed since the revert. Putting the change back would…"
	card.unrevert_finished(false, message, ["joinery.lock"])
	await _frames()
	var ask: Control = card.get("_line").asking()
	_check(ask != null and ask.name == "RevertConflict", "The conflict is asked in place")
	_check(ask != null and ask.get("text").text == message, "In Joinery's words")
	_check(ask != null and ask.get("cancel").has_focus(), "Keep my files is focused")
	_check(card.state == "reverted", "Nothing changed meanwhile")
	if ask != null:
		ask.get("action").pressed.emit()
	_check(undos.back() == [ID, true], "Replace them overwrites (Joinery saves them first)")
	card.queue_free()
	await _frames()


## The feature plan card's Revert asks too, and its revert can be undone.
func _check_plan() -> void:
	var plan: PLAN = PLAN.new()
	plan.build({"changes": ["game/hedge.tscn", "game/hedge.gd"]}, "Add the hedge trimmer")
	_host.add_child(plan)
	plan.set_applied("Added the hedge trimmer.", ID)
	var asked: Array = []
	plan.revert_pressed.connect(func(id: String) -> void: asked.append(id))
	plan.unrevert_pressed.connect(func(id: String, o: bool) -> void: asked.append([id, o]))
	var revert: Button = plan.get("_revert")
	revert.pressed.emit()
	await _frames()
	var ask: Control = plan.asking()
	_check(ask != null and ask.get("text").text.contains("Its 2 files go back"), "Plan asks")
	_check(asked.is_empty(), "Plan reverts nothing before the answer")
	ask.get("action").pressed.emit()
	_check(asked == [ID], "Plan reverts on the answer")
	plan.set_reverted(true)
	var undo: Button = plan.get("_undo_revert")
	_check(undo.is_visible_in_tree(), "A reverted plan offers Undo revert")
	undo.pressed.emit()
	_check(asked.back() == [ID, false], "Undo revert on the plan")
	plan.set_unreverted(true)
	_check(plan.texts().has("Restored") and revert.is_visible_in_tree(), "Plan restored")
	plan.queue_free()
	await _frames()


## Right after a revert: "Reverted “…”" with Undo, for a few seconds, never taking focus.
func _check_toast() -> void:
	var toast: TOAST = TOAST.new()
	_host.add_child(toast)
	var pressed: Array[String] = []
	toast.undo_pressed.connect(func(id: String) -> void: pressed.append(id))
	var before := root.gui_get_focus_owner()
	toast.offer(ID, HEDGE)
	await _frames()
	_check(toast.visible and toast.text.text.begins_with("Reverted “Hedge"), "The toast says it")
	_check(toast.time_left() > TOAST.SECONDS - 1.0, "For a few seconds")
	_check(root.gui_get_focus_owner() == before, "It never takes focus")
	toast.undo.pressed.emit()
	_check(pressed == [ID] and not toast.visible, "Undo puts that change back, and it goes")
	toast.offer(ID, HEDGE)
	toast.call("_on_timeout")
	await _frames()
	_check(not toast.visible and toast.transaction_id.is_empty(), "It goes by itself")
	toast.queue_free()
	await _frames()


## The Timeline's revert entry of a change still reverted offers Undo revert.
func _check_timeline() -> void:
	var tab: Control = TIMELINE.new()
	tab.set("cli_enabled", false)
	tab.size = Vector2(420, 600)
	_host.add_child(tab)
	await _frames()
	var now := Time.get_unix_time_from_system()
	var revert := {
		"commit": "3d82c76" + "0".repeat(33),
		"short": "3d82c76",
		"subject": "Revert: " + HEDGE,
		"author": "Ada",
		"time": now - 60,
		"files": 8,
		"reverted_change": ID
	}
	var plain := {
		"commit": "172a3a9" + "0".repeat(33),
		"short": "172a3a9",
		"subject": HEDGE,
		"author": "Ada",
		"time": now - 120,
		"files": 8
	}
	tab.call("apply_listing", {"state": "ready", "entries": [revert, plain]})
	await _frames()
	var rows: Array = tab.call("entry_rows")
	_check(rows.size() == 2, "Both entries listed")
	if rows.size() == 2:
		_check((rows[0] as Node).find_child("UndoRevert", true, false) != null, "Undo revert")
		_check((rows[1] as Node).find_child("UndoRevert", true, false) == null, "Only there")
	var message := "joinery.lock was changed since the revert."
	tab.call("_on_unreverted", {"error": message, "conflict": ["joinery.lock"]}, ID)
	await _frames()
	var ask := tab.find_child("RevertConflict", true, false) as Control
	_check(ask != null and ask.get("text").text == message, "The Timeline asks about edits")
	tab.queue_free()
	await _frames()
