# gdlint: disable=max-public-methods
@tool
extends PanelContainer
## Journeyman, the sidebar chat. Top to bottom: the header (the connection banner and one setup
## notice when needed, and the settings menu), the transcript, and the composer with the status
## line in its top-left; transcript and composer share one centred column (MAX_W.column). The
## Review panel is built here but lives in the bottom panel ([method take_review_panel]);
## decisions are made only there. The persona is always "Journeyman"; the provider (Codex,
## Claude…) is named only in the lost-connection banner and in Settings.
##
## Sending is optimistic: the request card appears at once as "sending" (Stop in Send's slot)
## and resolves when Journeyman picks it up, or offers Retry. A request lost with the
## connection turns the header dot red and disables Send, leaving Retry as the one action. A
## chat bridge added as a child is attached automatically ([method attach_bridge]).
## While a request runs or a change waits for review, Send queues the message (sidebar_queue.gd);
## queued messages go out one at a time once nothing is in the way ([method _drain]). Agents
## working alongside (parallel tasks) have their own cards above the composer (sidebar_tasks.gd).
## "/feature add|remove <id>" shows a plan card; the feature is applied only on Apply. "/brief"
## opens design/brief.md read-only. Brief suggestions arrive one line at a time, never acted on.

signal setup_requested
signal focus_toggle_requested
signal toolbar_toggle_requested
signal prompt_submitted(text: String, targets: Array)
signal accept_edit_requested
signal reject_edit_requested
signal cancel_requested
signal reconnect_requested
## "Connect" in the first-run banner.
signal connect_requested
signal art_review_requested
signal review_requested
signal review_closed
signal selection_options_changed(selection: Dictionary)
signal models_refresh_requested
signal target_opened(path: String)
## An accepted change was reverted on disk; the editor should rescan and reload its files.
signal change_reverted(transaction_id: String)
## A sidebar change (a feature) wrote [param paths] on disk; unheard, the dock rescans itself.
signal files_changed(transaction_id: String, paths: PackedStringArray)

const UI_THEME := preload("res://addons/joinery/ui_theme.gd")
const STYLE := preload("res://addons/joinery/sidebar_style.gd")
const HISTORY_SCRIPT := preload("res://addons/joinery/chat_history.gd")
const SETTINGS_SCRIPT := preload("res://addons/joinery/journeyman_settings.gd")
const CHIP_SCRIPT := preload("res://addons/joinery/target_chip.gd")
const HEADER_SCRIPT := preload("res://addons/joinery/sidebar_header.gd")
const FOOTER_SCRIPT := preload("res://addons/joinery/sidebar_footer.gd")
const COMPOSER_SCRIPT := preload("res://addons/joinery/sidebar_composer.gd")
const MENU_SCRIPT := preload("res://addons/joinery/command_menu.gd")
const SLASH_SCRIPT := preload("res://addons/joinery/slash_commands.gd")
const CHATS_SCRIPT := preload("res://addons/joinery/chat_sessions.gd")
const TASKS_SCRIPT := preload("res://addons/joinery/sidebar_tasks.gd")
const SUGGESTIONS := preload("res://addons/joinery/sidebar_suggestions.gd")
const OPENER := preload("res://addons/joinery/review_opener.gd")
const REVIEW_PATH := "res://addons/joinery/review_panel.gd"
const REVIEW_WAIT := "Next message…"
## While another chat works and no agent is free (chat_turns.gd), a message sent here waits:
## the queue's heading, and the message box's shorter hint.
const OTHER_WAIT := "Sends when the other chat finishes"
const OTHER_HINT := "Waits for the other chat…"
const PERSONA := "Journeyman"
const DISCARDED := "Discarded"
const BRIEF := preload("res://addons/joinery/sidebar_brief.gd")
const FILE_OPEN := preload("res://addons/joinery/sidebar_file_open.gd")
const NOTIFY := preload("res://addons/joinery/desktop_notify.gd")
const WORDS := preload("res://addons/joinery/sidebar_turn_words.gd")
const REVERTS := preload("res://addons/joinery/sidebar_reverts.gd")
## Minimum dock width in font sizes: in Joinery's layout, the whole visible sidebar.
const MIN_EM := 26
## The same below the Inspector (classic layout): a wider shared column crowds a laptop screen.
const SHARED_MIN_EM := 19

## Minimum width in font sizes: [constant MIN_EM], or [constant SHARED_MIN_EM] when shared.
var min_em := MIN_EM

## "connected", "connecting", "error" or "none" (no account connected yet).
var connection_state := "connecting"
## The latest art review ({text, sha256, approved, version, references, suggestions}).
var art_review_event: Dictionary = {}
## "/brief" and the look's layout upgrade (sidebar_brief.gd).
var _brief: BRIEF
var _content: VBoxContainer
var _header: HEADER_SCRIPT
var _transcript: HISTORY_SCRIPT
## The conversation Journeyman's own request is in: the shown one, or a chat left running
## while another is open (chat_sessions.gd). Its replies, steps and decision go there.
var _turn_log: HISTORY_SCRIPT
var _settings: SETTINGS_SCRIPT
## Saved chats: the Chats page, New chat, restoring a chat (chat_sessions.gd).
var _chats: CHATS_SCRIPT
var _footer: FOOTER_SCRIPT
var _tasks: TASKS_SCRIPT
var _composer: COMPOSER_SCRIPT
var _prompt: TextEdit
var _chips: HFlowContainer
var _options: MenuButton
var _send: Button
var _stop: Button
var _command_layer: CanvasLayer
var _command_popup: MENU_SCRIPT
var _matches: Array[String]:
	get:
		return _command_popup.matches
var _slash: SLASH_SCRIPT
var _review: Control
var _review_pending := false
var _details_open := false
var _busy := false
var _agent_name := "Journeyman"
var _assistant_open := false
var _started_msec := 0
var _last_elapsed := -1
var _progress := ""
var _outcome := "done"
var _failure := ""
var _active_card: Control
var _bridge: Node
## The plan card of the last "/feature add|remove" and its {action, feature}.
var _plan_card: Control
var _plan: Dictionary = {}
## Suggestions from the game brief, offered one at a time (sidebar_suggestions.gd).
var _suggestions: SUGGESTIONS
## Opens a ready change's review by itself, never while the user types (review_opener.gd).
var _opener: OPENER
## True between an art approval and sending the request the art gate held.
var _resume_ready := false
## Revert's outcome, Undo revert and the toast after a revert (sidebar_reverts.gd).
var _reverts: REVERTS


func _init() -> void:
	name = "Journeyman"
	child_entered_tree.connect(_on_child_entered)


func _ready() -> void:
	UI_THEME.watch(self)
	theme_type_variation = "JoineryDock"
	STYLE.own_slot(self)
	custom_minimum_size.x = get_theme_default_font_size() * min_em
	_content = VBoxContainer.new()
	_content.theme_type_variation = "JoineryRow"
	add_child(_content)
	_header = HEADER_SCRIPT.new()
	_header.reconnect_pressed.connect(func() -> void: reconnect_requested.emit())
	_header.connect_pressed.connect(func() -> void: connect_requested.emit())
	_header.notice.check_requested.connect(func() -> void: run_command("/tools"))
	_header.notice.setup_requested.connect(func() -> void: setup_requested.emit())
	_content.add_child(_header)
	_transcript = make_transcript()
	_content.add_child(_transcript)
	_footer = FOOTER_SCRIPT.new()
	_footer.review_requested.connect(func() -> void: review_requested.emit())
	_composer = COMPOSER_SCRIPT.new()
	_composer.set_status(_footer)
	_composer.submitted.connect(_on_submit)
	_composer.stop_pressed.connect(_request_cancel)
	_composer.target_opened.connect(func(path: String) -> void: target_opened.emit(path))
	_composer.notice.connect(func(text: String) -> void: show_status(text, 4.0))
	_content.add_child(_composer)
	STYLE.centered_column(_composer)
	_prompt = _composer.prompt
	_chips = _composer.chips
	_options = _composer.options
	_send = _composer.send
	_stop = _composer.stop
	_prompt.text_changed.connect(func() -> void: _update_commands(_prompt.text))
	_prompt.gui_input.connect(_on_prompt_input)
	_options.changed.connect(func(value: Dictionary) -> void: selection_options_changed.emit(value))
	_command_layer = CanvasLayer.new()
	_command_layer.layer = 100
	add_child(_command_layer)
	_command_popup = MENU_SCRIPT.new()
	_command_popup.anchor = _composer
	_command_popup.theme = theme
	_command_popup.chosen.connect(_on_command_chosen)
	_command_layer.add_child(_command_popup)
	_slash = SLASH_SCRIPT.new()
	_slash.requested.connect(_on_slash_requested)
	_slash.started.connect(func(_command: String) -> void: _sync())
	_slash.finished.connect(_on_slash_finished)
	_slash.feature_planned.connect(_on_feature_planned)
	_slash.feature_applied.connect(_on_feature_applied)
	_slash.readiness_changed.connect(_header.notice.show_readiness)
	_slash.revertible_changed.connect(_on_revertible)
	add_child(_slash)
	_reverts = REVERTS.new(self, _slash, _composer)
	_brief = BRIEF.new(self, _slash)
	_review = _make_review()
	_suggestions = SUGGESTIONS.new(self)
	_opener = OPENER.new()
	add_child(_opener)
	_chats = CHATS_SCRIPT.attach(self, _header.row, _content)
	_tasks = TASKS_SCRIPT.attach(self, _content, _composer.get_parent())
	_settings = SETTINGS_SCRIPT.attach(self, _header.row, _content)
	NOTIFY.attach(self)  # Done or needs you while the editor is in the background.
	_show_connection()
	_settings.apply_all()
	if Engine.is_editor_hint():  # Which app needs something, from the cache.
		_slash.check_readiness.call_deferred()


## A conversation view wired to this sidebar, with the shown one's settings: the first, and
## each chat opened while another keeps running out of sight (chat_sessions.gd).
func make_transcript() -> HISTORY_SCRIPT:
	var view := HISTORY_SCRIPT.new()
	view.accessibility_name = "Conversation"
	view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	view.example_chosen.connect(_fill_prompt)
	view.connect_requested.connect(func() -> void: connect_requested.emit())
	view.retry_requested.connect(_retry)
	view.revert_requested.connect(func(id: String) -> void: _slash.revert(id))
	view.unrevert_requested.connect(func(id: String, o: bool) -> void: _slash.unrevert(id, o))
	var shown := _transcript
	var wire := func() -> void:
		view.queue.edit_requested.connect(_edit_queued)
		view.queue.send_requested.connect(_drain)
		if is_instance_valid(shown):
			view.set_text_scale(shown.get("_text_scale"))
			view.set_show_timings(shown.get("_show_timings"))
			view.set_retry_allowed(shown.get("_retry_allowed"))
			view.set_connection(connection_state)
	view.ready.connect(wire, CONNECT_ONE_SHOT)
	return view


func _exit_tree() -> void:
	if _slash != null:
		_slash.cancel()


func _notification(what: int) -> void:
	if what == NOTIFICATION_PREDELETE and is_instance_valid(_review) and not _review.get_parent():
		_review.free()


func _on_theme_rebuilt() -> void:
	custom_minimum_size.x = get_theme_default_font_size() * min_em
	if _command_popup != null:
		_command_popup.theme = theme


func _process(_delta: float) -> void:
	if _busy and _started_msec > 0:
		var seconds := int((Time.get_ticks_msec() - _started_msec) / 1000.0)
		if seconds != _last_elapsed:
			_last_elapsed = seconds
			var card := _card()
			if card != null:
				card.set_elapsed(seconds)
	if _command_popup != null and _command_popup.visible:
		_command_popup.place()


func _input(event: InputEvent) -> void:
	var click := event as InputEventMouseButton
	if click == null or not click.pressed or not _command_popup.visible:
		return
	var inside_menu := _command_popup.get_global_rect().has_point(click.position)
	if not inside_menu and not _prompt.get_global_rect().has_point(click.position):
		_command_popup.close()


# --- Bridge ------------------------------------------------------------------------------------


## Listen to a chat bridge for receipts, notices, transaction ids and parallel tasks.
func attach_bridge(bridge: Node) -> void:
	_bridge = bridge
	_tasks.bridge = bridge
	var links := {
		"task_event": _tasks.on_event,
		"sent": _on_sent,
		"notice": _on_notice,
		"file_preview": _on_bridge_preview,
		"art_review": _on_art_review_event,
		"art_gate": _on_art_gate,
		"art_approved": _on_art_approved,
		"suggestions": _suggestions.take,
		"usage_received": _header.usage.show_usage,
		"connection_problem": _header.show_problem,
	}
	for signal_name: String in links:
		if (
			bridge.has_signal(signal_name)
			and not bridge.is_connected(signal_name, links[signal_name])
		):
			bridge.connect(signal_name, links[signal_name])


func _on_child_entered(node: Node) -> void:
	if node.has_signal("sent") and node.has_signal("file_preview"):
		attach_bridge(node)


func _on_sent() -> void:
	var card := _card()
	if card != null:
		card.set_sent()
	_sync_send_slot()


func _on_notice(text: String, kind: String) -> void:
	var card := _card()
	if card != null and card.state == "sending" and kind == "error":
		# The card says it in place ("Not sent. …") with Retry.
		_failure = text
		return
	_turn().add_notice(text, kind)


func _on_bridge_preview(event: Dictionary) -> void:
	var card := _card()
	if card != null and card.transaction_id.is_empty():
		card.transaction_id = str(event.get("transaction_id", ""))


func _on_art_review_event(event: Dictionary) -> void:
	art_review_event = event.duplicate(true)


## Asset work stopped at the art gate: the request's card holds it, with the look in small.
func _on_art_gate(event: Dictionary) -> void:
	var card := _card()
	if card != null:
		card.show_art_gate("", event.get("look", art_review_event))


## The art direction was approved: requests the gate held go out again as they were.
func _on_art_approved(_sha256: String) -> void:
	show_status("Look approved")
	_resume_ready = true
	_resume_held()


## After approval, send the held request as soon as nothing else is in the way.
func _resume_held() -> void:
	if not _resume_ready or _busy or _review_pending:
		return
	_resume_ready = false
	for card: Control in _transcript._cards:
		if is_instance_valid(card) and card.gated():
			_send_request(card)
			return


# --- Composer and commands ---------------------------------------------------------------------


func _update_commands(text: String) -> void:
	if not text.begins_with("/") or text.contains(" ") or text.contains("\n"):
		_command_popup.close()
		return
	# While a request runs, only the commands that open something in the editor apply.
	_command_popup.open_for(text, false, _busy or _review_pending)


func _on_prompt_input(event: InputEvent) -> void:
	var key := event as InputEventKey
	if key == null or not key.pressed or not _command_popup.visible:
		return
	match key.keycode:
		KEY_UP, KEY_DOWN:
			_command_popup.move(-1 if key.keycode == KEY_UP else 1)
		KEY_TAB:
			_complete(_command_popup.current())
		KEY_ENTER, KEY_KP_ENTER:
			if key.shift_pressed:
				_command_popup.close()
				return
			var typed := _prompt.text.strip_edges()
			var exact := SLASH_SCRIPT.all_commands().has(typed.split(" ")[0].to_lower())
			_on_submit(typed if exact else _command_popup.current())
		KEY_ESCAPE:
			_command_popup.close()
		_:
			return
	_prompt.accept_event()


func _complete(command: String) -> void:
	if command.is_empty():
		return
	_command_popup.close()
	_prompt.text = command + " "
	_prompt.set_caret_column(_prompt.text.length())
	_prompt.grab_focus()


func _on_command_chosen(command: String) -> void:
	_prompt.clear()
	_on_submit(command)


func _fill_prompt(text: String) -> void:
	_prompt.text = text
	_prompt.set_caret_column(text.length())
	_prompt.grab_focus()


func _on_submit(text: String) -> void:
	var entry := text.strip_edges()
	if entry.is_empty():
		return
	var own: bool = _chats.turns.busy_in(_transcript)
	if (own or _occupied()) and _transcript.queue.waits(entry):
		if own or not _chats.turns.send_typed(_transcript, entry, target_snapshot()):
			_enqueue(entry)
		return
	_command_popup.close()
	_prompt.clear()
	if entry.begins_with("/") and not SLASH_SCRIPT.is_prompt(entry):
		run_command(entry)
		return
	_start_request(entry, target_snapshot())


func _start_request(entry: String, targets: Array) -> void:
	_assistant_open = false
	var card := _transcript.add_request(entry, targets)
	card.connect("gate_review_requested", func() -> void: art_review_requested.emit())
	card.connect(
		"gate_describe_requested", func() -> void: fill_composer("Here's how my game should look: ")
	)
	card.connect("file_opened", func(path: String) -> void: FILE_OPEN.open(path, self))
	_send_request(card)


## A request runs, is on its way, or a change waits for review: new messages queue.
func _occupied() -> bool:
	var card := _card()
	return _busy or _review_pending or (card != null and card.state == "sending")


# --- Queue -------------------------------------------------------------------------------------


func _enqueue(entry: String) -> void:
	_command_popup.close()
	var other := _parked() and not _chats.turns.busy_in(_transcript)
	_transcript.queue.heading.text = OTHER_WAIT if other else "Queued"
	if not _transcript.queue.push(entry, target_snapshot()):
		show_status("The queue is full. Remove a message or wait for a reply.", 4.0)
		return
	_prompt.clear()


## Edit: the message and its targets go back into the message box; a draft there stays.
func _edit_queued(card: Control) -> void:
	if _composer.restore(str(card.get("text")), card.get("targets")):
		_transcript.queue.remove(card)
	else:
		show_status("Send or clear the message box first.")


## Send the oldest queued message when nothing is in the way; commands run in order too.
func _drain() -> void:
	var card := _card()
	var gated: bool = card != null and card.gated()
	var queue := _transcript.queue
	if queue.size_queued() == 0 or queue.paused or gated or _chats.turns.busy_in(_transcript):
		return
	if connection_state != "connected" or not _slash.running().is_empty():
		return
	if _occupied():  # Journeyman works in another chat: it may start once an agent is free.
		_chats.turns.drain(_transcript)
		return
	var next := queue.take()
	var entry := str(next["text"])
	if not entry.begins_with("/") or SLASH_SCRIPT.is_prompt(entry):
		_start_request(entry, next["targets"])
		return
	run_command(entry)
	# A command that finished at once lets the next one go.
	if _slash.running().is_empty():
		_drain.call_deferred()


## Put [param text] in the message box, ready to edit and send (offers and "Ask Journeyman");
## with none, keep what is typed there and take the focus (the Tools page's "Ask Journeyman").
func fill_composer(text := "") -> void:
	_fill_prompt(text if not text.is_empty() else _prompt.text)


func _send_request(card: Control) -> void:
	_active_card = card
	_turn_log = _transcript
	_failure = ""
	if not card.is_connected("failed", _on_card_failed):
		card.connect("failed", _on_card_failed)
	card.set_sending()
	_sync_send_slot()
	if prompt_submitted.get_connections().is_empty():
		card.set_failed("Open this project through Joinery to chat.")
		return
	prompt_submitted.emit(card.request, card.targets)


func _retry(card: Control) -> void:
	if _occupied():
		return
	if card.not_sent():  # Not sent: the same card goes again.
		_send_request(card)
	else:  # It ran and failed: ask again, as a new request.
		_start_request(card.request, card.targets)


## A request that never reached the assistant. When the connection went with it, every
## indicator says so: the dot turns red and Send rests, while the card's Retry is the action.
func _on_card_failed(reason: String) -> void:
	_transcript.queue.pause()
	_sync_send_slot()
	if connection_state == "error" or reason.to_lower().contains("connection"):
		connection_state = "error"
		_header.show_state("error", _agent_name, reason, false)
		_composer.set_connection(connection_state)
		_transcript.set_connection("error")


## Stop takes Send's slot while Journeyman works or a request is on its way to it.
func _sync_send_slot() -> void:
	var sending: bool = _card() != null and _card().state == "sending"
	_composer.set_busy(_chats.turns.busy_in(_transcript) or not _parked() and (_busy or sending))
	_composer.set_placeholder(_waiting_placeholder())


## Run a slash command ("/doctor", "/help", …) without posting it; an unknown one gets a notice.
func run_command(entry: String) -> void:
	if entry == "reconnect":
		reconnect_requested.emit()
		return
	var command := entry if entry.begins_with("/") else "/" + entry
	if not _slash.run(command):
		_transcript.add_notice("Unknown command. Type / to see commands.", "info")


func _on_slash_requested(command: String) -> void:
	match command:
		"/help":
			_prompt.grab_focus()
			_command_popup.open_for("", true)
		"/setup":
			setup_requested.emit()
		"/art":
			art_review_requested.emit()
		"/brief":
			_brief.open()
		"/models":
			models_refresh_requested.emit()
		"/focus":
			focus_toggle_requested.emit()
		"/toolbar":
			toolbar_toggle_requested.emit()


func _on_slash_finished(_command: String, text: String, kind: String) -> void:
	_sync()
	if not text.is_empty():
		_transcript.add_notice(text, kind)
	_drain.call_deferred()


## Bring a draft of the look in the first layout to the new one without asking (the art page
## does this on open). Tried once per version; a failure leaves the draft as it is.
func upgrade_bible(version := "") -> void:
	_brief.upgrade(version if not version.is_empty() else str(art_review_event.get("sha256", "")))


func _on_feature_planned(plan: Dictionary) -> void:
	var action := str(plan.get("action", "add"))
	var id := str(plan.get("feature", ""))
	var verb := "Add" if action == "add" else "Remove"
	var card: Control = _transcript.add_plan(
		plan, "%s %s" % [verb, SLASH_SCRIPT.feature_phrase(id)]
	)
	_plan_card = card
	_plan = {"action": action, "feature": id}
	card.connect("apply_pressed", func() -> void: _slash.apply_feature(action, id))
	card.connect("revert_pressed", func(tx: String) -> void: _slash.revert(tx))
	card.connect("unrevert_pressed", func(tx: String, o: bool) -> void: _slash.unrevert(tx, o))


func _on_feature_applied(ok: bool, message: String, transaction_id: String) -> void:
	if not is_instance_valid(_plan_card):
		return
	if not ok:
		_plan_card.set_failed(message)
		return
	var name := SLASH_SCRIPT.feature_phrase(str(_plan.get("feature", "")))
	var done := ("Added %s." if _plan.get("action") == "add" else "Removed %s.") % name
	var paths := PackedStringArray()
	for path: Variant in _plan_card.plan.get("changes", []):
		paths.append(str(path))
	if paths.has("project.godot"):
		message += " Reload the project to use its new settings (Project > Reload Current Project)."
	_plan_card.set_applied((done + " " + message).strip_edges(), transaction_id)
	if not files_changed.get_connections().is_empty():
		files_changed.emit(transaction_id, paths)
	elif Engine.is_editor_hint():
		EditorInterface.get_resource_filesystem().scan()


## Which accepted changes can still be reverted, from `joinery transactions list`.
func _on_revertible(states: Dictionary) -> void:
	for id: String in states:
		var card := _transcript.card_for(id)
		if card != null:
			card.set_revertible(bool(states[id]))


## Ask again which changes can be reverted (after Compact in Settings, an accept, a revert).
func refresh_revertible() -> void:
	_slash.check_revertible()


## Footer and composer follow the state: a running check, a reply, a pending review, or idle.
func _sync() -> void:
	var command: String = _slash.running()
	if not command.is_empty():
		_footer.set_mode("command", command)
	elif _busy:
		_footer.set_mode("idle")  # The work shows in the chat only; the composer keeps Stop.
	elif _review_pending:  # Shown on the card, or decided there and on its way: nothing waits.
		var card := _card()
		var shown: bool = (
			card != null and (card.review_row().visible or not card.awaiting_decision())
		)
		_footer.set_mode("idle" if shown else "review")
	else:
		_footer.set_mode("idle")
	_composer.set_locked(not command.is_empty())
	_composer.set_placeholder(_waiting_placeholder())


## While Send would queue, the message box says so.
func _waiting_placeholder() -> String:
	if _parked() and _occupied() and not _chats.turns.busy_in(_transcript):
		return "" if _chats.turns.can_start(_transcript) else OTHER_HINT
	if _review_pending or _chats.turns.busy_in(_transcript):
		return REVIEW_WAIT
	return REVIEW_WAIT if _occupied() and connection_state == "connected" else ""


func _request_cancel() -> void:
	_transcript.queue.pause()
	if _chats.turns.stop_in(_transcript):  # This chat's own turn, beside the other: only it.
		return
	var card := _card()
	if not _busy and card != null and card.state == "sending":
		# Stopped before Journeyman took it: the request stays here, ready to Retry.
		card.set_failed()
		cancel_requested.emit()
		return
	_progress = "Stopping…"
	_outcome = "cancelled"
	_composer.stopping()
	_sync()
	cancel_requested.emit()
	_tasks.offer_stop_all()


## A transient caption over the transcript's bottom for [param seconds]; [param action] adds a
## link that runs [param callback]. It takes no layout height. "" clears it.
func show_status(text: String, seconds := 3.0, action := "", callback := Callable()) -> void:
	_transcript.show_caption(text, seconds, action, callback)


## Apply settings: {mode, text_scale, show_timings, details_open}; missing keys are kept.
func apply_preferences(prefs: Dictionary) -> void:
	if prefs.has("mode"):
		_options.set_mode(str(prefs["mode"]))
	if prefs.has("text_scale"):
		_transcript.set_text_scale(float(prefs["text_scale"]))
	if prefs.has("show_timings"):
		_transcript.set_show_timings(bool(prefs["show_timings"]))
	if prefs.has("details_open"):
		_details_open = bool(prefs["details_open"])
		_review_call("set_details_open", [_details_open])


# --- Targets -----------------------------------------------------------------------------------


func on_selection_changed() -> void:
	if Engine.is_editor_hint():
		_set_targets(CHIP_SCRIPT.from_selection())


func set_resource_target(path: String, type: String = "Resource", source: String = "") -> void:
	if not path.is_empty():
		_set_targets([CHIP_SCRIPT.for_resource(path, type, source)])


## Make a part of a model the message target ({part_kind, resource, object, material, …} from
## `joinery parts` or a viewport pick, or a target already built from one).
func set_part_target(part: Dictionary) -> void:
	if not part.is_empty():
		_set_targets([CHIP_SCRIPT.for_part(part)])


## A part dragged from the workspace's parts list ({type: "joinery_part", part}) becomes the
## target wherever it lands in the sidebar; the message box keeps its own text drops.
func _can_drop_data(_at: Vector2, data: Variant) -> bool:
	return (
		data is Dictionary and data.get("type") == "joinery_part" and data.get("part") is Dictionary
	)


func _drop_data(_at: Vector2, data: Variant) -> void:
	set_part_target(data["part"])


func target_snapshot() -> Array[Dictionary]:
	return _composer.target_snapshot()


func _set_targets(targets: Array[Dictionary]) -> void:
	_composer.set_targets(targets)
	_transcript.set_start_context(targets, CHIP_SCRIPT.open_scene())


# --- Connection state --------------------------------------------------------------------------


## The provider behind Journeyman ("claude", shown as "Claude Code"); "" when none.
func set_chat_agent(agent: String) -> void:
	_agent_name = preload("chat_bridge.gd").agent_name(agent)
	if _composer != null:
		_show_connection()


func set_chat_state(state: String, detail: String) -> void:
	if state == "working":
		_progress = detail
		var card := _card()
		if card != null and _busy:
			card.add_step(detail)
		_sync()
		return
	connection_state = "none" if state == "error" and _agent_name.is_empty() else state
	_show_connection(detail if state == "error" else "")


## A line of the work trail (chat_bridge.gd's trail_event), or a test run as it goes (its
## tests_event), joins the request's card.
func add_trail(event: Dictionary) -> void:
	var card := _card()
	if card != null and _busy:
		card.call("add_tests" if event.get("kind") == "tests" else "add_trail", event)
		if event.get("state") == "running" and event.get("note") != "thought":
			_progress = str(event.get("text", ""))
		_sync()


## Header, Send and the example prompts all follow [member connection_state]. While the
## header's banner offers the fix (Reconnect, Connect), unsent cards say only "Not sent".
func _show_connection(detail := "") -> void:
	_header.show_state(connection_state, _agent_name, detail)
	_composer.set_connection(connection_state)
	_composer.set_placeholder(_waiting_placeholder())
	_transcript.set_connection(connection_state)
	_transcript.set_retry_allowed(not connection_state in ["error", "none"])
	if connection_state == "connected":
		_drain.call_deferred()


func set_chat_busy(busy: bool) -> void:
	var was_busy := _busy
	_busy = busy
	_tasks.set_main_busy(busy)
	if busy:
		_started_msec = Time.get_ticks_msec()
		_last_elapsed = -1
		_progress = ""
		_outcome = "done"
		_command_popup.close()
	else:
		_started_msec = 0
		var card := _card()
		if card != null and was_busy:
			if card.state == "sending":
				card.set_failed(_failure)
			else:
				card.finish(_outcome)
		if was_busy and _outcome != "done":
			_turn().queue.pause()
	_sync_send_slot()
	_sync()
	if not busy and was_busy:
		_resume_held()
		_drain()


func set_model_options(models: Array) -> void:
	_options.set_models(models)


func restore_selection_options(selection: Dictionary) -> void:
	_options.restore(selection)


func show_execution(event: Dictionary) -> void:
	_options.show_execution(event)
	_on_sent()
	_progress = "Thinking…"
	_sync_send_slot()
	_sync()


func show_timing(event: Dictionary) -> void:
	var outcome := str(event.get("outcome", ""))
	_outcome = (
		"done" if outcome == "done" else "cancelled" if outcome == "cancelled" else "failed"
	)
	var placed := _turn().show_timing(event)
	if _outcome == "cancelled" and not placed:
		_turn().add_decision("Stopped", "stopped", _card())


func append_agent_text(text: String, new_message: bool) -> void:
	_on_sent()
	if not _assistant_open and _busy and _card() != null:
		_card().reply_started()
	_turn().append_delta(PERSONA, text, new_message or not _assistant_open)
	_assistant_open = true


func show_agent_message(text: String, error := false) -> void:
	var card := _card()
	if card != null and card.state == "sending" and _bridge != null:
		# Before the bridge sent the request, anything it says is a delivery failure.
		_failure = text
		_turn().add_notice(text, "error")
		return
	_on_sent()
	finish_agent_response(true)
	_turn().call("add_error" if error else "add_message", PERSONA, text)


## The reply is over; a turn that ends not [param ok] finishes its card as failed.
func finish_agent_response(ok: bool) -> void:
	if not ok and _busy and _outcome == "done":
		_outcome = "failed"
	_turn().finish_response()
	_assistant_open = false


func _card() -> Control:
	if is_instance_valid(_active_card):
		return _active_card
	return _turn().current_card() if _transcript != null else null


## The conversation Journeyman's request is in ([member _turn_log]), else the shown one.
func _turn() -> HISTORY_SCRIPT:
	return _turn_log if is_instance_valid(_turn_log) else _transcript


## Journeyman's request runs in a chat that is not on screen.
func _parked() -> bool:
	return _turn() != _transcript


# --- Review ------------------------------------------------------------------------------------


## The Review panel for the editor's bottom panel (review_panel.gd).
func take_review_panel() -> Control:
	return _review


func show_edit_review(summary: String, count := 0) -> void:
	_review_call("show_scene_review", [summary, count])
	_open_review([])
	_arrive()


func show_file_review(
	summary: String, text_diff: String, media: Array = [], changes: Array = [], transaction_id := ""
) -> void:
	_review_call("show_file_review", [summary, text_diff, media, changes])
	_review_call("set_details_open", [_details_open])
	_open_review(media)
	var card := _card()
	if card != null and not transaction_id.is_empty():
		card.transaction_id = transaction_id
	_arrive()


## The request card travels into the Review panel's header (a shared-element entrance).
func _arrive() -> void:
	var card := _card()
	if card != null and card.is_visible_in_tree() and _review.has_method("arrive_from"):
		_review.call("arrive_from", card.get_global_rect())


## Close the review. [param kind] is "success", "rejected", "info" or "error"; the message is
## shown on the request's card, or as a notice when there is none. A discard always reads
## "Discarded": nothing in the game changed.
func hide_edit_review(message: String = "", kind := "info") -> void:
	var was_pending := _review_pending
	_review_pending = false
	_opener.cancel()
	var decided := WORDS.decision_kind(message, kind)
	if decided == "rejected":
		message = DISCARDED
	if decided in ["success", "rejected"] and was_pending and _review.has_method("show_result"):
		_review.call("show_result", decided == "success", message)
	else:
		_review_call("clear", [])
	_sync()
	if was_pending:
		review_closed.emit()
	if decided == "success" and was_pending:
		refresh_revertible()
	if not message.is_empty():
		finish_agent_response(true)
		_turn().add_decision(message, decided, _card() if was_pending else null)
	_resume_held()
	_drain()


func set_review_applying(on: bool) -> void:
	_review_call("set_applying", [on])


## The decision failed: the panel shows why and takes the decision again.
func show_review_error(text: String) -> void:
	_review_call("show_error", [text])
	var card := _card()
	if card != null:
		card.roll_back()


func _open_review(media: Array) -> void:
	_review_pending = true
	var card := _card()
	if card != null:
		card.set_ready(media, _review)
		# Deferred: an outcome hides the row before the card's state says it was decided.
		card.review_row().visibility_changed.connect(
			_sync, CONNECT_REFERENCE_COUNTED | CONNECT_DEFERRED
		)
	_command_popup.close()
	_sync()
	# The review opens by itself, once the user isn't typing.
	_opener.request(review_requested.emit)


func _on_accept() -> void:
	_decide("success", "Change accepted", accept_edit_requested)


func _on_reject() -> void:
	_decide("rejected", DISCARDED, reject_edit_requested)


## The card shows the decision at once, then [param ask] asks for it: once, never again while
## that decision is on its way (a second Accept, or ⌘↩ after the button).
func _decide(kind: String, text: String, ask: Signal) -> void:
	var card := _card()
	if card != null and card.deciding():
		return
	if card != null and card.awaiting_decision():
		card.set_outcome(kind, text, true)
	ask.emit()


func _make_review() -> Control:
	var panel: Control = null
	if ResourceLoader.exists(REVIEW_PATH):
		var script := load(REVIEW_PATH) as Script
		if script != null and script.can_instantiate():
			panel = script.new() as Control
	if panel == null:
		panel = VBoxContainer.new()
	panel.name = "Review"
	var links := {
		"accept_pressed": _on_accept,
		"reject_pressed": _on_reject,
		"show_requested": func() -> void: review_requested.emit(),
		"part_picked": set_part_target,
	}
	for signal_name: String in links:
		if panel.has_signal(signal_name):
			panel.connect(signal_name, links[signal_name])
	if panel.has_method("set_details_open"):
		panel.call("set_details_open", _details_open)
	return panel


func _review_call(method: String, arguments: Array) -> void:
	if is_instance_valid(_review) and _review.has_method(method):
		_review.callv(method, arguments)
