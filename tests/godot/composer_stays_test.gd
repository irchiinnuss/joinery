extends SceneTree
## The message box stays under the conversation, whole and inside the sidebar, through whole
## turns as the chat bridge reports them: a long Markdown reply, a to-do, a change going to
## review and decided, a change kept without asking, a message queued while Journeyman works,
## and a sidebar made short while a change waits.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const STYLE := preload("res://addons/joinery/sidebar_style.gd")
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
var _dock: Control
var _bridge: Node


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 4) -> void:
	for index: int in count:
		await process_frame


## A long reply in Markdown: headings, bold, code and lists, more than the sidebar shows.
static func _long_reply() -> String:
	var text := ""
	for index: int in 30:
		text += "## Part %d\n\nThe **engine** keeps `running` when you let go:\n\n" % index
		text += "- Letting go no longer stops it.\n- It stops when you switch it off.\n\n"
	return text


## The message box is shown, has a size, sits in the sidebar's column below the conversation
## and inside the sidebar, and no page holds it hidden.
func _composer_stays(when: String) -> void:
	await _frames()
	var composer: Control = _dock.get("_composer")
	var content: Control = _dock.get("_content")
	var transcript: Control = _dock.get("_transcript")
	var column := composer.get_parent() as Control
	_check(composer.is_visible_in_tree(), "%s: the message box shows" % when)
	_check(column != null and column.get_parent() == content, "%s: it stays in place" % when)
	_check(
		composer.size.x > 0.0 and composer.size.y >= composer.get_combined_minimum_size().y - 0.5,
		"%s: it has its whole size: %s" % [when, composer.size]
	)
	var inside := _dock.get_global_rect().grow(0.5)
	_check(
		inside.encloses(composer.get_global_rect()),
		"%s: it is inside the sidebar: %s in %s" % [when, composer.get_global_rect(), inside]
	)
	_check(
		transcript.get_global_rect().end.y <= composer.get_global_rect().position.y + 0.5,
		"%s: the conversation ends above it" % when
	)
	for meta: StringName in column.get_meta_list():
		_check(not str(meta).begins_with(STYLE.HELD), "%s: no page holds it: %s" % [when, meta])


func _turn(request: String, reply: String) -> void:
	_dock.call("_on_submit", request)
	_bridge.call("_handle", {"kind": "chat", "chat_id": "chat-a", "mode": "new"})
	_bridge.call("_handle", {"kind": "execution", "model": "opus", "name": "Opus"})
	_bridge.call("_handle", {"kind": "progress", "text": "Reading push_mower.gd"})
	_bridge.call("_handle", {"kind": "trail", "state": "running", "text": "Editing push_mower.gd"})
	for part: String in [reply.substr(0, reply.length() / 2), reply.substr(reply.length() / 2)]:
		_bridge.call("_handle", {"kind": "message_delta", "text": part, "new_message": false})
		await process_frame


func _finish() -> void:
	_bridge.call("_handle", {"kind": "timing", "outcome": "done", "total_ms": 779955})
	_bridge.call("_handle", {"kind": "done", "ok": true})


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	root.size = Vector2i(420, 760)
	_dock = load(DOCK_PATH).new()
	root.add_child(_dock)
	_dock.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	_bridge = load("res://addons/joinery/chat_bridge.gd").new()
	_dock.add_child(_bridge)
	var fake := GDScript.new()
	fake.source_code = JOB_SOURCE
	fake.reload()
	var job: RefCounted = fake.new()
	_bridge.set("_job", job)
	# Wired as the plugin wires them (plugin.gd).
	_bridge.status_changed.connect(_dock.set_chat_state)
	_bridge.busy_changed.connect(_dock.set_chat_busy)
	_bridge.message_delta.connect(_dock.append_agent_text)
	_bridge.message_received.connect(_dock.show_agent_message)
	_bridge.completed.connect(_dock.finish_agent_response)
	_bridge.execution_changed.connect(_dock.show_execution)
	_bridge.timing_received.connect(_dock.show_timing)
	_bridge.call("_handle", {"kind": "connection", "state": "connected", "parallel": 3})
	_bridge.set("_job", job)
	_dock.prompt_submitted.connect(
		func(text: String, targets: Array) -> void: _bridge.call("submit", text, targets)
	)
	await _composer_stays("Before any request")
	var reply := _long_reply()

	# A whole turn with a long Markdown reply.
	await _turn("Fix the mower's cutting sound", reply)
	await _composer_stays("While Journeyman works")
	_finish()
	await _composer_stays("A turn finished with a long reply")
	var composer: Control = _dock.get("_composer")
	_check(not composer.get("_busy"), "Send is back once the turn finished")

	# A to-do, then a change that goes to review, then Accept.
	var todo := {
		"kind": "todo",
		"todo_id": "t1",
		"state": "waiting",
		"steps": ["Turn the mowing loop down to -18 LUFS", "Render it again", "Check it"],
		"done_when": [{"id": "d1", "text": "The loop is at -18 LUFS", "kind": "test"}],
	}
	_bridge.call("_handle", todo)
	await _composer_stays("A to-do shows")
	await _turn("Turn the mowing loop down", reply)
	_bridge.call("_handle", {"kind": "file_preview", "transaction_id": "tx1", "summary": "Quieter"})
	(
		_dock
		. show_file_review(
			"Mower engine: steady idle and mowing sounds",
			"~ game/tools/push_mower.gd\n",
			[],
			[{"path": "game/tools/push_mower.gd", "change": "modified"}],
			"tx1",
		)
	)
	_finish()
	await _composer_stays("A turn finished with its change waiting for review")
	_dock.call("hide_edit_review", "Change accepted", "success")
	await _composer_stays("The change was accepted")

	# A message sent while Journeyman works waits, then goes once the turn finishes.
	await _turn("Make the grass bend more", reply)
	_dock.call("_on_submit", "Then add clippings")
	_check(_dock.get("_transcript").queue.size_queued() == 1, "A message sent meanwhile waits")
	await _composer_stays("A message waits for the turn")
	_finish()
	await _composer_stays("The waiting message went out")
	await _turn("", reply)
	_finish()
	await _composer_stays("Its turn finished too")

	# A change Joinery kept without asking: no review, a quiet line on the card.
	await _turn("Put the blade back", reply)
	var card: Control = _dock.call("_card")
	_finish()
	card.call("set_outcome", "auto", "Accepted automatically")
	await _composer_stays("A turn finished with its change kept without asking")

	# A short sidebar while a change waits: the conversation gives up its height first.
	await _turn("Darken the bag", reply)
	_dock.show_file_review("Darker bag", "~ art/bag.blend\n", [], [], "tx2")
	_finish()
	root.size = Vector2i(420, 420)
	await _composer_stays("A short sidebar with a change waiting")
	_dock.call("hide_edit_review", "Discarded", "rejected")
	await _composer_stays("The change was discarded")
	print("composer stays: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
