extends RefCounted
## The visual suite's work trail: while Journeyman works, ONE live line that replaces itself
## (the current call with the clock, the newest thought faint under it), then a test run on that
## line (the test it checks now, how far, a failure by name), then folded into "Completed in …",
## then opened to the whole record (an edit's +/− lines, a render that landed, the test results
## grouped by folder).
## [param suite] is visual_sidebar_test.gd, whose `_build`, `_shot` and `_thumbnail` these use;
## the screens are taken on a sidebar of their own, with [param beside]'s hidden meanwhile.
## A line naming a project file shows its name as a link ("trail-link", and in the open record).
## [method finished_long]: a turn that finished with a long reply ("turn-finished-long").


static func shots(suite: Variant, light: bool, width: int, beside: Control) -> void:
	var behind := beside.get_parent() as Control
	behind.hide()
	var built: Array = suite._build(light)
	var host: Control = built[0]
	var chat: Control = built[1]
	await suite._frames()
	chat.set_chat_agent("claude")
	chat.set_chat_state("connected", "Claude Code connected")
	chat.call("_on_submit", "Make the door a little wider and keep the frame lined up.")
	var card: Control = chat.get("_transcript").call("current_card")
	chat.set_chat_busy(true)
	var render: String = suite._thumbnail()
	# A project file the trail names: its name is a link.
	var door := FileAccess.open("res://door.gd", FileAccess.WRITE)
	door.store_string("extends Node\n")
	door.close()
	for event: Dictionary in [
		{
			"text": "The door's size lives in door.blend, so I'll widen it in Blender first.",
			"note": "thought",
			"state": "done",
		},
		{"id": "a", "text": "Reading door.gd", "path": "res://door.gd"},
		{"id": "a", "text": "Reading door.gd", "state": "done"},
		{"id": "b", "text": "Searching for DoorFrame"},
		{"id": "b", "text": "Searching for DoorFrame", "state": "done"},
		{"id": "c", "text": "Modelling the door in Blender: transform parts, 1.2 scale x"},
		{"id": "c", "text": "", "state": "done", "picture": render},
		{
			"id": "d",
			"text": "Editing level.gd",
			"file": "level.gd",
			"added": 12,
			"removed": 3,
			"path": "res://game/level.gd",
		},
		{"id": "d", "text": "Editing level.gd", "state": "done"},
		{"id": "e", "text": "Handing the change over"},
		{
			"text": "Once its checks pass, I'll say what changed.",
			"note": "thought",
			"state": "done"
		},
	]:
		event["kind"] = "trail"
		chat.call("add_trail", event)
		if event.get("id") == "d" and not event.has("state"):
			# The file a line names is a link, on the live line too.
			card.call("set_elapsed", 19)
			await suite._shot("trail-link", light, width, chat)
	card.call("set_elapsed", 23)
	await suite._shot("trail", light, width, chat)
	var run := {
		"kind": "tests",
		"id": "tests-1",
		"label": "Running the scene tests",
		"now": "Checking hose reaches flowerbed",
		"total": 4,
		"results":
		[
			{"name": "mower starts on lawn", "area": "garden", "passed": true, "reason": ""},
			{"name": "door opens wider", "area": "house", "passed": true, "reason": ""},
			{
				"name": "frame lines up",
				"area": "house",
				"passed": false,
				"reason": "the frame is 4 cm left of the wall",
			},
		],
		"done": false,
		"summary": "",
	}
	chat.call("add_trail", run.duplicate(true))
	card.call("set_elapsed", 31)
	await suite._shot("trail-tests", light, width, chat)
	run["now"] = ""
	run["done"] = true
	run["results"].append(
		{"name": "hose reaches flowerbed", "area": "garden", "passed": true, "reason": ""}
	)
	run["summary"] = "4 checks: 3 passed, 1 failed — frame lines up"
	chat.call("add_trail", run)

	chat.call("add_trail", {"kind": "trail", "id": "e", "text": "", "state": "done"})
	chat.append_agent_text("The door is 20% wider and the frame still lines up.", true)
	chat.finish_agent_response(true)
	chat.show_timing({"outcome": "done", "total_ms": 41000, "first_response_ms": 900})
	chat.set_chat_busy(false)
	await suite._shot("trail-done", light, width, chat)
	card.call("set_details_open", true)
	await suite._shot("trail-open", light, width, chat)
	host.queue_free()
	await suite._frames(2)
	behind.show()
	DirAccess.remove_absolute(ProjectSettings.globalize_path("res://door.gd"))


## A sound made in SuperCollider waits for review: the card's calm note names it with its tool,
## and offers Preview and Ask to fix; Ask to fix asks about that asset.
static func preview_note(suite: Variant, light: bool, width: int, chat: Control) -> void:
	var review: Object = chat.get("_review")
	var asked: Array[String] = []
	var ask := func(path: String) -> void: asked.append(path)
	review.connect("revise_requested", ask)
	(
		chat
		. show_file_review(
			"Make the engine hum lower",
			"~ audio/engine_sound.scd\n~ audio/engine_sound.wav",
			[],
			[
				{"path": "audio/engine_sound.wav", "change": "modified"},
				{"path": "audio/engine_sound.scd", "change": "modified"},
			],
		)
	)
	var card: Control = chat.get("_transcript").call("current_card")
	var row: Control = card.call("review_row")
	suite._check(
		row.call("note_text") == "Changed in SuperCollider: engine sound",
		"The note names the asset and its tool: %s" % row.call("note_text")
	)
	await suite._shot("review-note", light, width, chat)
	(row.find_child("Asktofix", true, false) as Button).pressed.emit()
	suite._check(
		asked == ["audio/engine_sound.scd"], "Ask to fix asks about that asset: %s" % [asked]
	)
	review.disconnect("revise_requested", ask)


## A turn that finished with a long Markdown reply, more than the sidebar shows: the
## conversation scrolls to its end above the message box, which stays whole and inside the
## sidebar.
static func finished_long(suite: Variant, light: bool, width: int) -> void:
	var built: Array = suite._build(light)
	var host: Control = built[0]
	var chat: Control = built[1]
	await suite._frames()
	chat.set_chat_agent("codex")
	chat.set_chat_state("connected", "Codex connected")
	chat.call("_on_submit", "Fix the mower's cutting sound; it stalls after the wind-up.")
	chat.set_chat_busy(true)
	chat.call("_on_sent")
	var reply := "I've changed the push mower's engine sounds and how it stops.\n\n"
	for part: String in ["Engine keeps running", "Sounds", "Stalling", "Tests"]:
		reply += "**%s**\n" % part
		reply += "- Letting go of the mower, or picking it up, no longer turns it off.\n"
		reply += "- It stops when you switch it off with `use_tool`, or runs out of fuel.\n\n"
	chat.append_agent_text(reply, true)
	chat.finish_agent_response(true)
	chat.show_timing({"outcome": "done", "total_ms": 779955, "first_response_ms": 900})
	chat.set_chat_busy(false)
	await suite._frames(4)
	var composer: Control = chat.get("_composer")
	var transcript: ScrollContainer = chat.get("_transcript")
	var bar := transcript.get_v_scroll_bar()
	suite._check(bar.max_value > bar.page, "The reply is longer than the sidebar shows")
	suite._check(composer.is_visible_in_tree(), "The message box shows after the turn")
	suite._check(
		chat.get_global_rect().grow(0.5).encloses(composer.get_global_rect()),
		"The message box is inside the sidebar: %s" % composer.get_global_rect()
	)
	suite._check(
		transcript.get_global_rect().end.y <= composer.get_global_rect().position.y + 0.5,
		"The conversation ends above the message box"
	)
	await suite._shot("turn-finished-long", light, width, chat)
	host.queue_free()
	await suite._frames(2)
