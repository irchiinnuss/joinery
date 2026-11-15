extends SceneTree
## The cut card: "/cut" runs `joinery cut --json` and shows one headline, where the findings
## came from, the polish list by priority with each row's source, the proposed cuts as ticked
## boxes with their reasons, the features a Keep line covers apart (never a box), and the ship
## estimate. Write sends one `cut_write` with the ticked cuts to the chat host (nothing is
## written or deleted here); without Journeyman nothing is sent and the boxes come back; and
## the real CLI answers.

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
var _calls: Array[PackedStringArray] = []


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


static func report() -> Dictionary:
	var rows: Array = [
		{
			"id": "r1",
			"priority": "high",
			"source": "playtest",
			"title": "Crash",
			"where": "res://pier/fishing.gd:12 at 0:41",
			"detail": "fishing rod is null",
		},
		{
			"id": "r2",
			"priority": "high",
			"source": "brief",
			"title": "Fix a broken feature: Fishing at the pier",
			"where": "reach: pier",
		},
		{
			"id": "r3",
			"priority": "medium",
			"source": "access",
			"title": "The score text is small",
			"where": "ui/hud.tscn (Score)",
			"detail": "11 px",
		},
		{"id": "r4", "priority": "medium", "source": "design", "title": "The exit is never shown"},
		{"id": "r5", "priority": "low", "source": "tidy", "title": "Remove 3 unused things"},
		{"id": "r6", "priority": "low", "source": "brief", "title": "Nothing checks “Storm waves”"},
	]
	return {
		"headline": "6 to polish · 2 high · 2 cuts proposed",
		"read": ["brief features", "playtest 20261115-140200", "design check", "tidy"],
		"rows": rows,
		"cuts":
		[
			{
				"id": "c1",
				"text": "Fishing at the pier",
				"reason": "broken",
				"why": "The bot didn't get to Pier in 1:00 of play.",
			},
			{
				"id": "c2",
				"text": "The harbour has a crane",
				"reason": "scope",
				"why": "Nothing in the game is named like “crane”.",
			},
		],
		"kept": ["Jumping between roofs"],
		"ship":
		{
			"text":
			"Freeze new content by 2026-12-27 (70% of the 60 days left), then polish until 2027-01-14."
		},
	}


func _cut(args: PackedStringArray) -> Dictionary:
	_calls.append(args)
	return report()


func _sent(job: RefCounted, kind: String) -> Dictionary:
	var lines: Array[Dictionary] = job.get("lines")
	for index in range(lines.size() - 1, -1, -1):
		if str(lines[index].get("kind", "")) == kind:
			return lines[index]
	return {}


func _boxes(card: Control) -> Array[CheckBox]:
	var found: Array[CheckBox] = []
	for box: Node in card.find_children("", "CheckBox", true, false):
		found.append(box as CheckBox)
	return found


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
	var tasks: Control = dock.get("_tasks")
	var cutter: Node = tasks.get_node("Cut")
	cutter.set("runner", _cut)

	# "/cut": what is left, at a glance.
	dock.call("run_command", "/cut")
	await process_frame
	var card: Control = tasks.get("cut_card")
	_check(card != null and tasks.visible, "The cut card shows")
	_check(_calls.size() == 1 and _calls[0].has("--json"), "It ran joinery cut --json")
	var title := card.get("title_label") as Label
	_check(title.text == "6 to polish · 2 high · 2 cuts proposed", "Headline: " + title.text)
	_check(
		card.call("step_text").begins_with("From brief features, playtest"),
		"It says where it read: " + card.call("step_text")
	)
	var rows: Array = card.call("polish_rows")
	_check(rows.size() == 6, "One row per finding")
	_check((rows[5] as Control).visible == false, "The rest wait behind Show all")
	var first := rows[0] as Control
	_check(first.tooltip_text.contains("fishing.gd:12"), "Where is in the row's tooltip")
	var words: Array[String] = []
	for label: Node in first.find_children("", "Label", true, false):
		words.append((label as Label).text)
	_check(words.has("Crash") and words.has("playtest"), "Each row says its source: %s" % [words])

	# Cuts: ticked boxes with reasons; Keep features are never a box.
	var boxes := _boxes(card)
	_check(
		boxes.size() == 2 and boxes.all(func(b: CheckBox) -> bool: return b.button_pressed),
		"Each proposed cut is a ticked box"
	)
	_check(
		boxes.all(func(b: CheckBox) -> bool: return not b.text.contains("roofs")),
		"A Keep feature is never a box"
	)
	var kept := card.find_child("Kept", true, false) as Label
	_check(kept != null and kept.text.contains("Jumping between roofs"), "Keep features are named")
	var ship := card.find_child("Ship", true, false) as Label
	_check(ship != null and ship.text.begins_with("Freeze new content"), "The ship estimate shows")
	var write := card.get("write") as Button
	_check(write.text == "Write polish list and 2 cuts", "The button counts the cuts")
	boxes[1].button_pressed = false
	_check(write.text == "Write polish list and 1 cut", "Unticking follows: " + write.text)

	# Write: one change in Review, sent to the host; nothing is written here.
	write.pressed.emit()
	var sent := _sent(job, "cut_write")
	_check(sent.get("cuts", []) == ["c1"], "Only the ticked cut goes: %s" % [sent])
	_check(card.get("state") == "sent", "The card waits for Review")
	_check(title.text == "Polish list and 1 cut wait in Review", "And says where: " + title.text)
	_check(card.call("step_text") == "Nothing changes until you accept it", "Nothing changes yet")
	_check(not FileAccess.file_exists("res://design/polish.md"), "Nothing is written here")

	# Without Journeyman: nothing is sent and the boxes come back, with why.
	card.call("show_report", report())
	backend.call("_handle", {"kind": "connection", "state": "error", "message": "gone"})
	var before: int = (job.get("lines") as Array).size()
	(card.get("write") as Button).pressed.emit()
	await process_frame
	_check((job.get("lines") as Array).size() == before, "Nothing is sent when not connected")
	_check(card.call("step_text").begins_with("Connect Journeyman"), "It says why")
	_check(_boxes(card).all(func(b: CheckBox) -> bool: return not b.disabled), "Boxes come back")
	backend.call("_handle", {"kind": "connection", "state": "connected", "parallel": 3})

	(card.get("close") as Button).pressed.emit()
	await process_frame
	await process_frame
	_check(tasks.get("cut_card") == null, "Dismissed")

	await _real_cli(dock, cutter, tasks)
	backend.queue_free()
	dock.queue_free()
	await process_frame
	print("cut card: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## The real `joinery cut --json` on this project answers with a plan.
func _real_cli(dock: Control, cutter: Node, tasks: Control) -> void:
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	if python.is_empty():
		push_error("JOINERY_TEST_PYTHON is not set")
		_failed = true
		return
	var said: Array = []
	var real := func(args: PackedStringArray) -> Dictionary:
		var at := args.find("cut")
		var argv := PackedStringArray(
			["-c", "import sys; from joinery.cli import main; sys.exit(main())"]
		)
		argv.append_array(args.slice(at))
		var output: Array = []
		OS.execute(python, argv, output, true)
		said.append("".join(output))
		return load("res://addons/joinery/slash_commands.gd").call("parse_json", "".join(output))
	cutter.set("runner", real)
	dock.call("run_command", "/cut")
	await process_frame
	var card: Control = tasks.get("cut_card")
	var data: Dictionary = card.get("report")
	_check(data.has("rows") and data.has("headline"), "The real CLI answers: %s" % data)
	_check(card.get("state") == "ready", "And the card shows it: %s" % str(said).left(2000))
