extends SceneTree
## A request's to-do: the card shows Journeyman's steps and "done when" items before any work;
## every line edits in place and has its own remove; Go sends the list as left as a request of
## its own ("Go"); the items then read Pass, Fail or Inconclusive with the evidence, on the card
## and in the review. Cancel drops a to-do; one dropped for a new message steps aside. Each step
## is a short title with its detail behind a chevron; the user adds, moves and removes steps,
## a repeat is refused, and while the work runs each step is checked off as it is done.

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
const STEPS := [
	"Put a lever beside the gate",
	"Open the gate when the lever is pulled",
	"Put the key in the vault",
]
const DETAILS := ["Brass lever, 40 cm, on the gate's left post", "", "On the vault's table"]
const DONE := [
	{"id": "d1", "text": "The player can reach the vault", "kind": "reach"},
	{"id": "d2", "text": "The gate opens when the lever is pulled", "kind": "test"},
	{"id": "d3", "text": "No errors in 60 s of play", "kind": "no_errors"},
	{"id": "d4", "text": "The key looks like brass", "kind": "look"},
]

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _sent(job: RefCounted, kind: String) -> Dictionary:
	var lines: Array[Dictionary] = job.get("lines")
	for index in range(lines.size() - 1, -1, -1):
		if str(lines[index].get("kind", "")) == kind:
			return lines[index]
	return {}


## The card's fields to edit in place: the steps' titles and the done-when items.
func _fields(card: Node) -> Array[Node]:
	var found: Array[Node] = []
	for words: Node in card.find_children("Words", "", true, false):
		if words is LineEdit or words is TextEdit:
			found.append(words)
	return found


func _results() -> Array:
	return [
		{
			"id": "d1",
			"text": "The player can reach the vault",
			"status": "fail",
			"evidence": "The bot didn't get to Vault in 1:00 of play (closest 3.4), seed 4242.",
		},
		{
			"id": "d3",
			"text": "No errors in 60 s of play",
			"status": "pass",
			"evidence": "No errors in 1:00 of play, seed 4242.",
		},
		{
			"id": "d2",
			"text": "The gate opens when the lever is pulled",
			"status": "inconclusive",
			"evidence": "res://tests/test_gate.gd didn't run: the project has no GUT tests set up.",
		},
	]


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
	backend.set("chat_id", "chat-a")
	dock.prompt_submitted.connect(
		func(text: String, targets: Array) -> void: backend.call("submit", text, targets)
	)
	var tasks: Control = dock.get("_tasks")

	# Journeyman proposes: the card shows the steps and what done means, ready to edit.
	var event := {
		"kind": "todo",
		"todo_id": "t1",
		"state": "waiting",
		"steps": STEPS,
		"details": DETAILS,
		"done_when": DONE,
	}
	backend.call("_handle", event)
	var card: Control = tasks.get("todo_card")
	_check(card != null and card.is_visible_in_tree(), "The to-do card shows")
	_check(not card.get("minimised"), "A new to-do opens expanded, to read and edit")
	_check(card.call("steps") == PackedStringArray(STEPS), "Its steps: %s" % card.call("steps"))
	_check((card.call("done_rows") as Array).size() == 4, "Its done-when items")
	_check((card.get("go") as Button).is_visible_in_tree(), "Go is there")
	_check(card.call("step_text") == "Edit anything, then press Go.", card.call("step_text"))
	var fields := _fields(card)
	_check(fields.size() == 7, "Every line edits in place: %d" % fields.size())

	# Each step's detail waits behind its chevron, and edits in place there too.
	var rows: Array[Control] = card.call("step_rows")
	var first: Control = rows[0]
	_check(not (first.get("_detail_box") as Control).visible, "The detail starts closed")
	(first.get("chevron") as Button).pressed.emit()
	_check(first.get("open") and str(first.call("detail_text")) == DETAILS[0], "Opened: detail")
	_check(first.get("detail") is TextEdit, "The detail edits in place while waiting")
	(first.get("detail") as TextEdit).text = "Brass lever, 50 cm"

	# A step is added at the end; one that says what step 3 says is refused.
	var added: Control = card.call("add_step")
	_check(added != null and (card.call("step_rows") as Array).size() == 4, "Add a step")
	(added.get("title") as TextEdit).text = "put the KEY in the vault."
	added.call("commit")
	await process_frame
	_check((card.call("step_rows") as Array).size() == 3, "The repeat is refused")
	_check(card.call("step_text") == "Step 3 already says that.", card.call("step_text"))
	added = card.call("add_step")
	(added.get("title") as TextEdit).text = "Hide a spare key"
	added.call("commit")
	# Alt+Up moves it above step 3.
	var up := InputEventKey.new()
	up.pressed = true
	up.alt_pressed = true
	up.keycode = KEY_UP
	(added.get("title") as TextEdit).gui_input.emit(up)
	_check(
		card.call("steps") == PackedStringArray([STEPS[0], STEPS[1], "Hide a spare key", STEPS[2]]),
		"Moved up: %s" % card.call("steps")
	)
	var named := str((added.get("title") as TextEdit).accessibility_name)
	_check(named.begins_with("Step 3:"), "Renumbered: %s" % named)

	# The user rewords the first step, removes the second and the look item, presses Go.
	fields[0].set("text", "Put a lever by the gate")
	var removes: Array[Button] = card.call("remove_buttons")
	_check(removes.size() == 8, "Every line has its remove: %d" % removes.size())
	removes[1].pressed.emit()
	removes[7].pressed.emit()
	_check(
		(
			card.call("steps")
			== PackedStringArray([STEPS[0].replace("beside", "by"), "Hide a spare key", STEPS[2]])
		),
		"Edited steps: %s" % card.call("steps")
	)
	(card.get("go") as Button).pressed.emit()
	var go := _sent(job, "todo_go")
	_check(str(go.get("text", "")) == "Go", "Go is a request of its own: %s" % go)
	_check(str(go.get("todo_id", "")) == "t1", "For this to-do")
	var sent_steps: Array = go.get("steps", [])
	_check(sent_steps.size() == 3, "With the steps as left: %s" % [sent_steps])
	var wanted := [
		[STEPS[0].replace("beside", "by"), "Brass lever, 50 cm", 1],
		["Hide a spare key", "", 0],
		[STEPS[2], DETAILS[2], 3],
	]
	for index: int in mini(sent_steps.size(), wanted.size()):
		var sent: Dictionary = sent_steps[index]
		_check(
			(
				str(sent["title"]) == wanted[index][0]
				and str(sent["detail"]) == wanted[index][1]
				and int(sent["from"]) == wanted[index][2]
			),
			"Step %d as left: %s" % [index + 1, sent]
		)
	var ids: Array = []
	for item: Variant in go.get("done_when", []):
		ids.append(str((item as Dictionary)["id"]))
	_check(ids == ["d1", "d2", "d3"], "And the done-when items as left: %s" % [ids])
	_check(_sent(job, "prompt").is_empty(), "Not as a plain prompt")
	var todo_list: Script = load("res://addons/joinery/sidebar_todo.gd")
	_check(card.get("minimised"), "Go minimises it to its one line")
	_check(todo_list.call("minimised", "chat-a", "t1"), "And the chat remembers that")

	# Working: the list is read-only; then the items are checked.
	var titles: Array = []
	var details: Array = []
	for step: Variant in sent_steps:
		titles.append(str((step as Dictionary)["title"]))
		details.append(str((step as Dictionary)["detail"]))
	event = {
		"kind": "todo",
		"todo_id": "t1",
		"state": "going",
		"steps": titles,
		"details": details,
		"done_when": [DONE[0], DONE[1], DONE[2]],
	}
	backend.call("_handle", event)
	_check(_fields(card).is_empty(), "Read-only now")
	_check(not (card.get("go") as Button).is_visible_in_tree(), "No Go while working")
	_check(card.call("step_text") == "Working through the steps…", card.call("step_text"))

	# Each step is checked off as Journeyman says it is done, not all at the end.
	var count: int = (event["steps"] as Array).size()
	var moves := [["started", "", ""], ["done", "started", ""]]
	backend.call("_handle", {"kind": "todo", "todo_id": "t1", "step": 1, "step_states": moves[0]})
	rows = card.call("step_rows")
	_check(rows[0].get("progress") == "started", "Step 1 is being worked on")
	_check(card.call("step_text") == "Working through the steps…", "The state stays going")
	backend.call("_handle", {"kind": "todo", "todo_id": "t1", "step": 2, "step_states": moves[1]})
	_check(rows[0].get("progress") == "done" and rows[1].get("progress") == "started", "Moved on")
	_check((card.call("step_rows") as Array)[0] == rows[0], "In place: the rows are the same")
	_check(card.call("progress") == [1, count + 3], "1 done: %s" % [card.call("progress")])
	var live: Control = card.get("live")
	_check(card.get("minimised") and live.visible, "Minimised while working: the live light")
	var line: String = (card.get("title_label") as Label).text
	_check(line == "To-do · 1 of %d done" % (count + 3), "Counts as it goes: %s" % line)
	# Opened by hand: the step line says it instead, and the chat remembers it open.
	(card.get("fold") as Button).pressed.emit()
	_check(not card.get("minimised") and not live.visible, "Open, the step line says it instead")
	_check(not todo_list.call("minimised", "chat-a", "t1"), "Remembered open")
	# Steps that need nothing first run at once as tasks (run_steps = "together").
	backend.call("_handle", {"kind": "todo", "todo_id": "t1", "state": "steps", "together": [1, 3]})
	_check(card.call("step_text") == "Making steps 1 and 3 at once…", card.call("step_text"))
	_check(card.call("working"), "Still working while the steps run")
	backend.call("_handle", {"kind": "todo", "todo_id": "t1", "state": "checking"})
	_check((card.call("progress") as Array)[0] == count, "Checking: every step is done")
	_check(card.call("step_text") == "Checking what done means…", "It says it is checking")
	(
		backend
		. call(
			"_handle",
			{
				"kind": "todo",
				"todo_id": "t1",
				"state": "checked",
				"results": _results(),
				"summary": "1 of 3 done",
			}
		)
	)
	_check(card.call("step_text") == "1 of 3 done", "Checked: %s" % card.call("step_text"))
	var results: Array[Dictionary] = card.call("done_rows")
	_check(
		results[0]["status"] == "fail" and str(results[0]["evidence"]).contains("Vault"),
		str(results)
	)
	_check(results[1]["status"] == "inconclusive" and results[2]["status"] == "pass", str(results))
	_check(not live.visible, "Checked: no light")
	_check(card.find_children("Evidence", "Label", true, false).size() == 3, "Evidence shows")
	_check((card.get("close") as Button).visible, "It can be dismissed")

	# Minimised to one line, and opened again; the chat remembers which.
	(card.get("fold") as Button).pressed.emit()
	_check(card.get("minimised"), "The chevron minimises it")
	var title: Label = card.get("title_label")
	_check(title.text == "To-do · 4 of 6 done", "One line: %s" % title.text)
	_check(not live.visible, "Finished: no live light on the line")
	_check(not (card.get("_body") as Control).visible, "Only the header line shows")
	_check(todo_list.call("minimised", "chat-a", "t1"), "The chat remembers it minimised")
	_check(not todo_list.call("minimised", "chat-b", "t1"), "Another chat doesn't")
	(card.get("fold") as Button).pressed.emit()
	_check(not card.get("minimised") and title.text == "To-do", "Opened again: %s" % title.text)
	_check(not todo_list.call("minimised", "chat-a", "t1"), "And remembered open")
	(card.get("fold") as Button).pressed.emit()
	# Shown again (the chat reopened), the same to-do keeps the way the user left it.
	tasks.get_node("TodoList").call("_replace", "t1")
	card = tasks.get("todo_card")
	_check(card.get("minimised"), "The same to-do comes back minimised")

	# The review shows the same results under what changed.
	var summary: Control = load("res://addons/joinery/review_summary.gd").new()
	dock.add_child(summary)
	summary.call("show_review", {"changes": [{"path": "game/gate.gd", "change": "modified"}]})
	_check(summary.call("done_text") == "", "No to-do, no done-when chip")
	summary.call("update", {"done_when": _results()})
	_check(summary.call("done_text") == "1 of 3 done", "Chip: %s" % summary.call("done_text"))
	var shown: Array[Dictionary] = summary.call("done_rows")
	_check(shown.size() == 3 and shown[0]["status"] == "fail", "Rows: %s" % [shown])
	_check(str(shown[2]["evidence"]).begins_with("res://tests/test_gate.gd"), "%s" % [shown])
	_check(not summary.call("simple", 0), "A Fail opens the panel rather than deciding inline")
	summary.queue_free()

	# A second to-do, cancelled.
	(card.get("close") as Button).pressed.emit()
	await process_frame
	_check(tasks.get("todo_card") == null, "Dismissed")
	event = {"kind": "todo", "todo_id": "t2", "state": "waiting", "steps": STEPS, "done_when": []}
	backend.call("_handle", event)
	card = tasks.get("todo_card")
	_check(not card.get("minimised"), "A new to-do in the same chat opens expanded again")
	_check(not (card.get("_done_title") as Control).visible, "No done-when items, no heading")
	(card.get("cancel") as Button).pressed.emit()
	_check(str(_sent(job, "todo_cancel").get("todo_id", "")) == "t2", "Cancel drops it")
	backend.call("_handle", {"kind": "todo", "todo_id": "t2", "state": "cancelled"})
	_check(card.call("step_text") == "Cancelled", "It says so")
	# A to-do the card never showed, dropped, changes nothing.
	backend.call("_handle", {"kind": "todo", "todo_id": "t9", "state": "dropped"})
	_check(tasks.get("todo_card") == card, "An unknown to-do is ignored")

	backend.queue_free()
	dock.queue_free()
	await process_frame
	print("todo card: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
