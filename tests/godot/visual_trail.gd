extends RefCounted
## The visual suite's work trail: while Journeyman works, ONE live line that replaces itself
## (the current call with the clock, the newest thought faint under it), then a test run on that
## line (the test it checks now, how far, a failure by name), then folded into "Completed in …",
## then opened to the whole record (an edit's +/− lines, a render that landed, the test results
## grouped by folder).
## [param suite] is visual_sidebar_test.gd, whose `_build`, `_shot` and `_thumbnail` these use;
## the screens are taken on a sidebar of their own, with [param beside]'s hidden meanwhile.


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
	for event: Dictionary in [
		{
			"text": "The door's size lives in door.blend, so I'll widen it in Blender first.",
			"note": "thought",
			"state": "done",
		},
		{"id": "a", "text": "Reading level.gd"},
		{"id": "a", "text": "Reading level.gd", "state": "done"},
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
