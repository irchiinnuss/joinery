extends SceneTree
## A message from another chat while Journeyman works: it starts at once as that chat's own turn
## when an agent is free, and shows in that chat (its card, words, steps, Stop and change), kept
## out of sight like any working chat with its own dot on the Chats page. With every agent busy
## it waits in its chat's queue as before, and goes out once one is free. A message in a chat
## whose own turn still works waits in that chat's queue.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const FIRST := "20261118-090000-aaaaaa"
const SECOND := "20261118-090000-bbbbbb"
const THIRD := "20261118-090000-cccccc"
const JOB_SOURCE := """extends RefCounted
var lines: Array[Dictionary] = []
func send_line(line: String) -> void:
	lines.append(JSON.parse_string(line))
func take_lines() -> Array:
	return []
func poll() -> Dictionary:
	return {}
func close_pipes() -> void:
	pass
"""

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _sent(job: RefCounted, kind: String) -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	for line: Dictionary in job.get("lines"):
		if str(line.get("kind", "")) == kind:
			found.append(line)
	return found


func _folder() -> String:
	return ProjectSettings.globalize_path("res://.joinery/chats")


## Saved chats for the Chats list: each as the host saves it, a line of its own.
func _fixtures() -> void:
	DirAccess.make_dir_recursive_absolute(_folder())
	var when := Time.get_datetime_string_from_unix_time(int(Time.get_unix_time_from_system()))
	for id: String in [FIRST, SECOND, THIRD]:
		var head := {"type": "chat", "id": id, "title": id, "updated": when + "+00:00"}
		var file := FileAccess.open(_folder().path_join(id + ".jsonl"), FileAccess.WRITE)
		file.store_string(JSON.stringify(head) + "\n")
		file.close()


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	_fixtures()
	var dock: Control = load(DOCK_PATH).new()
	root.add_child(dock)
	dock.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	var backend: Node = load("res://addons/joinery/chat_bridge.gd").new()
	dock.add_child(backend)
	var fake := GDScript.new()
	fake.source_code = JOB_SOURCE
	fake.reload()
	var job: RefCounted = fake.new()
	backend.set("_job", job)
	backend.call("_handle", {"kind": "connection", "state": "connected", "parallel": 3})
	dock.call("set_chat_state", "connected", "")
	var main_sent: Array[String] = []
	dock.connect(
		"prompt_submitted", func(text: String, _targets: Array) -> void: main_sent.append(text)
	)
	var sessions: Node = dock.get("_chats")
	var turns: RefCounted = sessions.get("turns")
	var composer: Control = dock.get("_composer")
	var prompt: TextEdit = composer.get("prompt")
	var header: Control = dock.get("_header")

	# Journeyman works in the first chat.
	dock.call("_on_submit", "Make a level")
	dock.call("set_chat_busy", true)
	dock.call("_on_sent")
	backend.call("_handle", {"kind": "chat", "chat_id": FIRST, "mode": "new"})
	var first: Control = dock.get("_transcript")
	_check(main_sent == ["Make a level"], "The first chat's request went out: %s" % [main_sent])

	# A new chat while it works: an agent is free, so the message box doesn't say it would wait.
	sessions.call("new_chat")
	var second: Control = dock.get("_transcript")
	_check(second != first and first.is_inside_tree(), "The working chat runs on out of sight")
	var hint := str(prompt.placeholder_text)
	_check(hint != dock.get("OTHER_HINT"), "With an agent free, nothing says it waits: " + hint)

	# Its message starts at once as its own chat's turn, beside the other.
	prompt.text = "Make a lamp"
	dock.call("_on_submit", "Make a lamp")
	var started := _sent(job, "chat_task")
	_check(started.size() == 1, "It starts now, beside the other chat: %s" % [job.get("lines")])
	_check(main_sent.size() == 1, "Not as Journeyman's own request")
	_check(prompt.text.is_empty(), "The message left the message box")
	var line: Dictionary = started[0] if not started.is_empty() else {}
	_check(str(line.get("text")) == "Make a lamp", "The message is sent: %s" % [line])
	_check(str(line.get("chat_id", "x")) == "", "From a new chat")
	var ref := str(line.get("ref", ""))
	_check(not ref.is_empty(), "With a ref its events come back with")
	var queue: Control = second.get("queue")
	_check(queue.call("size_queued") == 0, "Nothing waits in its queue")
	var lamp: Control = second.call("current_card")
	_check(lamp != null and str(lamp.get("request")) == "Make a lamp", "Its card is in its chat")
	_check(first.call("current_card") != lamp, "Not in the working chat")
	_check(composer.get("_busy"), "Its own Stop stands in for Send")

	# The host takes it and names the new chat; its words and steps go to its own chat.
	var working := {
		"kind": "task",
		"task_id": "t1",
		"origin": "chat",
		"ref": ref,
		"chat_id": "",
		"state": "working",
		"title": "Make a lamp",
	}
	backend.call("_handle", working)
	_check(str(lamp.get("state")) == "working", "The card is taken: %s" % lamp.get("state"))
	backend.call("_handle", {"kind": "chat", "chat_id": SECOND, "mode": "new", "task_id": "t1"})
	backend.call("_handle", working.merged({"chat_id": SECOND}, true))
	_check(sessions.call("current_id") == SECOND, "The new chat is named")
	_check(backend.get("chat_id") == SECOND, "Its next message continues it")
	backend.call(
		"_handle",
		{"kind": "message_delta", "text": "A brass lamp.", "task_id": "t1", "chat_id": SECOND}
	)
	backend.call(
		"_handle",
		{"kind": "progress", "text": "Modelling in Blender", "task_id": "t1", "chat_id": SECOND}
	)
	_check(
		str(second.call("get_parsed_text")).contains("A brass lamp."), "Its reply is in its chat"
	)
	_check(not str(first.call("get_parsed_text")).contains("brass"), "Not in the other chat")
	_check(lamp.call("shows_step", "Modelling in Blender"), "Its step is on its own card")
	var tasks: Control = dock.get("_tasks")
	_check(tasks.call("card", "t1") == null, "No task card: it is a chat's own turn")
	_check(header.call("agents_text") == "2 agents working", "Two agents work")

	# While it works, another message in this chat waits in this chat's queue as always.
	dock.call("_on_submit", "Make it taller")
	_check(queue.call("size_queued") == 1, "A second message waits for this chat's own turn")
	_check(queue.get("heading").text == "Queued", "Behind its own turn, not the other chat")
	_check(_sent(job, "chat_task").size() == 1, "Nothing more started")
	queue.call("remove", queue.call("first"))

	# Back to the first chat: the second keeps working out of sight, with its own dot.
	sessions.call("open_chat", FIRST)
	_check(dock.get("_transcript") == first, "The working chat comes back")
	_check(second.is_inside_tree() and not second.visible, "The second runs on out of sight")
	_check(composer.get("_busy"), "The first chat's Stop")
	var ids: PackedStringArray = sessions.call("working_ids")
	_check(ids.has(FIRST) and ids.has(SECOND), "Both chats are at work: %s" % [ids])
	(
		backend
		. call(
			"_handle",
			{
				"kind": "message_delta",
				"text": " With a glass shade.",
				"task_id": "t1",
				"chat_id": SECOND,
			}
		)
	)
	_check(str(second.call("get_parsed_text")).contains("glass shade"), "It goes on out of sight")
	sessions.call("open_list")
	var page: Control = sessions.get("page")
	_check(page.call("row_for", FIRST).get("working"), "The Chats list marks the first chat")
	_check(page.call("row_for", SECOND).get("working"), "And the second")
	_check(not page.call("row_for", THIRD).get("working"), "And only those")
	page.call("set_open", false)

	# Its change waits: a line in its chat, its own row in the Review tab.
	sessions.call("open_chat", SECOND)
	_check(dock.get("_transcript") == second, "The second chat comes back, live")
	_check(composer.get("_busy"), "With its own Stop")
	(
		backend
		. call(
			"_handle",
			{
				"kind": "file_preview",
				"transaction_id": "tx1",
				"summary": "Lamp",
				"task_id": "t1",
				"chat_id": SECOND,
			}
		)
	)
	backend.call(
		"_handle",
		{"kind": "timing", "outcome": "done", "total_ms": 3000, "task_id": "t1", "chat_id": SECOND}
	)
	backend.call(
		"_handle",
		working.merged(
			{"chat_id": SECOND, "state": "in_review", "transaction_id": "tx1", "summary": "Lamp"},
			true
		)
	)
	var offers: PackedStringArray = second.call("offer_texts")
	_check(offers.has("Ready for review: Lamp"), "Its change is offered in its chat: %s" % [offers])
	_check(not composer.get("_busy"), "Its turn is over: Send is back")
	_check(sessions.call("working_ids") == PackedStringArray([FIRST]), "Only the first works now")
	backend.call("_handle", working.merged({"chat_id": SECOND, "state": "accepted"}, true))
	var said: PackedStringArray = second.call("notice_texts")
	_check(said.has("Change accepted"), "The decision is a line in its chat: %s" % [said])

	# Stop on a chat's own turn stops only that turn.
	prompt.text = "Make a rug"
	dock.call("_on_submit", "Make a rug")
	var rug := _sent(job, "chat_task")
	_check(rug.size() == 2 and str(rug[1].get("chat_id")) == SECOND, "It continues its chat")
	(
		backend
		. call(
			"_handle",
			{
				"kind": "task",
				"task_id": "t2",
				"origin": "chat",
				"ref": str(rug[1].get("ref")),
				"chat_id": SECOND,
				"state": "working",
			}
		)
	)
	dock.call("_request_cancel")
	var stops := _sent(job, "cancel")
	_check(stops.size() == 1 and str(stops[0].get("task_id")) == "t2", "Stop stops only it")
	backend.call(
		"_handle",
		{"kind": "task", "task_id": "t2", "origin": "chat", "chat_id": SECOND, "state": "stopped"}
	)
	_check(not composer.get("_busy"), "Stopped")
	(second.get("queue") as Control).call("resume")

	# Every agent busy: a message from a third chat waits in its queue, as before.
	backend.set("parallel", 2)
	sessions.call("new_chat")
	var third: Control = dock.get("_transcript")
	(
		backend
		. call(
			"_handle",
			{
				"kind": "task",
				"task_id": "t4",
				"title": "Make a chair",
				"origin": "user",
				"state": "working",
			}
		)
	)
	await process_frame
	_check(str(prompt.placeholder_text) == dock.get("OTHER_HINT"), "It says it would wait")
	dock.call("_on_submit", "Make a fence")
	var fence_queue: Control = third.get("queue")
	_check(fence_queue.call("size_queued") == 1, "It waits in its chat's queue")
	_check(
		fence_queue.get("heading").text == dock.get("OTHER_WAIT"),
		"Saying what it waits for: " + str(fence_queue.get("heading").text)
	)
	_check(_sent(job, "chat_task").size() == 2, "Nothing went out")

	# An agent comes free: the waiting message starts beside the other chat.
	(
		backend
		. call(
			"_handle",
			{
				"kind": "task",
				"task_id": "t4",
				"title": "Make a chair",
				"origin": "user",
				"state": "done",
			}
		)
	)
	await process_frame
	var fence := _sent(job, "chat_task")
	_check(fence.size() == 3 and str(fence[2].get("text")) == "Make a fence", "Then it starts")
	_check(fence_queue.call("size_queued") == 0, "And left the queue")
	var fence_card: Control = third.call("current_card")
	_check(fence_card != null and str(fence_card.get("request")) == "Make a fence", "On its card")

	# The host found every agent busy after all: back to the queue it goes.
	backend.call(
		"_handle", {"kind": "chat_wait", "ref": str(fence[2].get("ref")), "full": true, "text": ""}
	)
	_check(fence_queue.call("size_queued") == 1, "It waits again")
	_check(third.call("current_card") == null, "Its card went back")
	_check(main_sent.size() == 1, "Never as Journeyman's own request")

	backend.set("_job", null)
	dock.queue_free()
	await process_frame
	for file_name: String in DirAccess.get_files_at(_folder()):
		DirAccess.remove_absolute(_folder().path_join(file_name))
	print("chat turns: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
