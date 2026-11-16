extends SceneTree
## How work shows in the sidebar while Journeyman is busy, and how its result arrives:
## - a live trail, newest first: each new step ticks the one before, and a reply another reply
##   follows while the turn still works becomes a step, so only the last reply stays as text;
##   the finished steps fold behind "Completed in …" and open together;
## - the host's trail events (every agent's tool calls) join it as short lines: one pulses, an
##   edit shows its +/− lines, progress during a call sits under it, a picture shows small;
## - a ready change opens its review by itself, but never takes the keys from someone typing:
##   it waits until the typing stops and the message box keeps the focus;
## - a task's change opens its review by itself too, unless Joinery kept it without asking or
##   the main request's review is waiting.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
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


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
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
	backend.set("_job", job)
	dock.call("set_chat_state", "connected", "")
	await _activity(dock)
	await _trail(dock, backend)
	await _review_opens(dock)
	await _task_reviews(dock, backend)
	backend.queue_free()
	dock.queue_free()
	await process_frame
	print("chat activity: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## The trail while it works, newest first; interim replies become steps; the steps fold when
## done.
func _activity(dock: Control) -> void:
	var transcript: Object = dock.get("_transcript")
	dock.call("_on_submit", "Make the door wider")
	var card: Control = transcript.call("current_card")
	dock.call("set_chat_busy", true)
	dock.call("show_execution", {"name": "GPT-6.1 Sol", "mode": "fast"})
	dock.call("set_chat_state", "working", "Opening door.blend in Blender")
	dock.call("set_chat_state", "working", "Widening the door")
	_check(
		(
			card.call("visible_steps")
			== PackedStringArray(["Widening the door", "Opened door.blend in Blender"])
		),
		"Newest first, the one before ticked: %s" % card.call("visible_steps")
	)
	# The agent says what it is about to do, works, then answers.
	dock.call("append_agent_text", "I'll open **door.blend** first.\nThen widen it.", true)
	dock.call("finish_agent_response", true)
	dock.call("set_chat_state", "working", "Exporting the model…")
	dock.call("append_agent_text", "The door is 20% wider.", true)
	await process_frame
	var replies: Array = []
	for record: Dictionary in transcript.get("_messages"):
		if record["who"] != "You":
			replies.append(str(record["text"]))
	_check(
		replies == ["The door is 20% wider."], "Only the last reply stays as text: %s" % [replies]
	)
	_check(
		(card.call("visible_steps") as PackedStringArray)[0] == "I'll open door.blend first.",
		"The interim reply is the live line: %s" % card.call("visible_steps")
	)
	dock.call("finish_agent_response", true)
	dock.call("show_timing", {"outcome": "done", "total_ms": 30000})
	dock.call("set_chat_busy", false)
	_check(card.call("visible_steps").is_empty(), "The steps fold behind Completed in 0:30")
	card.call("set_details_open", true)
	_check(
		(card.call("visible_steps") as PackedStringArray).size() == 4,
		"Opened, every step shows: %s" % card.call("visible_steps")
	)
	card.call("set_details_open", false)


## Every agent's tool calls arrive as the host's trail events and read as short lines.
func _trail(dock: Control, backend: Node) -> void:
	var transcript: Object = dock.get("_transcript")
	dock.call("_on_submit", "Move the spawn point")
	var card: Control = transcript.call("current_card")
	dock.call("set_chat_busy", true)
	var say := func(event: Dictionary) -> void:
		event["kind"] = "trail"
		backend.call("_handle", event)
	say.call({"text": "The spawn lives in level.gd.", "note": "thought", "state": "done"})
	say.call({"id": "a", "text": "Reading level.gd"})
	say.call({"id": "b", "text": "Editing level.gd", "file": "level.gd", "added": 12, "removed": 3})
	say.call({"id": "a", "text": "Reading level.gd", "state": "done"})
	_check(
		(
			card.call("visible_steps")
			== PackedStringArray(
				["Editing level.gd", "Read level.gd", "The spawn lives in level.gd."]
			)
		),
		"Newest first, the read done: %s" % card.call("visible_steps")
	)
	var trail: Node = card.get("_steps")
	var breathing := 0
	for dot: Node in trail.find_children("*", "", true, false):
		breathing += int(dot.has_meta("breathing"))
	_check(breathing == 1, "Only the live line pulses: %d" % breathing)
	var size: Node = trail.find_child("Size", true, false)
	_check(size != null, "The edit shows its lines")
	var counts: Array = []
	for label: Node in size.get_children():
		counts.append((label as Label).text)
	_check(counts == ["+12", "−3"], "Added and removed: %s" % [counts])
	# Joinery's progress while the call runs sits under it; the footer doesn't repeat it.
	dock.call("set_chat_state", "working", "Running scene tests: 3 of 8")
	var note: Label = trail.find_child("Note", true, false)
	_check(note.is_visible_in_tree() and note.text == "Running scene tests: 3 of 8", note.text)
	_check(card.call("shows_step", "Running scene tests: 3 of 8"), "The card shows the progress")
	# A picture that landed shows small under its call.
	DirAccess.make_dir_recursive_absolute(OS.get_cache_dir())
	var picture := OS.get_cache_dir().path_join("joinery-trail-test.png")
	var image := Image.create(32, 16, false, Image.FORMAT_RGBA8)
	image.fill(Color.ORANGE)
	image.save_png(picture)
	say.call({"id": "b", "text": "Editing level.gd", "state": "done", "picture": picture})
	_check(trail.find_child("Picture", true, false) != null, "The picture shows")
	say.call({"id": "c", "text": "Running the tests"})
	say.call({"id": "c", "text": "Running the tests", "state": "failed"})
	_check(
		(card.call("visible_steps") as PackedStringArray)[0] == "Running the tests",
		"A failed call keeps its words: %s" % card.call("visible_steps")
	)
	dock.call("set_chat_busy", false)
	DirAccess.remove_absolute(picture)


## A ready change opens its review by itself; while the user types it waits, and the message
## box keeps the focus.
func _review_opens(dock: Control) -> void:
	var opened: Array[int] = [0]
	dock.connect("review_requested", func() -> void: opened[0] += 1)
	var opener: Node = dock.get("_opener")
	opener.set("idle", 0.2)
	dock.call("_on_submit", "Add a creak")
	dock.call("set_chat_busy", true)
	dock.call("show_file_review", "Creak", "", [], [], "tx-1")
	_check(opened[0] == 1, "Nothing to press: the review opens by itself, once: %d" % opened[0])
	dock.call("hide_edit_review", "Change accepted", "success")
	dock.call("set_chat_busy", false)

	var prompt: TextEdit = dock.get("_prompt")
	prompt.grab_focus()
	prompt.text = "Then make the handle"
	prompt.text_changed.emit()
	dock.call("_on_submit", "Paint the door")
	prompt.text = "Then make the handle brass"
	dock.call("set_chat_busy", true)
	dock.call("show_file_review", "Paint", "", [], [], "tx-2")
	_check(opened[0] == 1, "While the user types, the review waits")
	_check(bool(opener.call("waiting")), "…and opens later")
	prompt.text = "Then make the handle brass."
	await create_timer(0.1).timeout
	_check(opened[0] == 1, "Still typing: still waiting")
	await create_timer(0.5).timeout
	await process_frame
	_check(opened[0] == 2, "Typing stopped: it opened")
	await process_frame
	_check(prompt.has_focus(), "The message box kept the focus")
	_check(prompt.text == "Then make the handle brass.", "…and every word")
	dock.call("hide_edit_review", "Change accepted", "success")
	dock.call("set_chat_busy", false)
	prompt.text = ""


## A task's ready change opens its review by itself, after a moment that lets Joinery keep it
## without asking; never over the main request's review.
func _task_reviews(dock: Control, backend: Node) -> void:
	var shown: Array[String] = []
	backend.connect("task_review", func(id: String) -> void: shown.append(id))
	var lamp := {"kind": "task", "task_id": "t1", "title": "Make a lamp", "state": "working"}
	backend.call("_handle", lamp)
	backend.call("_handle", lamp.merged({"state": "in_review", "transaction_id": "tx-lamp"}, true))
	_check(shown.is_empty(), "Not at once: an automatic accept may follow")
	await create_timer(1.0).timeout
	_check(shown == ["tx-lamp"], "The task's review opened by itself: %s" % [shown])

	# Kept without asking: accepted before the moment passed, nothing opens.
	var rug := {"kind": "task", "task_id": "t2", "title": "Make a rug", "state": "working"}
	backend.call("_handle", rug)
	backend.call("_handle", rug.merged({"state": "in_review", "transaction_id": "tx-rug"}, true))
	backend.call("_handle", rug.merged({"state": "accepted", "transaction_id": "tx-rug"}, true))
	# A console fix's change opens the same way, with no card of its own.
	var fix := {"kind": "task", "task_id": "c1", "console": true, "state": "working"}
	backend.call("_handle", fix)
	backend.call("_handle", fix.merged({"state": "in_review", "transaction_id": "tx-fix"}, true))
	await create_timer(1.0).timeout
	_check(shown == ["tx-lamp", "tx-fix"], "Kept changes open nothing: %s" % [shown])

	# The main request's review is waiting: the task's stays in the Review tab's list.
	dock.call("_on_submit", "Add a chair")
	dock.call("set_chat_busy", true)
	dock.call("show_file_review", "Chair", "", [], [], "tx-chair")
	var mug := {"kind": "task", "task_id": "t3", "title": "Make a mug", "state": "working"}
	backend.call("_handle", mug)
	backend.call("_handle", mug.merged({"state": "in_review", "transaction_id": "tx-mug"}, true))
	await create_timer(1.0).timeout
	_check(shown.size() == 2, "Never over the main request's review: %s" % [shown])
	dock.call("hide_edit_review", "Change accepted", "success")
	dock.call("set_chat_busy", false)
