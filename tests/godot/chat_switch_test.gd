extends SceneTree
## Clicking a chat in the Chats list opens it, or says on screen why not, in every state: while
## Journeyman works in one chat, while another chat runs beside it, and while the chat on screen
## holds a queued message (which goes out only from its own screen).

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const FIRST := "20261118-100000-aaaaaa"
const SECOND := "20261118-100000-bbbbbb"
const OLDER := "20261101-100000-cccccc"
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


func _folder() -> String:
	return ProjectSettings.globalize_path("res://.joinery/chats")


func _fixtures() -> void:
	DirAccess.make_dir_recursive_absolute(_folder())
	var when := Time.get_datetime_string_from_unix_time(int(Time.get_unix_time_from_system()))
	for id: String in [FIRST, SECOND, OLDER]:
		var head := {"type": "chat", "id": id, "title": id, "updated": when + "+00:00"}
		var user := {"type": "user", "text": "Hello from " + id}
		var file := FileAccess.open(_folder().path_join(id + ".jsonl"), FileAccess.WRITE)
		file.store_string(JSON.stringify(head) + "\n" + JSON.stringify(user) + "\n")
		file.close()


## Click [param chat_id]'s row in the Chats list, as the user does.
func _click(sessions: Node, chat_id: String) -> void:
	sessions.call("open_list")
	var page: Control = sessions.get("page")
	var row: Node = page.call("row_for", chat_id)
	_check(row != null, "The list has a row for " + chat_id)
	if row != null:
		(row.get("_open") as Button).pressed.emit()


## What the user sees after a click: the chat opened, or a visible line saying why not.
func _opened_or_told(dock: Control, sessions: Node, chat_id: String, what: String) -> void:
	var page: Control = sessions.get("page")
	var shown := str(sessions.get("shown_id"))
	var transcript: Control = dock.get("_transcript")
	var caption: Control = transcript.get("_caption")
	var told := (
		caption.is_visible_in_tree() and not str(transcript.get("_caption_text").text).is_empty()
	)
	_check(not page.visible, what + ": the list closes")
	_check(shown == chat_id or told, what + ": it opens or says why not (shown %s)" % shown)


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
	backend.set("_job", fake.new())
	backend.call("_handle", {"kind": "connection", "state": "connected", "parallel": 1})
	dock.call("set_chat_state", "connected", "")
	dock.connect("prompt_submitted", func(_t: String, _x: Array) -> void: pass)
	var sessions: Node = dock.get("_chats")

	# Idle: a click opens an older chat, read-only.
	_click(sessions, OLDER)
	_opened_or_told(dock, sessions, OLDER, "Idle")
	_check(sessions.get("shown_id") == OLDER, "Idle, the chat opens")
	sessions.call("continue_chat")

	# Journeyman works in one chat: a click on another chat opens it.
	_click(sessions, FIRST)
	sessions.call("continue_chat")
	dock.call("_on_submit", "Make a level")
	dock.call("set_chat_busy", true)
	dock.call("_on_sent")
	_click(sessions, SECOND)
	_opened_or_told(dock, sessions, SECOND, "While another chat works")
	_check(sessions.get("shown_id") == SECOND, "It opens while the other chat works")
	sessions.call("continue_chat")

	# A message waits here for the working chat: a click on another chat opens it, or says why
	# not where the user can see it.
	dock.call("_on_submit", "Make a lamp")
	var queue: Control = (dock.get("_transcript") as Control).get("queue")
	_check(queue.call("size_queued") == 1, "The message waits for the working chat")
	_click(sessions, FIRST)
	_opened_or_told(dock, sessions, FIRST, "With a queued message, to the working chat")
	_click(sessions, OLDER)
	_opened_or_told(dock, sessions, OLDER, "With a queued message, to an older chat")
	sessions.call("open_chat", SECOND)

	# New chat from the list's header button, with a message queued: opens or says why not.
	sessions.call("open_list")
	(sessions.get("page") as Control).emit_signal("new_chat_requested")
	var page: Control = sessions.get("page")
	_check(not page.visible, "New chat: the list closes")

	backend.set("_job", null)
	dock.queue_free()
	await process_frame
	for file_name: String in DirAccess.get_files_at(_folder()):
		DirAccess.remove_absolute(_folder().path_join(file_name))
	print("chat switch: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
