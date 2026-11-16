extends RefCounted
## The visual suite's work trail: Journeyman's tool calls as short lines while it works (newest
## first, one live line, an edit's +/− lines, a render that landed, a thought between steps,
## Joinery's progress under the running call), then folded into "Completed in …", then opened.
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
		{
			"text": "Now I'll hand the change over for its checks.",
			"note": "thought",
			"state": "done"
		},
		{"id": "e", "text": "Handing the change over"},
	]:
		event["kind"] = "trail"
		chat.call("add_trail", event)
	chat.set_chat_state("working", "Running scene tests: 3 of 8")
	card.call("set_elapsed", 23)
	await suite._shot("trail", light, width, chat)

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
