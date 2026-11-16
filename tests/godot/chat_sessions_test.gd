extends SceneTree
## Saved chats in the sidebar: the Chats list, bringing back the latest chat as the open one,
## change cards showing what Joinery says became of each change, an older chat read-only with
## Continue, New chat, renaming, and the chat id each prompt carries to the chat host.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const LATEST := "20261110-090000-aaaaaa"
const OLDER := "20261101-090000-bbbbbb"
const ACCEPTED := "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
const REVERTED := "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
const PRUNED := "cccccccccccccccccccccccccccccccc"
const NAMED := "20261112-090000-dddddd"

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _folder() -> String:
	return ProjectSettings.globalize_path("res://.joinery/chats")


func _save(chat_id: String, meta: Dictionary, entries: Array, seconds_ago: int) -> void:
	DirAccess.make_dir_recursive_absolute(_folder())
	var updated := Time.get_datetime_string_from_unix_time(
		int(Time.get_unix_time_from_system()) - seconds_ago
	)
	var head := meta.merged({"type": "chat", "id": chat_id, "updated": updated + "+00:00"})
	var lines := PackedStringArray([JSON.stringify(head)])
	for entry: Dictionary in entries:
		lines.append(JSON.stringify(entry))
	var file := FileAccess.open(_folder().path_join(chat_id + ".jsonl"), FileAccess.WRITE)
	file.store_string("\n".join(lines) + "\n")
	file.close()


func _fixtures() -> void:
	_save(
		LATEST,
		{"title": "Make the door wider", "provider": "codex", "handle": "thread-1"},
		[
			{
				"type": "user",
				"text": "Make the door wider",
				"targets": [{"id": "res://door.tscn", "label": "Door"}],
			},
			{"type": "step", "text": "Opening door.blend in Blender"},
			{"type": "agent", "text": "I widened **Door** by 20%."},
			{"type": "change", "transaction_id": ACCEPTED, "summary": "Widen", "state": "review"},
			{"type": "change", "transaction_id": ACCEPTED, "state": "accepted"},
			{"type": "turn", "outcome": "done", "total_ms": 72000},
			{"type": "user", "text": "Darken the wood"},
			{"type": "agent", "text": "Darker now."},
			{"type": "change", "transaction_id": REVERTED, "state": "accepted"},
			{"type": "turn", "outcome": "done", "total_ms": 5000},
		],
		60
	)
	_save(
		OLDER,
		{"title": "Harbour lamps", "provider": "claude", "handle": ""},
		[
			{"type": "user", "text": "Make the lamps flicker"},
			{"type": "agent", "text": "They flicker."},
			{
				"type": "change",
				"transaction_id": PRUNED,
				"state": "accepted",
				"auto": true,
				"notes": ["Reviewer: the flicker never stops"],
			},
			{"type": "turn", "outcome": "done", "total_ms": 3000},
		],
		3 * 86400
	)


## New chat while Journeyman works: the working chat keeps running out of sight, a message
## from the new chat waits for it, and opening the working chat again shows it live.
func _while_working(chat: Control, backend: Node, sessions: Node) -> void:
	var sent: Array[String] = []
	chat.connect(
		"prompt_submitted",
		func(text: String, _targets: Array) -> void:
			sent.append("%s|%s" % [text, backend.get("chat_id")])
	)
	var stops: Array[bool] = []
	chat.connect("cancel_requested", func() -> void: stops.append(true))
	var composer: Control = chat.get("_composer")
	chat.set_chat_state("connected", "")
	sessions.call("open_chat", OLDER)
	sessions.call("continue_chat")
	var running: Control = chat.get("_transcript")
	chat.call("_on_submit", "Add rain")
	chat.set_chat_busy(true)
	chat.call("_on_sent")
	_check(sent == ["Add rain|" + OLDER], "The request went out: %s" % [sent])
	_check(composer.get("_busy"), "Stop stands in for Send while it works")

	sessions.call("new_chat")
	var fresh: Control = chat.get("_transcript")
	_check(fresh != running and fresh.visible, "New chat opens while Journeyman works")
	_check(not running.visible and running.is_inside_tree(), "The working chat runs on, hidden")
	_check(fresh.call("current_card") == null, "The new chat is empty")
	_check(backend.get("chat_id") == "", "Its first message starts a chat of its own")
	_check(not composer.get("_busy"), "The new chat has Send, not the other chat's Stop")
	var waits: String = chat.get("OTHER_WAIT")
	var placeholder := str(composer.get("prompt").placeholder_text)
	_check(
		placeholder == chat.get("OTHER_HINT"), "The message box says it would wait: " + placeholder
	)
	chat.append_agent_text("Rain falls on the pier.", true)
	_check(
		str(running.call("get_parsed_text")).contains("Rain falls on the pier."),
		"Its reply reaches the working chat"
	)
	_check(not str(fresh.call("get_parsed_text")).contains("Rain falls"), "Not the chat on screen")
	_check(sessions.call("working_id") == OLDER, "The working chat is known")
	sessions.call("open_list")
	var page: Control = sessions.get("page")
	_check(page.call("row_for", OLDER).get("working"), "The Chats list marks it")
	_check(not page.call("row_for", LATEST).get("working"), "And only it")
	sessions.call("new_chat")
	_check(chat.get("_transcript") == fresh, "New chat again keeps the empty chat")

	# A message from the new chat waits for the other chat, then goes out as a new chat.
	chat.call("_on_submit", "Make a lighthouse")
	var queue: Control = fresh.get("queue")
	_check(queue.call("size_queued") == 1, "It waits in the new chat's queue")
	_check(queue.get("heading").text == waits, "Saying what it waits for")
	_check(sent.size() == 1, "Nothing went out yet")
	sessions.call("open_chat", OLDER)
	_check(chat.get("_transcript") == fresh, "A chat with a queued message stays until it goes")

	# The working chat's turn ends: its card finishes out of sight, and the queued one goes.
	chat.show_timing({"outcome": "done", "total_ms": 4000})
	chat.set_chat_busy(false)
	var rain: Control = running.call("current_card")
	_check(rain.get("state") == "done", "The working chat's card finished: %s" % rain.get("state"))
	_check(sent.size() == 2 and sent[1] == "Make a lighthouse|", "Then the queued one: %s" % [sent])
	_check(fresh.call("current_card") != null, "On the new chat's own card")
	chat.set_chat_busy(true)
	chat.call("_on_sent")

	# Back to the first chat while the new one works: as it was left, its reply and all.
	sessions.call("open_chat", OLDER)
	_check(chat.get("_transcript") == running and running.visible, "The kept chat comes back")
	_check(not fresh.visible and fresh.is_inside_tree(), "The new one runs on, hidden")
	_check(backend.get("chat_id") == OLDER, "Its next message continues it")
	_check(not sessions.get("viewing"), "It takes messages")
	_check(not composer.get("_busy"), "Send, since the work is in the other chat")
	# The host names the new chat: it stays the one at work, and this one keeps its id.
	backend.call("_handle", {"kind": "chat", "chat_id": NAMED, "mode": "new"})
	_check(sessions.call("current_id") == OLDER, "The chat on screen keeps its id")
	_check(backend.get("chat_id") == OLDER, "And its next message continues it")
	_check(sessions.call("working_id") == NAMED, "The new chat is at work")
	sessions.call("open_chat", NAMED)
	_check(chat.get("_transcript") == fresh, "Opening it shows it live")
	_check(composer.get("_busy"), "With its Stop")
	chat.call("_request_cancel")
	_check(stops.size() == 1, "Stop stops it")
	chat.show_timing({"outcome": "cancelled", "total_ms": 1000})
	chat.set_chat_busy(false)
	_check(sessions.call("working_id") == "", "Nothing works now")


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	var store: GDScript = load("res://addons/joinery/chat_store.gd")
	_check(store.when(1000, 1030) == "Just now", "Seconds ago")
	_check(store.when(1000, 1000 + 7200) == "2 h ago", "Hours ago")
	_check(store.when(1000, 1000 + 3 * 86400) == "3 days ago", "Days ago")
	_check(store.call("read", "../etc").is_empty(), "Chat ids are file names only")
	_fixtures()
	var listed: Array = store.call("list")
	_check(listed.size() == 2 and listed[0]["id"] == LATEST, "Most recent first: %s" % [listed])

	var chat: Control = load(DOCK_PATH).new()
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	var backend: Node = load("res://addons/joinery/chat_bridge.gd").new()
	chat.add_child(backend)
	var sessions: Node = chat.get("_chats")
	# Joinery's CLI answers from here: nothing runs in the background.
	var calls: Array[String] = []
	var answers := {"chats": {"code": 0, "output": "{}"}}
	sessions.set(
		"runner",
		func(args: PackedStringArray, done: Callable) -> void:
			var words := " ".join(args.slice(args.find("joinery") + 1))
			calls.append(words)
			# Joinery can't be asked yet: the chat's own record shows.
			done.call(answers.get(words.split(" ")[0], {"code": 1, "output": ""}))
	)
	var transcript: Node = chat.get("_transcript")
	var composer: Control = chat.get("_composer")
	_check(sessions != null, "The sidebar has saved chats")
	var buttons: Array[String] = []
	for button: Node in chat.get("_header").get("row").get_children():
		if button is Button:
			buttons.append(str((button as Button).tooltip_text))
	_check(buttons.has("Chats") and buttons.has("New chat"), "Header buttons: %s" % [buttons])
	_check(transcript.call("current_card") == null, "Outside the editor nothing restores itself")

	# The editor reopened: the latest chat comes back as the open chat.
	sessions.call("restore_latest")
	await process_frame
	_check(sessions.call("current_id") == LATEST, "The latest chat is open")
	_check(backend.get("chat_id") == LATEST, "Its next prompt continues it")
	_check(not sessions.get("viewing") and composer.visible, "The open chat takes messages")
	var cards: Array = transcript.get("_cards")
	_check(cards.size() == 2, "One card per request")
	var first: Control = cards[0]
	_check(first.get("request") == "Make the door wider", "The request is back")
	_check(first.get("targets")[0]["label"] == "Door", "With its target chips")
	_check(first.get("transaction_id") == ACCEPTED, "And its change")
	_check(first.get("state") == "accepted", "The recorded decision shows first")
	var text: String = transcript.call("get_parsed_text")
	_check(text.contains("I widened **Door** by 20%."), "Replies are back: " + text)

	# What Joinery says now wins: the second change was reverted since, the first is too old.
	(
		sessions
		. call(
			"show_states",
			{
				"transactions":
				[
					{"id": ACCEPTED, "state": "accepted", "revertible": false},
					{"id": REVERTED, "state": "reverted", "revertible": false},
				]
			}
		)
	)
	_check((first.get("_too_old") as Control).visible, "An old change says it's too old")
	_check(not (first.get("_revert") as Control).visible, "And offers no Revert")
	var second: Control = cards[1]
	_check(second.get("_outcome_text").text == "Reverted", "A reverted change says so")

	# The Chats list: the open chat first, then the older one with who answered and when.
	sessions.call("open_list")
	var page: Control = sessions.get("page")
	_check(page.visible and not transcript.visible, "The list replaces the conversation")
	var latest_row: Node = page.call("row_for", LATEST)
	var older_row: Node = page.call("row_for", OLDER)
	_check(latest_row != null and older_row != null, "Both chats are listed")
	_check(
		latest_row.get("current") and not older_row.get("current"),
		"The open chat is highlighted, no 'Open' word"
	)
	_check(older_row.get("caption").text == "3d", older_row.get("caption").text)
	var told: String = older_row.get("_open").accessibility_description
	_check(told == "Claude · 3 days ago", "Who answered is told: " + told)
	var filled := 0
	for button: Node in page.find_children("*", "Button", true, false):
		filled += int((button as Button).theme_type_variation == "JoineryPrimaryButton")
	_check(filled == 0, "New chat is an icon, not a filled button")
	_check(calls.size() == 1 and calls[0].begins_with("transactions list"), "%s" % [calls])
	var renamed: Array[String] = []
	page.connect(
		"rename_requested", func(id: String, title: String) -> void: renamed.append(id + title)
	)
	older_row.call("begin_rename")
	older_row.get("_field").text = "  Lamps   that flicker "
	older_row.call("_commit")
	_check(renamed == [OLDER + "Lamps that flicker"], "Rename asks Joinery: %s" % [renamed])
	_check(older_row.get("title").text == "Lamps that flicker", "And shows the name at once")
	_check(calls[-1].begins_with("chats rename --project "), "Through the CLI: %s" % [calls])
	_check(calls[-1].ends_with("--id %s --title Lamps that flicker" % OLDER), calls[-1])

	# An older chat opens read-only, with Continue where the message box was.
	sessions.call("open_chat", OLDER)
	await process_frame
	_check(not page.visible and transcript.visible, "Opening a chat closes the list")
	_check(sessions.get("viewing"), "An older chat is only viewed")
	var bar: Control = sessions.get("past_bar")
	_check(bar.visible and not composer.visible, "Continue stands in for the message box")
	_check(
		str(bar.find_children("", "Label", true, false)[0].text).begins_with(
			"Earlier chat · Claude"
		),
		"The bar names it"
	)
	_check(backend.get("chat_id") == LATEST, "Viewing doesn't change the open chat")
	_check(transcript.get("_cards").size() == 1, "Its own transcript is shown")
	var kept: Control = transcript.get("_cards")[0]
	_check(
		(
			kept.get("_outcome_text").text
			== "Accepted automatically\nReviewer: the flicker never stops"
		),
		(
			"A change kept without asking says so, notes and all: "
			+ str(kept.get("_outcome_text").text)
		)
	)
	_check((kept.get("_revert") as Button).text == "Undo", "With Undo")
	sessions.call("continue_chat")
	_check(not bar.visible and composer.visible, "Continue brings the message box back")
	_check(backend.get("chat_id") == OLDER, "The next prompt continues the older chat")

	# Each prompt names its chat.
	backend.call("submit", "Brighter please", [])
	_check(
		backend.get("_pending").get("chat_id") == OLDER,
		"The prompt carries its chat: %s" % [backend.get("_pending")]
	)
	backend.set("_pending", {})
	chat.set_chat_busy(false)

	# The host continued it from a summary: the transcript says so plainly.
	(
		backend
		. call(
			"_handle",
			{
				"kind": "chat",
				"chat_id": OLDER,
				"mode": "summary",
				"message": "Continued with a summary; the agent starts fresh.",
			}
		)
	)
	var notices: PackedStringArray = transcript.call("notice_texts")
	_check(notices.has("Continued with a summary; the agent starts fresh."), "%s" % [notices])
	# A resumed chat is what the user expects: its message is for logs, not a notice.
	(
		backend
		. call(
			"_handle",
			{
				"kind": "chat",
				"chat_id": OLDER,
				"mode": "resumed",
				"message": "Continued this chat; the agent remembers it.",
			}
		)
	)
	notices = transcript.call("notice_texts")
	_check(not notices.has("Continued this chat; the agent remembers it."), "%s" % [notices])

	# New chat: an empty conversation, and the next prompt starts a chat of its own.
	sessions.call("new_chat")
	_check(transcript.call("current_card") == null, "New chat clears the conversation")
	_check(backend.get("chat_id") == "", "The next prompt starts a new chat")
	backend.call("_handle", {"kind": "chat", "chat_id": "20261111-090000-cccccc", "mode": "new"})
	_check(sessions.call("current_id") == "20261111-090000-cccccc", "The host names it")
	_check(sessions.get("shown_id") == "20261111-090000-cccccc", "And it is the one on screen")
	await _while_working(chat, backend, sessions)

	backend.queue_free()
	chat.queue_free()
	await process_frame
	for file_name: String in DirAccess.get_files_at(_folder()):
		DirAccess.remove_absolute(_folder().path_join(file_name))
	print("chat sessions: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
