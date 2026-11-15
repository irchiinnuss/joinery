extends SceneTree
## The brief's features on the /brief page: the section shows its own words while the real
## `joinery brief --status --json` runs, then one row per feature marked built, broken,
## missing or not checked with what checks it, when and the evidence; nothing without evidence
## is marked; "Check now" asks for fresh evidence and says it is working until the marks come.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const BRIEF := """# Game brief — Lamplight

## 1. Pitch
A keeper fixes the harbour lights before the storm.

## 18. Features
One feature per line.

- The harbour has a crane (reach: crane)
- A boat to row (test: res://tests/test_boat.gd)
- Fishing for fun

## 19. Keep
- Jump height 1.2 m
"""
const REPORT := {
	"headline": "1 built · 1 broken · 1 missing · 1 not checked",
	"counts": {"built": 1, "broken": 1, "missing": 1, "not_checked": 1},
	"when": "15 Nov 14:02",
	"features":
	[
		{
			"text": "Lamps light up",
			"status": "built",
			"word": "built",
			"kind": "test",
			"target": "res://tests/test_lamp.gd",
			"when": "15 Nov 14:02",
			"evidence": "res://tests/test_lamp.gd ran and passed.",
		},
		{
			"text": "The keeper reaches the lighthouse",
			"status": "broken",
			"word": "broken",
			"kind": "reach",
			"target": "lighthouse",
			"when": "15 Nov 14:02",
			"evidence":
			"The bot didn't get to Lighthouse in 1:00 of play (closest 6.0), seed 4242.",
		},
		{
			"text": "The harbour has a crane",
			"status": "missing",
			"word": "missing",
			"kind": "reach",
			"target": "crane",
			"when": "15 Nov 14:02",
			"evidence": "Nothing in the game is named like “crane”, so it isn't built.",
		},
		{
			"text": "Fishing for fun",
			"status": "not_checked",
			"word": "not checked",
			"kind": "",
			"target": "",
			"when": "",
			"evidence": "Nothing checks this line yet.",
		},
	],
}

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _texts(node: Node) -> Array[String]:
	var found: Array[String] = []
	for label: Node in node.find_children("*", "Label", true, false):
		found.append((label as Label).text)
	return found


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	root.size = Vector2i(900, 900)
	var path := ProjectSettings.globalize_path("res://design/brief.md")
	DirAccess.make_dir_recursive_absolute(path.get_base_dir())
	var file := FileAccess.open(path, FileAccess.WRITE)
	file.store_string(BRIEF)
	file.close()
	var chat := load(DOCK_PATH).new() as Control
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	chat.set_chat_state("connected", "")

	# /brief: the page, with the section's own words while the marks are read.
	chat.call("run_command", "/brief")
	await process_frame
	var page: Control = chat.get_meta("joinery_art_page")
	_check(page != null and page.visible, "/brief opens the page")
	var features := page.find_child("Features", true, false) as Control
	_check(features != null, "The brief's Features section shows")
	var state := features.get_node("State") as Control
	_check(
		(state.get_node("Text") as Label).text == "Reading the marks…",
		"It says the marks are being read"
	)
	var rows := features.get_node("Rows") as Control
	_check(
		rows.get_child_count() == 1 and rows.get_child(0) is RichTextLabel,
		"Until then the section shows its own words"
	)
	_check(chat._slash._jobs.has("brief_status"), "joinery brief --status runs")

	# The real CLI answers: lines with nothing to check are not checked, a test that isn't
	# there is missing, and nothing is marked built without evidence.
	var waited := 0.0
	while chat._slash._jobs.has("brief_status") and waited < 60.0:
		await create_timer(0.25).timeout
		waited += 0.25
	await process_frame
	var marks: Array[String] = []
	for row: Node in rows.get_children():
		if row.has_meta("status"):
			marks.append(str(row.get_meta("status")))
	_check(marks == ["not_checked", "missing", "not_checked"], "The real marks: %s" % [marks])
	var headline := (state.get_node("Text") as Label).text
	_check(
		headline.begins_with("1 missing · 2 not checked · last checked "),
		"The headline counts the marks: " + headline
	)

	# A full report: one row per feature, each with its glyph, check, time and evidence.
	page.call("show_feature_status", REPORT)
	await process_frame
	var shown: Array[Node] = rows.get_children().filter(
		func(n: Node) -> bool: return not n.is_queued_for_deletion()
	)
	_check(shown.size() == 4, "One row per feature")
	var texts := _texts(rows)
	_check(texts.has("Built · test: res://tests/test_lamp.gd · 15 Nov 14:02"), "Built row")
	_check(texts.has("Broken · reach: lighthouse · 15 Nov 14:02"), "Broken row")
	_check(texts.has("Not checked"), "A line with nothing to check is only not checked")
	_check(
		texts.has("Nothing in the game is named like “crane”, so it isn't built."),
		"The evidence shows"
	)
	_check(
		(
			(state.get_node("Text") as Label).text
			== "1 built · 1 broken · 1 missing · 1 not checked · last checked 15 Nov 14:02"
		),
		"The headline says when it was last checked"
	)
	_check(shown[3].get_node("Mark") is Control, "Not checked has a hollow dot, not an outcome")

	# Check now: fresh evidence, and the section says it is working until the marks arrive.
	var asked: Array[bool] = []
	for link: Dictionary in page.get_signal_connection_list("status_check_requested"):
		page.disconnect("status_check_requested", link["callable"])
	page.connect("status_check_requested", func(check: bool) -> void: asked.append(check))
	var check_now := features.find_child("CheckNow", true, false) as Button
	check_now.pressed.emit()
	_check(asked == [true], "Check now asks for fresh evidence")
	_check(check_now.disabled and check_now.text == "Checking…", "It says it is checking")
	page.call("show_feature_status", {"error": "Couldn't read the marks."})
	_check(not check_now.disabled and check_now.text == "Check now", "Check now comes back")
	_check(
		(state.get_node("Text") as Label).text == "Couldn't read the marks.",
		"An error says so and keeps the rows"
	)

	DirAccess.remove_absolute(path)
	await process_frame
	print("brief status: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
