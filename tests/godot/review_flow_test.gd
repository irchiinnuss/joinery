extends SceneTree
## review_flow.gd: a live edit's review waits for Joinery's in_review, a dropped turn drops
## the preview, a file review opens on file_preview, and renders reach the panel and card. A
## parallel task's live edit follows that task's turn and never opens over the main request's.
## A change kept without asking opens no review: its card says so, with Undo. With live scene
## edits the flow runs the editor's own Accept, and Undo takes them back from the undo history.

const FLOW := preload("res://addons/joinery/review_flow.gd")
const CARD := preload("res://addons/joinery/sidebar_change_card.gd")

var _failed := false


## The chat's signals the flow follows (chat_bridge.gd).
class FakeChat:
	extends Node
	signal file_preview(event: Dictionary)
	signal turn_state(event: Dictionary)
	signal review_media(event: Dictionary)
	signal task_event(event: Dictionary)
	signal task_review(transaction_id: String)
	signal file_decision(event: Dictionary)
	var live: Array[bool] = []
	var decided: Array = []
	var rolled_back: Array[String] = []

	func decide_files(id: String, accept: bool, auto := false) -> void:
		decided.append([id, accept, auto])

	func rollback_files(id: String) -> void:
		rolled_back.append(id)

	func finish_live_review(accepted := true) -> void:
		live.append(accepted)


## The live preview's pending state (scene_edits.gd).
class FakeEdits:
	extends RefCounted
	var waiting := true
	var root: Node = null
	var stale := false

	func prepare_accept() -> Dictionary:
		return {"success": not stale}

	func pending() -> bool:
		return waiting

	func reject() -> void:
		waiting = false


## The request's card in the sidebar (sidebar_change_card.gd).
class FakeCard:
	extends RefCounted
	var state := "ready"
	var media: Array = []
	var done: Array = []
	var transaction_id := ""
	var outcome: Array = []
	var undo_live := Callable()

	func set_outcome(kind: String, text: String) -> void:
		outcome = [kind, text]

	func set_ready(items: Array) -> void:
		media = items

	func set_done(items: Array) -> void:
		done = items


## The sidebar (journeyman_dock.gd).
class FakeDock:
	extends RefCounted
	var card := FakeCard.new()
	var revertible_asked := 0

	func _card() -> Object:
		return card

	func refresh_revertible() -> void:
		revertible_asked += 1


## The review panel (review_panel.gd).
class FakePanel:
	extends RefCounted
	var media: Array = []

	func update_media(items: Array) -> void:
		media = items


## The Review tab (review_tab.gd).
class FakeTab:
	extends RefCounted
	var panel := FakePanel.new()

	func detail() -> Object:
		return panel


## The plugin's review entry points (plugin.gd).
class FakePlugin:
	extends RefCounted
	var calls: Array = []
	## The scene's undo history, and the mug its Accept moves; refuse: a stale preview.
	var history := UndoRedo.new()
	var mug: Node3D
	var refuse := false
	var _file_review: Dictionary = {}
	var _edits := FakeEdits.new()
	var _dock := FakeDock.new()
	var _review_tab := FakeTab.new()

	func _open_review(summary: String) -> void:
		calls.append(["open", summary])

	func _reject_scene_edit(message: String, kind: String) -> void:
		_edits.waiting = false
		calls.append(["reject", message, kind])

	func _call_dock(method: String, args: Array) -> void:
		calls.append([method, args[0]])

	func _show_edit_review_panel() -> void:
		calls.append(["panel"])

	func _on_review_row_applied(id: String, paths: PackedStringArray, _changes: Array) -> void:
		calls.append(["applied", id, paths])

	## The Accept button's live path (plugin.gd): one step in the scene's undo history.
	func _accept_scene_edit(quiet := false) -> void:
		calls.append(["accept", quiet])
		if refuse:
			return
		history.create_action("Put the mug on the table")
		history.add_do_property(mug, "position", Vector3(0, 1, 0))
		history.add_undo_property(mug, "position", Vector3.ZERO)
		history.commit_action(false)
		_edits.waiting = false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	var chat := FakeChat.new()
	root.add_child(chat)
	var plugin := FakePlugin.new()
	var flow: Node = FLOW.new()
	root.add_child(flow)
	flow.setup(plugin, chat)

	flow.live_ready("Rename the lamp")
	_check(flow.live_waiting(), "The live edit waits for its review")
	chat.turn_state.emit({"state": "working"})
	chat.turn_state.emit({"state": "submitting"})
	_check(plugin.calls.is_empty(), "Nothing opens before the change is in review")
	var parts := ["Made the lamp in Blender", "Put it on Desk"]
	chat.turn_state.emit({"state": "in_review", "done": parts})
	_check(plugin.calls == [["open", "Rename the lamp"]], "in_review opens the live review")
	_check(not flow.live_waiting(), "Opened once")
	_check(plugin._dock.card.done == parts, "The card lists what was done for each part")

	plugin.calls.clear()
	plugin._edits.waiting = true
	flow.live_ready("Add a chair")
	chat.turn_state.emit({"state": "idle", "reason": "failed"})
	_check(plugin.calls == [["reject", "", "info"]], "A failed turn drops the live preview")

	plugin.calls.clear()
	chat.turn_state.emit({"state": "in_review"})
	_check(plugin.calls.is_empty(), "No stale review opens later")

	plugin._edits.waiting = true
	flow.live_ready("Rename the lamp")
	var media := [{"path": "art/crate.blend", "media": "image", "after": "/x/after.png"}]
	chat.file_preview.emit(
		{"transaction_id": "t1", "summary": "Crate", "media": media, "media_pending": true}
	)
	_check(plugin._file_review.get("transaction_id") == "t1", "The file review is held")
	# The sidebar opens the panel itself, once the user isn't typing (review_opener.gd).
	_check(plugin.calls == [["show_file_review", "Crate"]], "The file review opens at once")
	plugin.calls.clear()
	chat.turn_state.emit({"state": "in_review", "transaction_id": "t1"})
	_check(plugin.calls.is_empty(), "With a file review, the live edit rides along with it")

	var rendered := [{"path": "art/crate.blend", "media": "image", "after": "/x/done.png"}]
	chat.review_media.emit({"transaction_id": "other", "media": []})
	_check(plugin._review_tab.panel.media.is_empty(), "Renders of another change are ignored")
	chat.review_media.emit({"transaction_id": "t1", "media": rendered})
	_check(plugin._review_tab.panel.media == rendered, "The panel takes the renders")
	_check(plugin._dock.card.media == rendered, "The card takes its thumbnail")
	_check(plugin._file_review["media"] == rendered, "The held review has them too")

	# A review opens before its renders exist: the real card waits for review_media quietly.
	var card: VBoxContainer = CARD.new("Weather the crate")
	root.add_child(card)
	card.set_ready([{"path": "art/crate.blend", "media": "image", "after": "/x/not_yet.png"}])
	_check(card.review_row().thumbnail() == null, "No thumbnail before the render exists")
	_check(not card.review_row().visible, "No empty picture row while the render runs")
	card.queue_free()

	# A task's live edit, once the main request is idle: its own turn moves its review.
	plugin.calls.clear()
	plugin._file_review = {}
	plugin._edits.waiting = true
	flow.live_ready("Move the lamp", "t1")
	chat.turn_state.emit({"state": "in_review"})
	_check(plugin.calls.is_empty(), "The main request's turn doesn't move a task's preview")
	chat.task_event.emit({"kind": "turn_state", "task_id": "t2", "state": "in_review"})
	_check(plugin.calls.is_empty(), "Nor does another task's")
	plugin._file_review = {"transaction_id": "m1"}
	chat.task_event.emit({"kind": "turn_state", "task_id": "t1", "state": "in_review"})
	_check(plugin.calls.is_empty(), "It never opens over the main request's file review")
	_check(not flow.owns_live(), "The main request's files are decided without it")
	plugin._file_review = {}
	flow.reopen_live()
	_check(plugin.calls == [["open", "Move the lamp"]], "It opens once that review is decided")
	plugin.calls.clear()
	chat.task_review.emit("")
	_check(plugin.calls == [["open", "Move the lamp"]], "Or from the task card's Review link")
	plugin.calls.clear()
	flow.live_ready("Move the rug", "t1")
	plugin._file_review = {"transaction_id": "m1"}
	var stopped := {"kind": "turn_state", "task_id": "t1", "state": "idle", "reason": "stopped"}
	chat.task_event.emit(stopped)
	_check(plugin.calls.is_empty(), "A stopped task leaves the main request's review alone")
	_check(not plugin._edits.waiting, "Its own preview goes")
	_check(chat.live == [false], "The host hears it was dropped")
	_check(flow.owns_live(), "The scene is free again")

	_check_auto_accepted(chat, plugin)
	_check_live_kept(chat, plugin, flow)

	flow.queue_free()
	chat.queue_free()
	await process_frame
	print("review flow: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## A change kept without asking (`[agents] auto_accept`): no review opens, the card takes the
## quiet line with Undo, and the files are rescanned as after an Accept.
func _check_auto_accepted(chat: FakeChat, plugin: FakePlugin) -> void:
	plugin.calls.clear()
	plugin._file_review = {}
	chat.file_decision.emit({"transaction_id": "a1", "accepted": true, "changes": []})
	_check(plugin.calls.is_empty(), "A user's own decision is the plugin's to show")
	var decision := {
		"transaction_id": "a1",
		"accepted": true,
		"auto_accepted": true,
		"message": "Accepted automatically",
		"changes": [{"path": "game/door.gd", "change": "modified"}],
	}
	chat.file_decision.emit(decision)
	var card: FakeCard = plugin._dock.card
	_check(card.transaction_id == "a1", "The card knows its change, for Undo")
	_check(card.outcome == ["auto", "Accepted automatically"], "The card says it was kept")
	_check(
		plugin.calls == [["applied", "a1", PackedStringArray(["game/door.gd"])]],
		"The files are picked up, and no review opens"
	)
	_check(plugin._dock.revertible_asked == 1, "Undo's availability is asked for")
	plugin.calls.clear()
	chat.file_decision.emit(decision.merged({"task_id": "t9"}))
	_check(plugin.calls.is_empty(), "A task's change is its own card's")
	var noted := decision.merged({"notes": ["Note: no texture painted yet (art/door.kra)"]})
	_check(
		(
			FLOW.auto_text(noted)
			== "Accepted automatically\nNote: no texture painted yet (art/door.kra)"
		),
		"What the reviewer noted shows under the kept change's line"
	)
	_check(FLOW.auto_text(decision) == "Accepted automatically", "No notes, the line alone")

	var real: VBoxContainer = CARD.new("Make the door open")
	root.add_child(real)
	real.transaction_id = "a1"
	real.set_outcome("auto", "Accepted automatically")
	_check(real.state == "accepted", "Kept is accepted")
	var undo: Button = real.get("_revert")
	_check(undo.visible and undo.text == "Undo", "One action: Undo")
	var asked: Array[String] = []
	real.revert_requested.connect(func(id: String) -> void: asked.append(id))
	undo.pressed.emit()
	_check(asked == ["a1"], "Undo is the change's own revert")
	real.revert_finished(true)
	_check(real.state == "reverted", "And it reads Reverted after")
	real.set_outcome("success", "Change accepted")
	_check(undo.text == "Revert", "A change the user accepted keeps Revert")
	real.queue_free()


## Live scene edits Joinery said may be kept (auto_accept hint): the flow runs the editor's own
## Accept without opening a review, alone or after the files; Undo is the scene's undo step.
func _check_live_kept(chat: FakeChat, plugin: FakePlugin, flow: Node) -> void:
	var mug := Node3D.new()
	root.add_child(mug)
	mug.position = Vector3(0, 1, 0)  # the preview on screen
	plugin.mug = mug
	plugin._edits.root = mug
	flow.set("history_for", func(_root: Variant) -> UndoRedo: return plugin.history)
	var card: FakeCard = plugin._dock.card
	card.outcome = []

	# Live edits alone.
	plugin.calls.clear()
	plugin._file_review = {}
	plugin._edits.waiting = true
	flow.live_ready("Put the mug on the table")
	chat.turn_state.emit({"state": "in_review", "auto_accept": true})
	_check(
		plugin.calls == [["accept", true]],
		"The editor's own Accept runs, quietly: %s" % [plugin.calls]
	)
	_check(card.outcome == ["auto", "Accepted automatically"], "The card says it was kept")
	_check(card.undo_live.is_valid(), "With Undo for the live edits")
	_check(str(card.undo_live.call()).is_empty(), "Undo goes through")
	_check(mug.position == Vector3.ZERO, "Undo puts the scene back")
	_check(not str(card.undo_live.call()).is_empty(), "Only once: the history moved on")

	# Without the hint the review opens as before.
	plugin.calls.clear()
	plugin._edits.waiting = true
	flow.live_ready("Put the mug on the table")
	chat.turn_state.emit({"state": "in_review"})
	_check(plugin.calls == [["open", "Put the mug on the table"]], "No hint: the review opens")

	# Files and live edits: the files are accepted first, as the Accept button does.
	plugin.calls.clear()
	plugin._edits.waiting = true
	mug.position = Vector3(0, 1, 0)
	flow.live_ready("Put a new mug on the table")
	var preview := {"transaction_id": "f1", "summary": "Mug", "auto_accept": true}
	chat.file_preview.emit(preview)
	_check(chat.decided == [["f1", true, true]], "Its files are accepted: %s" % [chat.decided])
	_check(plugin._file_review.is_empty() and plugin.calls.is_empty(), "No review opens")
	chat.turn_state.emit({"state": "in_review", "auto_accept": true})
	_check(plugin.calls.is_empty(), "The live edits wait for their files")
	var decision := {
		"transaction_id": "f1",
		"accepted": true,
		"auto_accepted": true,
		"message": "Accepted automatically",
		"changes": [{"path": "art/mug.glb", "change": "created"}],
	}
	chat.file_decision.emit(decision)
	_check(plugin.calls[0] == ["accept", true], "Then the live edits are kept")
	_check(plugin.calls[1][0] == "applied", "And the files picked up")
	_check(card.transaction_id == "f1" and card.undo_live.is_valid(), "Undo covers both")

	# The preview went stale before its files were decided: the review opens instead.
	plugin.calls.clear()
	chat.decided.clear()
	plugin._edits.waiting = true
	plugin._edits.stale = true
	flow.live_ready("Put a new mug on the table")
	chat.file_preview.emit(preview.merged({"transaction_id": "f2"}, true))
	_check(chat.decided.is_empty(), "Nothing is accepted")
	_check(plugin._file_review.get("transaction_id") == "f2", "The review opens as usual")
	plugin._edits.stale = false
	plugin._file_review = {}

	# Or it is refused once the files are in: they go back and the scene review opens.
	plugin.calls.clear()
	plugin._edits.waiting = true
	plugin.refuse = true
	flow.live_ready("Put a new mug on the table")
	chat.file_preview.emit(preview.merged({"transaction_id": "f3"}, true))
	chat.file_decision.emit(decision.merged({"transaction_id": "f3"}, true))
	_check(chat.rolled_back == ["f3"], "The files go back")
	_check(plugin.calls.has(["open", "Put a new mug on the table"]), "The scene review opens")
	plugin.refuse = false

	# The real card: Undo for live edits alone, with no files to revert.
	var real: VBoxContainer = CARD.new("Put the mug on the table")
	root.add_child(real)
	var undone: Array[bool] = []
	real.undo_live = func() -> String:
		undone.append(true)
		return ""
	real.set_outcome("auto", "Accepted automatically")
	var undo: Button = real.get("_revert")
	_check(undo.visible and undo.text == "Undo", "Undo shows for live edits alone")
	var asked: Array[String] = []
	real.revert_requested.connect(func(id: String) -> void: asked.append(id))
	undo.pressed.emit()
	_check(undone == [true] and asked.is_empty(), "Undo takes the live edits back, no file revert")
	_check(real.state == "reverted", "And it reads Reverted")
	real.queue_free()
	mug.queue_free()
	plugin.history.free()
