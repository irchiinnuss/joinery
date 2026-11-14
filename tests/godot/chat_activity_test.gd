extends SceneTree
## How work shows in the sidebar while Journeyman is busy:
## - one live activity line: each new step replaces the one before, and a reply another reply
##   follows while the turn still works becomes a step, so only the last reply stays as text;
##   the finished steps fold behind "Completed in …" and open together.

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
	backend.queue_free()
	dock.queue_free()
	await process_frame
	print("chat activity: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## One live line while it works; interim replies become steps; the steps fold when done.
func _activity(dock: Control) -> void:
	var transcript: Object = dock.get("_transcript")
	dock.call("_on_submit", "Make the door wider")
	var card: Control = transcript.call("current_card")
	dock.call("set_chat_busy", true)
	dock.call("show_execution", {"name": "GPT-6.1 Sol", "mode": "fast"})
	dock.call("set_chat_state", "working", "Opening door.blend in Blender")
	dock.call("set_chat_state", "working", "Widening the door")
	_check(
		card.call("visible_steps") == PackedStringArray(["Widening the door"]),
		"Only the newest step shows: %s" % card.call("visible_steps")
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
		card.call("visible_steps") == PackedStringArray(["I'll open door.blend first."]),
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
