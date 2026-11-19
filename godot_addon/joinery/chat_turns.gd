@tool
extends RefCounted
## Messages from other chats that run beside the chat Journeyman works in (chat tasks,
## ADR 0036). While Journeyman works in one chat, a message sent from another chat starts at
## once when the project has an agent free ([agents] parallel): the chat host runs it as that
## chat's own turn (`chat_task`), and its replies, steps, timing and change show in that chat's
## view, not as a task card. Each one:
##
## - starts as a request card in the chat it was sent from, "sending" until the host takes it;
## - keeps its view while it works or its change waits, out of sight like any kept chat, with
##   its dot on the Chats page (chat_sessions.gd);
## - has Stop in Send's slot while its chat is on screen ([method stop_in]);
## - offers its change in its chat ("Ready for review") and in the Review tab as its own row,
##   decided there like any task's (review_tasks.gd); the decision is a line in its chat.
##
## When every agent is busy the host answers `chat_wait` and the message goes back to its
## chat's queue, as before ("Sends when the other chat finishes"). A new chat is named by the
## host on its first message, and its kept view follows the name.

const SLASH := preload("slash_commands.gd")
const PERSONA := "Journeyman"
const DISCARDED := "Discarded"
const OTHER_WAIT := "Sends when the other chat finishes"
## Seconds a ready change waits before its review opens by itself (as a task's does).
const OPEN_DELAY := 0.8
const FINAL := ["accepted", "discarded", "done", "failed", "stopped"]

## The sidebar (journeyman_dock.gd) and its saved chats (chat_sessions.gd).
var dock: Control
var sessions: Node
## Sent, not yet taken by the host: ref -> {view, card, text, targets, chat}.
var _pending: Dictionary = {}
## Taken: task id -> {view, card, chat, state, open, offer, transaction_id}.
var _turns: Dictionary = {}
## Refs stopped before the host took them: each is cancelled the moment the host takes it.
var _stopped: Dictionary = {}


func _init(sidebar: Control, chats: Node) -> void:
	dock = sidebar
	sessions = chats


func _bridge() -> Object:
	var found: Variant = dock.get("_bridge")
	return found if found is Object and is_instance_valid(found) else null


## Turns of other chats working now, sent ones included: they take agents like tasks do.
func working_count() -> int:
	var count := _pending.size()
	for turn: Dictionary in _turns.values():
		if turn["state"] == "working":
			count += 1
	return count


## Whether a message sent from the chat on screen now starts at once beside the other chat:
## the project allows one more agent, and none of this chat's messages wait before it. With
## [param queued], the message is the oldest waiting in its queue.
func can_start(view: Control, queued := false) -> bool:
	var bridge := _bridge()
	if bridge == null or not bool(bridge.call("is_ready")) or busy_in(view):
		return false
	var queue: Variant = view.get("queue")
	if not queued and queue is Object and int(queue.call("size_queued")) > 0:
		return false
	var limit := int(bridge.get("parallel"))
	var tasks: Variant = dock.get("_tasks")
	var working := int(tasks.call("agents_count")) if tasks is Object else working_count()
	return limit > 1 and working + (1 if bool(dock.get("_busy")) else 0) < limit


## A turn of this chat is on its way or working in [param view].
func busy_in(view: Control) -> bool:
	return not _of(view, true).is_empty()


## [param view] has a turn working or a change waiting: it stays while out of sight.
func holds(view: Control) -> bool:
	return not _of(view, false).is_empty()


## The chats with a turn working beside the other one (for the Chats page's dots).
func chat_ids() -> PackedStringArray:
	var ids := PackedStringArray()
	for turn: Dictionary in _turns.values():
		if turn["state"] == "working" and not str(turn["chat"]).is_empty():
			ids.append(str(turn["chat"]))
	return ids


## Send [param text] from the chat on screen ([param view]) to run now beside the other chat.
func start(view: Control, text: String, targets: Array) -> bool:
	var bridge := _bridge()
	if bridge == null:
		return false
	var chat := str(sessions.call("current_id"))
	var card: Control = view.call("add_request", text, targets)
	card.call("set_sending")
	var ref := str(bridge.call("start_chat_task", chat, text, targets))
	if ref.is_empty():
		card.call("set_failed", "Journeyman isn't connected.")
		return false
	_pending[ref] = {"view": view, "card": card, "text": text, "targets": targets, "chat": chat}
	_follow()
	return true


## [param entry], sent from the chat on screen ([param view]), starts now as that chat's own turn
## when Journeyman works in another chat, it is a message (not a command) and an agent is free.
## False when it should wait in the queue instead.
func send_now(view: Control, entry: String, targets: Array, queued := false) -> bool:
	if not bool(dock.call("_parked")) or not bool(dock.call("_occupied")):
		return false
	if entry.begins_with("/") and not SLASH.is_prompt(entry):
		return false
	return can_start(view, queued) and start(view, entry, targets)


## [method send_now] for what the user just sent: when it starts, the message box clears.
func send_typed(view: Control, entry: String, targets: Array) -> bool:
	if not send_now(view, entry, targets):
		return false
	dock.get("_command_popup").call("close")
	(dock.get("_prompt") as TextEdit).clear()
	return true


## The oldest message waiting in [param view]'s queue starts beside the other chat once an
## agent is free; otherwise it waits on.
func drain(view: Control) -> void:
	var queue: Object = view.get("queue")
	var head: Control = queue.call("first")
	if head == null:
		return
	var targets: Variant = head.get("targets")
	if send_now(view, str(head.get("text")), targets if targets is Array else [], true):
		queue.call("remove", head)


## Stop the turn working in [param view]; false when none works there.
func stop_in(view: Control) -> bool:
	var found := _of(view, true)
	var bridge := _bridge()
	if found.is_empty() or bridge == null:
		return false
	if found.begins_with("ref:"):
		# Not taken yet: it never starts, and its card says so.
		var ref := found.trim_prefix("ref:")
		var waiting: Dictionary = _pending[ref]
		_pending.erase(ref)
		_stopped[ref] = true
		waiting["card"].call("set_failed")
	else:
		bridge.call("cancel_task", found)
	_follow()
	return true


## The connection to the chat host ended (or a new one starts): no turn of another chat goes on.
## Each one on its way or working ends failed, as Journeyman's own request does; a change waiting
## for review stays for the Review tab.
func connection_lost() -> void:
	for ref: String in _pending:
		var card: Control = _pending[ref]["card"]
		if is_instance_valid(card):
			card.call("set_failed")
	_pending.clear()
	_stopped.clear()
	for id: String in _turns.keys():
		var turn: Dictionary = _turns[id]
		if turn["state"] in ["", "working"]:
			_finish(turn["card"], "failed")
			_turns.erase(id)
	_follow()


## One event from the host (chat_bridge.gd's task_event). True when it belongs to a turn of
## another chat; the task cards never see those.
func take(event: Dictionary) -> bool:
	var kind := str(event.get("kind", ""))
	if kind == "chat_wait":
		_waits(event)
		return true
	var id := str(event.get("task_id", ""))
	if kind == "task" and str(event.get("origin", "")) == "chat":
		_apply(id, event)
		return true
	if not _turns.has(id):
		return false
	_show(_turns[id], event)
	return true


## The task's state: taken, named, in review, decided or over.
func _apply(id: String, event: Dictionary) -> void:
	if not _turns.has(id):
		var ref := str(event.get("ref", ""))
		if _stopped.has(ref):
			# The line was on its way when Stop was pressed: the host took it all the same.
			_stopped.erase(ref)
			var bridge := _bridge()
			if bridge != null:
				bridge.call("cancel_task", id)
			return
		if not _pending.has(ref):
			return
		var taken: Dictionary = _pending[ref]
		_pending.erase(ref)
		taken["state"] = ""
		taken["open"] = false
		taken["offer"] = null
		taken["transaction_id"] = ""
		_turns[id] = taken
		taken["card"].call("set_sent")
	var turn: Dictionary = _turns[id]
	var chat := str(event.get("chat_id", ""))
	if not chat.is_empty() and chat != str(turn["chat"]):
		# A new chat was named by its first message.
		turn["chat"] = chat
		sessions.call("chat_named", turn["view"], chat)
	var state := str(event.get("state", ""))
	if state == turn["state"]:
		return
	turn["state"] = state
	var view: Control = turn["view"]
	var card: Control = turn["card"]
	match state:
		"in_review":
			_offer(id, turn, event)
		"accepted":
			_drop_offer(turn)
			view.call("add_decision", "Change accepted", "success", card)
		"discarded":
			_drop_offer(turn)
			view.call("add_decision", DISCARDED, "rejected", card)
		"failed":
			var message := str(event.get("message", ""))
			if not message.is_empty():
				view.call("add_notice", message, "error")
			_finish(card, "failed")
		"stopped":
			_finish(card, "cancelled")
	if state in FINAL:
		_turns.erase(id)
	_follow()


## Its words, steps and timing go to its own chat's view and card.
func _show(turn: Dictionary, event: Dictionary) -> void:
	var view: Control = turn["view"]
	var card: Control = turn["card"]
	if not is_instance_valid(view):
		return
	var text := str(event.get("text", ""))
	match str(event.get("kind", "")):
		"message_delta":
			if not turn["open"] and is_instance_valid(card) and str(card.get("state")) == "working":
				card.call("reply_started")
			view.call(
				"append_delta",
				PERSONA,
				text,
				bool(event.get("new_message", false)) or not turn["open"]
			)
			turn["open"] = true
		"message":
			turn["open"] = false
			if str(event.get("level", "info")) == "warn":
				view.call("add_error", PERSONA, text)
			elif not text.is_empty():
				view.call("add_message", PERSONA, text)
		"notice":
			var level := str(event.get("level", "info"))
			view.call("add_notice", text, "error" if level == "warn" else "info")
		"progress":
			if is_instance_valid(card) and str(card.get("state")) == "working":
				card.call("add_step", text)
		"trail", "tests", "tool_activity":
			if is_instance_valid(card) and str(card.get("state")) == "working":
				card.call("add_tests" if event.get("kind") == "tests" else "add_trail", event)
		"file_preview":
			turn["transaction_id"] = str(event.get("transaction_id", ""))
			if is_instance_valid(card):
				card.set("transaction_id", turn["transaction_id"])
		"chat":
			# Like the open chat's: only a fresh start from a summary is worth a line.
			var said := str(event.get("message", ""))
			if str(event.get("mode", "")) == "summary" and not said.is_empty():
				view.call("add_notice", said, "info")
		"timing":
			turn["open"] = false
			var outcome := str(event.get("outcome", ""))
			var placed: bool = view.call("show_timing", event)
			var ending := (
				"done"
				if outcome == "done"
				else "cancelled" if outcome in ["stopped", "cancelled"] else "failed"
			)
			if ending == "cancelled" and not placed:
				view.call("add_decision", "Stopped", "stopped", card)
			_finish(card, ending)


## Its change waits: one line in its chat with Review, and the Review tab's row opens by itself
## once the user isn't typing, unless the main request's review is waiting.
func _offer(id: String, turn: Dictionary, event: Dictionary) -> void:
	var transaction := str(event.get("transaction_id", turn["transaction_id"]))
	turn["transaction_id"] = transaction
	if transaction.is_empty():
		return
	var summary := str(event.get("summary", ""))
	var line := "Ready for review: " + summary if not summary.is_empty() else "Ready for review"
	var show := _show_review.bind(transaction)
	turn["offer"] = turn["view"].call("add_offer", line, [["Review", show]])
	var opener: Variant = dock.get("_opener")
	if not opener is Object or not dock.is_inside_tree():
		return
	dock.get_tree().create_timer(OPEN_DELAY).timeout.connect(
		func() -> void:
			if _turns.has(id) and _turns[id]["state"] == "in_review":
				if not bool(dock.get("_review_pending")):
					opener.call("request", show, "task:" + transaction)
	)


func _show_review(transaction_id: String) -> void:
	var bridge := _bridge()
	if bridge != null and bridge.has_method("show_task_review"):
		bridge.call("show_task_review", transaction_id)


func _drop_offer(turn: Dictionary) -> void:
	var view: Control = turn["view"]
	if is_instance_valid(view) and is_instance_valid(turn["offer"]):
		view.call("remove_offer", turn["offer"])
	turn["offer"] = null


static func _finish(card: Control, outcome: String) -> void:
	if is_instance_valid(card) and str(card.get("state")) in ["working", "sending"]:
		card.call("finish", outcome)


## Every agent was busy after all: the message goes back to its chat's queue and waits.
func _waits(event: Dictionary) -> void:
	var ref := str(event.get("ref", ""))
	if not _pending.has(ref):
		return
	var waiting: Dictionary = _pending[ref]
	_pending.erase(ref)
	var view: Control = waiting["view"]
	var card: Control = waiting["card"]
	if not is_instance_valid(view):
		return
	if bool(event.get("full", false)):
		view.call("remove_request", card)
		var queue: Object = view.get("queue")
		queue.get("heading").text = OTHER_WAIT
		if not bool(queue.call("push", waiting["text"], waiting["targets"])):
			view.call("add_notice", "The queue is full. Remove a message or wait for a reply.")
	else:
		card.call("set_failed", str(event.get("text", "")))
	_follow()


## The id of the turn in [param view] ("ref:<ref>" while it is on its way), or "". With
## [param working], only a turn on its way or working; otherwise one waiting for review too.
func _of(view: Control, working: bool) -> String:
	for ref: String in _pending:
		if _pending[ref]["view"] == view:
			return "ref:" + ref
	for id: String in _turns:
		var turn: Dictionary = _turns[id]
		if turn["view"] == view and (turn["state"] in ["", "working"] or not working):
			return id
	return ""


## Send or Stop, the message box, the header's count and the Chats page follow.
func _follow() -> void:
	dock.call("_sync_send_slot")
	dock.call("_sync")
	var tasks: Variant = dock.get("_tasks")
	if tasks is Object:
		tasks.call("_sync")
	sessions.call("_mark_working")
	dock.call_deferred("_drain")
