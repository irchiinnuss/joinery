@tool
extends Node
## What a paired phone asks of the sidebar (Joinery Remote, ADR 0093). With the editor open, the
## chat host hands the sidebar the actions it owns as "remote_action" (chat_bridge.gd), and this
## does each one through the sidebar's own handlers, as if the user had clicked: a message is sent
## or queued like a typed one, Stop is the Stop button, Go is the to-do card's Go, and Accept or
## Discard on the change in review is the review's own button, with its checks (unsaved scenes,
## live scene edits on screen). So the cards, the queue and the live preview stay right, and the
## Timeline entry names the device. Each action is answered ("remote_ack") with what came of it,
## and the transcript says which device did it. When a phone's request still holds the project
## the editor opens ("handover"), the transcript offers "Stop it now" or "Wait".

const SLASH := preload("slash_commands.gd")
const NOT_HERE := "That isn't waiting at the editor any more."

var _plugin: Object
var _chat: Node
## The "Stop it now / Wait" line while a phone's request holds the project.
var _handover_row: Control


## Follow [param chat] (chat_bridge.gd) for [param plugin] (plugin.gd); placed under [param parent].
static func attach(parent: Node, plugin: Object, chat: Node) -> Node:
	var relay: Node = load("res://addons/joinery/remote_relay.gd").new()
	relay.set("_plugin", plugin)
	relay.set("_chat", chat)
	relay.name = "RemoteRelay"
	parent.add_child(relay)
	if chat.has_signal("remote_action"):
		chat.connect("remote_action", Callable(relay, "on_action"))
	if chat.has_signal("handover"):
		chat.connect("handover", Callable(relay, "on_handover"))
	return relay


## The project is still a phone's: {state: waiting|stopping|done, text}.
func on_handover(event: Dictionary) -> void:
	var dock := _dock()
	if dock == null:
		return
	var turn: Object = dock.call("_turn")
	var text := str(event.get("text", ""))
	if is_instance_valid(_handover_row):
		turn.call("remove_offer", _handover_row)
	_handover_row = null
	if str(event.get("state", "")) != "waiting":
		turn.call("add_notice", text, "info")
		return
	_handover_row = turn.call(
		"add_offer", text, [["Stop it now", _stop_phone_request], ["Wait", _wait_for_phone]]
	)


func _stop_phone_request() -> void:
	_chat.call("stop_phone_request")
	_wait_for_phone()


func _wait_for_phone() -> void:
	var dock := _dock()
	if dock != null and is_instance_valid(_handover_row):
		(dock.call("_turn") as Object).call("remove_offer", _handover_row)
	_handover_row = null


## One action: {ref, action: {kind, ...}, device: {id, name}}.
func on_action(event: Dictionary) -> void:
	var ref := str(event.get("ref", ""))
	var action: Dictionary = event.get("action", {}) if event.get("action") is Dictionary else {}
	var device: Dictionary = event.get("device", {}) if event.get("device") is Dictionary else {}
	var name := str(device.get("name", "A paired device"))
	var answer: Array = _do(action, name, str(device.get("id", "")))
	_chat.call("remote_ack", ref, bool(answer[0]), str(answer[1]))
	if bool(answer[0]) and not str(answer[2]).is_empty():
		var dock := _dock()
		if dock != null:
			(dock.call("_turn") as Object).call("add_notice", str(answer[2]), "info")


## [ok, text for the device, line for the transcript].
func _do(action: Dictionary, name: String, device_id := "") -> Array:
	var dock := _dock()
	var answer: Array = [false, "Not available remotely. Use the editor.", ""]
	if dock == null:
		answer = [false, "The sidebar isn't open.", ""]
	else:
		var kind := str(action.get("kind", ""))
		var todo := str(action.get("todo_id", ""))
		match kind:
			"prompt":
				answer = _send(dock, str(action.get("text", "")).strip_edges(), name, action)
			"cancel":
				answer = _stop(dock, bool(action.get("all", false)), name)
			"todo_go", "todo_cancel":
				answer = _todo(dock, todo, kind == "todo_go", name)
			"decision":
				var tx := str(action.get("transaction_id", ""))
				answer = _decide(dock, tx, bool(action.get("accept")), name, device_id)
	return answer


func _send(dock: Control, text: String, name: String, action: Dictionary) -> Array:
	if text.is_empty():
		return [false, "Write a message first.", ""]
	var chat_id := str(action.get("chat_id", ""))
	if not chat_id.is_empty() and chat_id != str(_chat.get("chat_id")):
		return [false, "Another chat is open at the editor. Send it there as a chat task.", ""]
	if text.begins_with("/") and not SLASH.is_prompt(text):
		return [false, "Commands run at the editor.", ""]
	var transcript: Object = dock.get("_transcript")
	var turns: Object = (dock.get("_chats") as Object).get("turns")
	if bool(dock.call("_occupied")) or bool(turns.call("busy_in", transcript)):
		# The same rule as a message typed now: it waits its turn in the queue.
		var queue: Object = transcript.get("queue")
		if not bool(queue.call("push", text, [])):
			return [false, "The queue is full. Wait for a reply.", ""]
		# The queued card says it already; a line in the running reply would only interrupt it.
		return [true, "Queued: it goes when Journeyman finishes.", ""]
	(dock.call("_turn") as Object).call("add_notice", "Sent from %s" % name, "info")
	dock.call("_start_request", text, [])
	return [true, "Sent.", ""]


func _stop(dock: Control, all: bool, name: String) -> Array:
	if not bool(dock.get("_busy")):
		return [false, "Journeyman isn't working on anything.", ""]
	dock.call("_request_cancel")
	if all:
		_chat.call("cancel_tasks")
	return [true, "Stopping.", "Stopped from %s" % name]


func _todo(dock: Control, todo_id: String, go: bool, name: String) -> Array:
	var tasks: Object = dock.get("_tasks")
	var card: Variant = tasks.get("todo_card") if tasks != null else null
	if not card is Control or not is_instance_valid(card) or str(card.get("todo_id")) != todo_id:
		return [false, NOT_HERE, ""]
	if not go:
		(card as Control).call("_on_cancel")
		return [true, "Cancelled.", "To-do cancelled from %s" % name]
	if bool(dock.call("_occupied")):
		return [false, "Wait for Journeyman to finish, then press Go.", ""]
	(card as Control).call("_on_go")
	return [true, "Going.", "Go from %s" % name]


func _decide(
	dock: Control, transaction_id: String, accept: bool, name: String, device_id := ""
) -> Array:
	var review: Dictionary = _plugin.get("_file_review")
	if str(review.get("transaction_id", "")) != transaction_id or transaction_id.is_empty():
		return [false, NOT_HERE, ""]
	_chat.set("decided_from", name)
	if not device_id.is_empty():
		_chat.set("decided_via", "remote:" + device_id)
	dock.call("_on_accept" if accept else "_on_reject")
	if not bool(_plugin.get("_deciding")):
		# The review's own checks refused (an unsaved scene, a stale live preview): the
		# Review panel says why at the editor.
		_chat.set("decided_from", "")
		_chat.set("decided_via", "")
		return [false, "The editor couldn't do it. Look at the machine.", ""]
	return [true, "Done.", "Accepted from %s" % name if accept else "Discarded from %s" % name]


func _dock() -> Control:
	var dock: Variant = _plugin.get("_dock")
	return dock as Control if dock is Control and is_instance_valid(dock) else null
