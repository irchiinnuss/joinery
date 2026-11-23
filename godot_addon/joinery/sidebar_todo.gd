@tool
extends Node
## A request's to-do in the sidebar (ADR 0060). Journeyman proposes it for a request with
## several steps; the host's "todo" events (chat_bridge.gd's todo_event) bring the card
## (todo_card.gd) in with the agents at work and move it on: waiting for Go, working,
## checking the "done when" items, and their results. Go sends the to-do as the user left it
## as a request of its own ("Go"), so it has its change card and review like any other;
## Cancel drops it. A newly proposed to-do opens expanded, for the user to read and edit; Go
## minimises it to its one line (with its live light while the work runs). The user can open
## or minimise it by hand; that choice is remembered for the chat's to-do (in the project's
## editor metadata) and holds when the card is shown again, until the next to-do, which opens
## expanded again.

const CARD := preload("todo_card.gd")
const UI_MOTION := preload("ui_motion.gd")
## Project metadata key: {chat_id: todo_id} for each chat whose to-do is minimised.
const MINIMISED_KEY := "todo_minimised"

## Where the minimised chats live outside the editor (tests, the visual suite).
static var _minimised_here := {}

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): the card sits in it.
var tasks: Object
## The to-do card, while there is one.
var card: Control
## What the last Go did: "sent", "queued", or why it couldn't start.
var last_go := ""
var _bridge: Object
## A Go pressed while Journeyman finished: its card in the sidebar's queue
## (sidebar_queued_card.gd), that queue, and the to-do it starts.
var _queued: Control
var _queue: Object
var _queued_id := ""


static func attach(sidebar: Control, list: Object) -> Node:
	var todo: Node = load("res://addons/joinery/sidebar_todo.gd").new()
	todo.set("dock", sidebar)
	todo.set("tasks", list)
	(list as Node).add_child(todo)
	return todo


func _init() -> void:
	name = "TodoList"


## Follow [param bridge] (chat_bridge.gd) for the host's to-do events.
func listen(bridge: Object) -> void:
	if bridge == null or bridge == _bridge:
		return
	_bridge = bridge
	if bridge.has_signal("todo_event") and not bridge.is_connected("todo_event", on_event):
		bridge.connect("todo_event", on_event)


## One event from the host: {todo_id, state, steps?, done_when?, results?, summary?}.
func on_event(event: Dictionary) -> void:
	var id := str(event.get("todo_id", ""))
	var state := str(event.get("state", ""))
	var same := is_instance_valid(card) and str(card.get("todo_id")) == id
	if not same:
		if state in ["dropped", "cancelled"] or not event.has("steps"):
			return
		_replace(id)
	var queued := _queued_go()
	card.call("apply", event)
	if queued and str(card.get("state")) == "waiting":
		card.call("set_starting")  # Its Go still waits its turn.
	elif queued:  # Dropped for a message sent ahead of it, or cancelled: Go has nothing to do.
		_unqueue()
	tasks.call("sync_visible")


func _replace(id: String) -> void:
	_unqueue()
	if is_instance_valid(card):
		card.queue_free()
	card = CARD.new(id)
	card.connect("go_pressed", _go)
	card.connect("cancel_pressed", _cancel)
	card.connect("close_pressed", close)
	card.connect("minimised_changed", func(on: bool) -> void: remember(_chat(), id, on))
	# A new to-do opens expanded; one shown again keeps the way it was left.
	card.call("set_minimised", minimised(_chat(), id))
	(tasks as Node).add_child(card)
	tasks.set("todo_card", card)
	(tasks as Control).show()
	UI_MOTION.enter(card)


## Go: the to-do as the user left it, sent as a request of its own. Pressed while Journeyman
## still finishes (the turn that proposed it is ending, a change waits for review, a message
## is ahead of it), Go waits in the queue like a typed message and goes, in its order, the
## moment the sidebar is free. The card says "Starting…" meanwhile; Cancel or Stop takes it
## back. [member last_go] says what happened.
func _go(line: Dictionary) -> void:
	last_go = ""
	if _bridge == null or not _bridge.has_method("hold_line") or not bool(_bridge.call("is_ready")):
		_refuse("Journeyman isn't connected.")
		return
	if _queued_go():  # Pressed again (from the phone, say): it already waits.
		last_go = "queued"
		return
	var view: Object = dock.get("_transcript")
	var turns: Object = (dock.get("_chats") as Object).get("turns")
	if not bool(dock.call("_occupied")) and not bool(turns.call("busy_in", view)):
		_send(line)
		return
	var queue: Object = view.get("queue")
	var queued: Variant = queue.call("push_action", "Go: start the to-do", _send_queued)
	if not queued is Control:
		_refuse("The queue is full. Remove a message, then press Go.")
		return
	_queued = queued
	_queue = queue
	_queued_id = str(card.get("todo_id"))
	_queued.connect("remove_pressed", _unqueue.bind(true))
	last_go = "queued"


func _send(line: Dictionary) -> void:
	_bridge.call("hold_line", line)
	dock.call("_start_request", "Go", [])
	last_go = "sent"
	# Go: the list gives way to its one line while the work runs.
	card.call("set_minimised", true)
	remember(_chat(), str(card.get("todo_id")), true)


## The queued Go's turn: send the to-do as it is now (edits made while it waited count).
func _send_queued() -> void:
	var id := _queued_id
	_forget_queued()
	if not is_instance_valid(card) or str(card.get("todo_id")) != id:
		return
	if str(card.get("state")) != "waiting":
		return
	var line: Dictionary = card.call("line")
	if line.is_empty():
		card.call("apply", {"state": "waiting"})
		return
	_send(line)


func _refuse(why: String) -> void:
	last_go = why
	dock.call("show_status", why, 3.0)
	card.call("apply", {"state": "waiting"})


## A Go of the to-do on show waits in the queue.
func _queued_go() -> bool:
	if not is_instance_valid(_queued) or not is_instance_valid(card):
		return false
	return _queued_id == str(card.get("todo_id"))


## Take the queued Go back out of the queue; [param reset] offers Go again on its card.
func _unqueue(reset := false) -> void:
	var leaving := _queued
	var queue := _queue
	var id := _queued_id
	_forget_queued()
	if is_instance_valid(leaving) and is_instance_valid(queue):
		queue.call("remove", leaving)
	if reset and is_instance_valid(card) and str(card.get("todo_id")) == id:
		if str(card.get("state")) == "waiting":
			card.call("apply", {"state": "waiting"})


func _forget_queued() -> void:
	_queued = null
	_queue = null
	_queued_id = ""


func _cancel() -> void:
	if _queued_go():  # Cancel first takes back a Go still waiting; the to-do stays.
		_unqueue(true)
		return
	if _bridge != null and _bridge.has_method("cancel_todo"):
		_bridge.call("cancel_todo", str(card.get("todo_id")))


func close() -> void:
	var leaving := card
	card = null
	tasks.set("todo_card", null)
	if not is_instance_valid(leaving):
		return
	UI_MOTION.exit(
		leaving,
		null,
		func() -> void:
			leaving.queue_free()
			tasks.call("sync_visible")
	)


## Whether [param chat_id]'s to-do [param todo_id] was left minimised (a different to-do: no).
static func minimised(chat_id: String, todo_id: String) -> bool:
	return str(_store().get(chat_id, "")) == todo_id and not todo_id.is_empty()


## Remember that [param chat_id]'s to-do [param todo_id] is minimised ([param on]) or open.
static func remember(chat_id: String, todo_id: String, on: bool) -> void:
	var chats := _store().duplicate()
	if on:
		chats[chat_id] = todo_id
	else:
		chats.erase(chat_id)
	if Engine.is_editor_hint():
		EditorInterface.get_editor_settings().set_project_metadata("joinery", MINIMISED_KEY, chats)
	else:
		_minimised_here = chats


static func _store() -> Dictionary:
	if Engine.is_editor_hint():
		var saved: Variant = EditorInterface.get_editor_settings().get_project_metadata(
			"joinery", MINIMISED_KEY, {}
		)
		return saved if saved is Dictionary else {}
	return _minimised_here


## The open chat ("" before the host names one).
func _chat() -> String:
	var id: Variant = _bridge.get("chat_id") if _bridge != null else null
	return str(id) if id != null else ""
