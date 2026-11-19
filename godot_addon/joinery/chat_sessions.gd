@tool
extends Node
## Saved chats in the sidebar. Every conversation is saved by the chat host
## (<project>/.joinery/chats, see chat_store.gd); this puts them back on screen.
##
## The header gets two buttons beside the menu: Chats (the list, chats_page.gd) and New chat.
## When the editor opens, the most recent chat comes back as the open chat: its requests,
## replies and change cards, and the next message continues it. Opening an older chat from
## the list shows it read-only, with "Continue" in place of the message box; Continue makes it
## the open chat. Change cards show what became of each change now, from
## `joinery transactions list` (accepted, reverted, discarded, too old to revert).
##
## Continuing is the chat host's job: the next prompt names the chat, and the host resumes the
## provider's own session or starts a fresh one from a summary, which the transcript says.
##
## Each request's menu rewinds the chat to it (Edit and resend) or branches a new chat there
## (chat_rewind.gd, ADR 0080).
##
## New chat and opening another chat work while Journeyman is working. The chat it works in
## keeps running out of sight: its conversation view is kept as it is and goes on taking the
## replies, steps and change ([member _kept]); its change still opens for review. The Chats
## page marks it with a dot, and opening it again brings back that same view, live, with Stop.
##
## A message sent from another chat meanwhile starts at once as that chat's own turn, beside
## the first, while the project has an agent free ([agents] parallel, ADR 0036): the host runs
## it as a chat task that continues that chat's session and saves the turn in that chat
## (chat_turns.gd, [member turns]). Its replies, steps, Stop and change show in its own view,
## kept out of sight the same way when the user goes elsewhere, with its own dot on the Chats
## page; its change is its own row in the Review tab. Only when every agent is busy (or with
## parallel = 1) does the message wait in that chat's queue ("Sends when the other chat
## finishes") and go out once one is free. A message in a chat whose own turn is still working
## waits in its queue as always. One agent at a time owns the scene on screen (ADR 0036).

const TASKS := preload("sidebar_tasks.gd")
const VARIANTS := preload("sidebar_variants.gd")
const STORE := preload("chat_store.gd")
const PAGE := preload("chats_page.gd")
const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const STYLE := preload("sidebar_style.gd")
const COMMAND_SCRIPT := preload("command_job.gd")
const SLASH := preload("slash_commands.gd")
const ATTACHMENTS := preload("chat_attachments.gd")
const REWIND := preload("chat_rewind.gd")
const TURNS := preload("chat_turns.gd")
const NOTICES := preload("chat_notices.gd")
const PERSONA := "Journeyman"
const DISCARDED := "Discarded"
const QUEUED := "Send or remove this chat's queued message first."
## A kept view's change cards ([member _changes]) while it is out of sight.
const CHANGES_META := "joinery_changes"
## Decision line for each state `joinery transactions list` reports: [kind, text].
const STATES := {
	"accepted": ["success", "Change accepted"],
	"rejected": ["rejected", DISCARDED],
	"reverted": ["reverted", "Reverted"],
	"review": ["info", "Not reviewed"],
	"staging": ["info", "Not reviewed"],
	"new": ["info", "Not reviewed"],
	"gone": ["info", "This change is no longer on record"],
}
## The decision line of a change Joinery kept without asking (`[agents] auto_accept`).
const AUTO := ["auto", "Accepted automatically"]

var dock: Control
var page: PAGE
var chats_button: Button
var new_button: Button
## Stands in for the message box while an older chat is only being viewed.
var past_bar: MarginContainer
## The chat on screen ("" for a new one), and whether it is only being viewed.
var shown_id := ""
var viewing := false
## The project folder whose chats these are ("" for the open project; tests set it).
var project := ""
## Runs a Joinery CLI call: (args: PackedStringArray, done: Callable(result)). Unset, it runs
## `uv run joinery …` in the background; tests set it to answer themselves.
var runner := Callable()
## Rewind and branch from a request (chat_rewind.gd).
var rewinder: REWIND
## Messages from other chats running beside the one Journeyman works in (chat_turns.gd).
var turns: TURNS
## The chat the next message continues ("" starts a new one).
var _current_id := ""
var _bridge: Node
var _transcript: Control
var _composer: Control
var _past_text: Label
var _continue: Button
## transaction id -> [card, state shown]
var _changes: Dictionary = {}
## slot -> [job, callback]
var _jobs: Dictionary = {}
## Chats kept running out of sight: chat id ("" until the host names a new one) -> its view.
var _kept: Dictionary = {}


## Add the header buttons, the Chats page and the bar for older chats to the sidebar.
static func attach(sidebar: Control, header_row: Control, content: Control) -> Node:
	var sessions: Node = load("res://addons/joinery/chat_sessions.gd").new()
	sessions.name = "Chats"
	sessions.dock = sidebar
	sessions.call("_build", header_row, content)
	sidebar.add_child(sessions)
	return sessions


func _build(header_row: Control, content: Control) -> void:
	_transcript = dock.get("_transcript")
	_composer = dock.get("_composer")
	chats_button = UI.icon_button("chats", "Chats")
	chats_button.accessibility_name = "Show saved chats"
	chats_button.pressed.connect(open_list)
	header_row.add_child(chats_button)
	new_button = UI.icon_button("compose", "New chat")
	new_button.accessibility_name = "Start a new chat"
	new_button.pressed.connect(new_chat)
	header_row.add_child(new_button)
	page = PAGE.new()
	page.hide()
	page.closed.connect(func() -> void: page.set_open(false))
	page.chosen.connect(open_chat)
	page.new_chat_requested.connect(new_chat)
	page.rename_requested.connect(rename)
	content.add_child(page)
	past_bar = MarginContainer.new()
	past_bar.accessibility_name = "Earlier chat"
	past_bar.hide()
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	past_bar.add_child(row)
	_past_text = UI.label("", "JoineryCaption")
	_past_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_past_text.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(_past_text)
	_continue = UI.primary("Continue")
	_continue.accessibility_name = "Continue this chat"
	_continue.tooltip_text = "Make this the open chat and write your next message"
	_continue.pressed.connect(continue_chat)
	row.add_child(_continue)
	content.add_child(past_bar)
	# Right above the message box (its column, when it is held in one).
	var box: Control = _composer if _composer.get_parent() == content else _composer.get_parent()
	content.move_child(past_bar, box.get_index())
	dock.child_entered_tree.connect(_on_dock_child)
	rewinder = REWIND.new(self)
	turns = TURNS.new(dock, self)
	_hook(_transcript)
	if dock.has_signal("review_closed"):
		dock.connect("review_closed", _mark_working)


func _ready() -> void:
	set_process(false)
	for child: Node in dock.get_children():
		_on_dock_child(child)
	if Engine.is_editor_hint():
		restore_latest.call_deferred()


func _exit_tree() -> void:
	for slot: String in _jobs:
		_jobs[slot][0].cancel()
	_jobs.clear()


func _on_dock_child(node: Node) -> void:
	if node.has_signal("chat_changed") and node != _bridge:
		_bridge = node
		_bridge.connect("chat_changed", _on_chat_changed)
		if node.has_signal("provider_switch"):
			_bridge.connect("provider_switch", _on_provider_switch)
		_bridge.set("chat_id", _current_id)
		if node.has_signal("busy_changed"):
			_bridge.connect("busy_changed", func(_busy: bool) -> void: _mark_working())
		if node.has_signal("status_changed"):
			# Other chats' turns end with the connection; a new one starts none of them.
			_bridge.connect("status_changed", _on_status)


func _on_status(state: String, _detail: String) -> void:
	if state in ["error", "connecting"] and turns != null:
		turns.connection_lost()


## The agent ran out of quota mid-request (ADR 0080): one action under the transcript carries
## the request on with another signed-in agent; Stop, or leaving it, lets the request end.
## The project's own [agents] fallback_provider switches without asking, and says so.
func _on_provider_switch(event: Dictionary) -> void:
	var message := str(event.get("message", ""))
	match str(event.get("state", "")):
		"offer":
			var to := str(event.get("to", ""))
			var action := "Continue with " + str(event.get("to_name", to))
			# A weak reference: the chat may have closed by the time the user answers.
			var bridge := weakref(_bridge)
			var answer := func() -> void:
				var live: Object = bridge.get_ref()
				if live != null:
					live.call("switch_provider", to)
			dock.call("show_status", message, 600.0, action, answer)
		"switched":
			dock.call("show_status", "")
			_transcript.call("add_notice", message, "info")
		_:
			dock.call("show_status", "")


## The chat the next message continues ("" for a new one).
func current_id() -> String:
	return _current_id


# --- Actions -----------------------------------------------------------------------------------


## The editor opened: bring back the most recent chat as the open one.
func restore_latest() -> void:
	var saved := STORE.list(project)
	if not saved.is_empty() and _transcript.call("current_card") == null:
		_show(str(saved[0]["id"]), false)


## The Chats page, with this project's chats; the one Journeyman works in has a dot.
func open_list() -> void:
	page.show_chats(STORE.list(project), _current_id, -1, working_ids())
	page.set_open(true)


## Open [param chat_id]: the open chat as it is, any other read-only with Continue. A chat
## kept running out of sight comes back as it is now.
func open_chat(chat_id: String) -> void:
	if chat_id == shown_id and not viewing:
		page.set_open(false)
		return
	if _blocked():
		return
	page.set_open(false)
	if _kept.has(chat_id):
		_return_to(chat_id)
		return
	_leave()
	_show(chat_id, chat_id != _current_id)
	_follow_work()


## Clear the conversation: the next message starts a new chat (and a fresh agent session).
## A chat Journeyman is working in keeps running out of sight.
func new_chat() -> void:
	if _blocked():
		return
	page.set_open(false)
	_leave()
	_reset()
	shown_id = ""
	_set_current("")
	_set_viewing(false)
	var usage: Variant = dock.get("_header").get("usage")
	if usage is Object:
		usage.call("show_usage", {})
	_follow_work()
	_composer.get("prompt").grab_focus.call_deferred()


## The chat on screen becomes the open one; the next message continues it.
func continue_chat() -> void:
	if shown_id.is_empty():
		return
	_set_current(shown_id)
	_set_viewing(false)
	_composer.get("prompt").grab_focus.call_deferred()


## Rename a chat through `joinery chats rename`; the row already shows the new name.
func rename(chat_id: String, title: String) -> void:
	var args := _cli(
		["chats", "rename", "--project", _project(), "--id", chat_id, "--title", title]
	)
	_start(
		"rename",
		args,
		func(result: Dictionary) -> void:
			if int(result.get("code", 1)) != 0:
				dock.call("show_status", "Couldn't rename the chat.", 4.0)
				if page.visible:
					page.show_chats(STORE.list(project), _current_id)
	)


# --- Restoring a chat --------------------------------------------------------------------------


func _show(chat_id: String, read_only: bool) -> void:
	var data := STORE.read(chat_id, project)
	if data.is_empty():
		dock.call("show_status", "That chat is no longer saved.", 4.0)
		return
	# A restored chat appears whole; nothing in it is news. Motion comes back as it was.
	var key: String = UI_MOTION._ENABLED_META
	# Unset in the editor; get_meta with a null default would print an error.
	var motion: Variant = Engine.get_meta(key) if Engine.has_meta(key) else null
	UI_MOTION.set_enabled(false)
	_reset()
	render(data["entries"])
	if motion == null:
		Engine.remove_meta(key)
	else:
		Engine.set_meta(key, motion)
	shown_id = chat_id
	if not read_only:
		_set_current(chat_id)
	_set_viewing(read_only)
	if read_only:
		var meta: Dictionary = data["chat"]
		var parts := PackedStringArray(["Earlier chat"])
		if not str(meta.get("provider", "")).is_empty():
			parts.append(STORE.provider_name(str(meta["provider"])))
		for chat: Dictionary in STORE.list(project):
			if chat["id"] == chat_id:
				parts.append(STORE.when(int(chat["updated"])))
		_past_text.text = " · ".join(parts)
		_past_text.tooltip_text = str(meta.get("title", ""))
	_check_states()


## Put [param entries] (a chat file's lines after the first) back in the transcript.
func render(entries: Array) -> void:
	_changes.clear()
	var card: Control = null
	for entry: Variant in entries:
		if not entry is Dictionary:
			continue
		var text := str(entry.get("text", ""))
		match str(entry.get("type", "")):
			"user":
				card = _transcript.call("add_request", text, _targets(entry))
				card.call("set_sent")
			"step":
				if card != null:
					card.call("add_step", text)
			"refusal":
				# A call the gate refused: the card says why and never reads "Completed".
				if card != null:
					var refused := {"kind": "tool_activity", "allowed": false}
					card.call("add_trail", refused.merged(entry))
			"agent":
				_transcript.call("add_message", PERSONA, text)
			"notice":
				var level := str(entry.get("level", "info"))
				var kind := (
					"info" if level == "info" else ("divider" if level == "divider" else "error")
				)
				if kind == "error" and text.begins_with(NOTICES.POLICY_STOP):
					kind = "policy"  # A Rule Zero stop keeps its own notice, as it was shown.
				_transcript.call("add_notice", text, kind)
			"change":
				var id := str(entry.get("transaction_id", ""))
				if card != null and not id.is_empty():
					card.set("transaction_id", id)
					if entry.has("guessed") or entry.has("not_as_asked"):
						card.call(
							"set_departures",
							Array(_notes(entry.get("guessed", []))),
							Array(_notes(entry.get("not_as_asked", [])))
						)
					_changes[id] = [
						card,
						str(entry.get("state", "review")),
						bool(entry.get("auto", false)),
						_notes(entry.get("notes", [])),
					]
			"turn":
				var outcome := str(entry.get("outcome", "done"))
				var timing := {"outcome": outcome, "total_ms": int(entry.get("total_ms", 0))}
				_transcript.call("show_timing", timing)
				if card != null:
					card.call("finish", outcome)
			"variants":
				# A request tried several ways: what was kept, as the sidebar said it.
				var said := VARIANTS.saved_line(entry)
				if not said.is_empty():
					_transcript.call("add_notice", said, "info")
			"task":
				# A parallel task's outcome, as the sidebar said it when it finished. A way of a
				# request tried several ways is told by its group's line instead.
				if entry.has("variant_group"):
					continue
				var ending := str(TASKS.ENDINGS.get(str(entry.get("state", "")), ""))
				if not ending.is_empty():
					var line := "%s: %s" % [str(entry.get("title", "Task")), ending]
					_transcript.call("add_notice", line, "info")
			"rewind":
				_transcript.call(
					"add_notice", "Rewound to here. Accepted changes stay in the Timeline.", "info"
				)
			"session":
				if str(entry.get("mode", "")) == "summary":
					_transcript.call(
						"add_notice", "Continued with a summary; the agent starts fresh."
					)
	for id: String in _changes:
		_apply(id, str(_changes[id][1]), true)
	_transcript.call("finish_response")


## A change card's decision line for [param state] (from the chat, then from Joinery).
func _apply(id: String, state: String, revertible: bool) -> void:
	var card: Control = _changes[id][0]
	if not is_instance_valid(card):
		return
	var spec: Array = STATES.get(state, ["error", "This change didn't finish"])
	if state == "accepted" and _changes[id].size() > 2 and bool(_changes[id][2]):
		# What the reviewer noted stays under the line, one per line (ADR 0085).
		var lines := PackedStringArray([AUTO[1]])
		if _changes[id].size() > 3:
			lines.append_array(_changes[id][3])
		spec = [AUTO[0], "\n".join(lines)]
	card.call("set_outcome", spec[0], spec[1])
	if state == "accepted":
		card.call("set_revertible", revertible)
	_changes[id][1] = state


## Ask Joinery what became of each change on screen.
func _check_states() -> void:
	if _changes.is_empty():
		return
	_start(
		"states",
		_cli(["transactions", "list", "--project", _project()]),
		func(result: Dictionary) -> void:
			if int(result.get("code", 1)) == 0:
				show_states(SLASH.parse_json(str(result.get("output", ""))))
	)


## Show each change as `joinery transactions list` reports it ([param report] is its JSON). A
## change it no longer lists is no longer on record.
func show_states(report: Dictionary) -> void:
	var known := {}
	for entry: Variant in report.get("transactions", []):
		if entry is Dictionary:
			known[str(entry.get("id", ""))] = entry
	for id: String in _changes:
		var entry: Dictionary = known.get(id, {"state": "gone", "revertible": false})
		var state := str(entry.get("state", "gone"))
		var revertible := bool(entry.get("revertible", true))
		if is_instance_valid(_changes[id][0]) and _changes[id][0].has_method("describe"):
			# Revert's question names the change and counts its files.
			_changes[id][0].call("describe", entry)
		if bool(entry.get("auto_accepted", false)) and _changes[id].size() > 2:
			_changes[id][2] = true
			var notes := _notes(entry.get("auto_notes", []))
			if not notes.is_empty() and _changes[id].size() > 3:
				_changes[id][3] = notes
		if state != str(_changes[id][1]):
			_apply(id, state, revertible)
		elif state == "accepted" and is_instance_valid(_changes[id][0]):
			_changes[id][0].call("set_revertible", revertible)
		if bool(entry.get("unplayed", false)) and is_instance_valid(_changes[id][0]):
			# Accepted and nobody has played since: the card says so under its line.
			_changes[id][0].call("show_unplayed", {"ids": [id]})


# --- Helpers -----------------------------------------------------------------------------------


## A kept change's notes as saved or listed: the non-empty lines.
static func _notes(value: Variant) -> PackedStringArray:
	var notes := PackedStringArray()
	if value is Array:
		for note: Variant in value:
			if not str(note).strip_edges().is_empty():
				notes.append(str(note))
	return notes


## The message's chips again: its scene targets, and its images when they are still saved.
func _targets(entry: Dictionary) -> Array:
	var targets: Array = []
	for target: Variant in entry.get("targets", []):
		if target is Dictionary:
			targets.append(target)
	for path: Variant in entry.get("attachments", []):
		var file := str(path)
		if FileAccess.file_exists(file):
			var stem := file.get_file().get_basename()
			targets.append(
				{"attachment": "image", "id": "attachment:" + stem, "path": file, "label": stem}
			)
	return targets


## A turn joined the transcript: its request card gets the rewind menu.
func _offer_rewind(turn: Node) -> void:
	if is_instance_valid(turn) and turn.get_child_count() > 0 and turn.get_child(0) is Control:
		rewinder.offer(turn.get_child(0) as Control)


func _reset() -> void:
	_transcript.call("clear")
	_changes.clear()
	if dock.call("_turn") == _transcript:
		dock.set("_active_card", null)
		dock.set("_assistant_open", false)


func _set_current(chat_id: String) -> void:
	_current_id = chat_id
	if is_instance_valid(_bridge):
		_bridge.set("chat_id", chat_id)


func _set_viewing(on: bool) -> void:
	viewing = on
	STYLE.show_part(past_bar, on)
	_composer.visible = not on
	if on:
		UI_MOTION.fade_in(past_bar)


# --- Chats kept running out of sight ----------------------------------------------------------


## The chat Journeyman works in or whose change waits for review; "" when none (or when it is
## a new chat the host has not named yet).
func working_id() -> String:
	if not bool(dock.call("_occupied")):
		return ""
	var turn: Variant = dock.call("_turn")
	for id: String in _kept:
		if _kept[id] == turn:
			return id
	return _current_id if turn == _transcript and not viewing else ""


## The change card of [param transaction_id] in the chat on screen or one kept out of sight;
## null when none shows it.
func card_for(transaction_id: String) -> Control:
	for view: Control in [_transcript] + _kept.values():
		var card: Variant = (
			view.call("card_for", transaction_id) if is_instance_valid(view) else null
		)
		if card is Control:
			return card
	return null


## Every chat at work: the one Journeyman works in and those running beside it.
func working_ids() -> PackedStringArray:
	var ids := turns.chat_ids() if turns != null else PackedStringArray()
	var main := working_id()
	if not main.is_empty() and not ids.has(main):
		ids.insert(0, main)
	return ids


## The dots on the Chats page follow the work.
func _mark_working() -> void:
	if page.visible:
		page.set_working(working_ids())


## A message from a new chat ([param view]) started there and the host named the chat.
func chat_named(view: Control, chat_id: String) -> void:
	if view == _transcript:
		_set_current(chat_id)
		if not viewing:
			shown_id = chat_id
		return
	for id: String in _kept.keys():
		if _kept[id] == view and id != chat_id:
			_kept[chat_id] = view
			_kept.erase(id)


## Messages queued in a chat Journeyman is not working in go out only from the screen, so that
## chat stays until they are sent or removed. The chat it works in is kept, queue and all. The
## Chats list closes first, so the chat with its queue and the line saying why are in sight: a
## click is never swallowed behind the list.
func _blocked() -> bool:
	if _queued(_transcript) and dock.call("_turn") != _transcript:
		page.set_open(false)
		dock.call("show_status", QUEUED, 6.0)
		return true
	return false


## Journeyman is working (in any chat, a chat's own turn beside another's included), a change
## waits for review, or messages wait to go: rewinding and branching wait (chat_rewind.gd).
func _occupied() -> bool:
	var beside := turns != null and turns.working_count() > 0
	return bool(dock.call("_occupied")) or _queued(_transcript) or beside


static func _queued(view: Control) -> bool:
	var queue: Variant = view.get("queue")
	return queue is Object and int(queue.call("size_queued")) > 0


## Another chat takes the screen: the one shown stays, out of sight, when Journeyman works in
## it or messages wait in it. [param next] (a kept view) takes its place, or a fresh view when
## the shown one stays; otherwise the shown view is reused.
func _leave(next: Control = null) -> void:
	_prune()
	var old := _transcript
	var turn: Variant = dock.call("_turn")
	var keep: bool = turn == old and (bool(dock.call("_occupied")) or _queued(old))
	# Another chat's own turn works or its change waits there (chat_turns.gd).
	keep = keep or turns.holds(old)
	if keep:
		old.set_meta(CHANGES_META, _changes.duplicate())
		_kept[_current_id] = old
	if next == null:
		if not keep:
			return
		next = dock.call("make_transcript")
	_swap(next)
	_changes.clear()
	if not keep:
		if turn == old:
			# Journeyman's last request was in it, and is over.
			dock.set("_turn_log", null)
			dock.set("_active_card", null)
		old.queue_free()


## Bring back the kept chat [param chat_id] as it is now, live.
func _return_to(chat_id: String) -> void:
	var view: Control = _kept[chat_id]
	_kept.erase(chat_id)
	_leave(view)
	_changes = view.get_meta(CHANGES_META, {})
	view.remove_meta(CHANGES_META)
	shown_id = chat_id
	_set_current(chat_id)
	_set_viewing(false)
	_follow_work()
	dock.call("_drain")
	_composer.get("prompt").grab_focus.call_deferred()


## Send or Stop, and what the message box says, for the chat now on screen.
func _follow_work() -> void:
	dock.call("_sync_send_slot")
	dock.call("_sync")


## Free kept chats with nothing left to show live: no request, no review, no queued message.
func _prune() -> void:
	var turn: Variant = dock.call("_turn")
	var working := bool(dock.call("_occupied"))
	for id: String in _kept.keys():
		var view: Control = _kept[id]
		if not (view == turn and working) and not _queued(view) and not turns.holds(view):
			_kept.erase(id)
			view.queue_free()


## Show [param view] in place of the conversation view on screen, which is hidden.
func _swap(view: Control) -> void:
	var content := _transcript.get_parent()
	var index := _transcript.get_index()
	STYLE.show_part(_transcript, false)
	if view.get_parent() == null:
		content.add_child(view)
		_hook(view)
	content.move_child(view, index)
	STYLE.show_part(view, true)
	_transcript = view
	dock.set("_transcript", view)
	# Up in the message box recalls the shown chat's requests.
	_composer.call("recall_from", view)


## Rewind and recall for a conversation view (each view once).
func _hook(view: Control) -> void:
	_composer.call("recall_from", view)
	var body: Variant = view.get("_body")
	if body is Node:
		(body as Node).child_entered_tree.connect(
			func(turn: Node) -> void: _offer_rewind.call_deferred(turn)
		)


func _on_chat_changed(event: Dictionary) -> void:
	var chat_id := str(event.get("chat_id", ""))
	var turn: Variant = dock.call("_turn")
	if turn != _transcript:
		# The chat running out of sight was named (or moved on): the shown chat keeps its own.
		for id: String in _kept.keys():
			if _kept[id] == turn and id != chat_id:
				_kept[chat_id] = turn
				_kept.erase(id)
		if is_instance_valid(_bridge):
			_bridge.set("chat_id", _current_id)
	else:
		_current_id = chat_id
		if not viewing:
			shown_id = chat_id
	# Only a chat that continues from a summary is worth a notice: the agent starts
	# fresh. A new or resumed chat is what the user expects.
	var message := str(event.get("message", ""))
	if str(event.get("mode", "")) == "summary" and not message.is_empty():
		turn.call("add_notice", message, "info")


func _project() -> String:
	return (
		project if not project.is_empty() else ProjectSettings.globalize_path("res://").rstrip("/")
	)


static func _cli(words: Array) -> PackedStringArray:
	var args := PackedStringArray(
		["run", "--frozen", "--quiet", "--project", SLASH.engine_dir(), "joinery"]
	)
	for word: Variant in words:
		args.append(str(word))
	return args


func _start(slot: String, args: PackedStringArray, callback: Callable) -> void:
	if runner.is_valid():
		runner.call(args, callback)
		return
	if _jobs.has(slot):
		_jobs[slot][0].cancel()
	var job: RefCounted = COMMAND_SCRIPT.new()
	if job.start("uv", args) != OK:
		return
	_jobs[slot] = [job, callback]
	set_process(true)


func _process(_delta: float) -> void:
	for slot: String in _jobs.keys():
		var result: Dictionary = _jobs[slot][0].poll()
		if result.is_empty():
			continue
		var callback: Callable = _jobs[slot][1]
		_jobs.erase(slot)
		callback.call(result)
	if _jobs.is_empty():
		set_process(false)
