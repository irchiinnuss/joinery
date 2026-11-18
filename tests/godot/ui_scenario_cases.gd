extends RefCounted
## The UI scenarios themselves, one function each (`_<name>` for scenario <name>): what the
## user does, step by step, and what must hold after each step. [member s] is the runner
## (ui_scenarios.gd) that builds the sidebar, drives it and records the checks; the scripted
## provider's turns, by request, are [constant SCRIPT].

const RUNNER := preload("ui_scenarios.gd")
## Longer checks of one scenario's screen, kept apart so this file stays readable.
const CHECKS := preload("ui_scenario_checks.gd")
## A reply long enough to scroll several screens: headings, a list, code, bold and a long path.
const LONG_REPLY := (
	"## What the door script does\n\n"
	+ "The **door** opens when the player is near and presses *Use*. Three parts matter:\n\n"
	+ "- `door.gd` listens for the player entering its `Area3D`;\n"
	+ "- the hinge turns 95° over 0.4 s with an ease-out;\n"
	+ "- a creak plays from `res://audio/sfx/door_creak.wav`.\n\n"
	+ "```gdscript\nfunc _on_body_entered(body: Node3D) -> void:\n"
	+ '\tif body.is_in_group("player"):\n\t\t_near = true\n```\n\n'
	+ "The long path it loads: res://art/props/harbour/doors/weathered_oak_double_door_with_"
	+ "brass_hinges_and_rusted_studs_variant_b_final.glb\n\n"
)
const SAY_HELLO := "Say hello"
const EXPLAIN := "Explain the door script"
const WIDEN := "Make the door a little wider"
const THINK := "Think it through before you answer"
const KEEP_GOING := "Keep going until I stop you"
const BREAK := "Try the broken provider"
const QUOTA := "Use up my quota"
const DROP := "Lose the connection halfway"
const SLOW := "Take too long"
const POLICY := "Make the sign texture with a service"
const DENIED := "Paint the sign in Krita"
const PLAN := "Plan the mower shed"
const OTHER := "Name three harbour sounds"
## The scripted provider's turns, by request (scripted_host.py).
const SCRIPT := {
	# An accept applies files before the host answers: long enough to see what shows meanwhile.
	"decision_ms": 1200,
	"models":
	[
		{"id": "scripted-sol", "name": "Scripted Sol", "efforts": ["low", "medium", "high"]},
	],
	"turns":
	{
		SAY_HELLO:
		[
			{"execution": {"name": "Scripted Sol", "effort": "low"}},
			{"wait": 300},
			{
				"stream": "Hello! Select something in the scene and tell me what to change.",
				"chunk": 6,
				"every_ms": 25
			},
		],
		EXPLAIN: [{"execution": {}}, {"stream": "LONG", "chunk": 24, "every_ms": 16}],
		WIDEN:
		[
			{"execution": {}},
			{"trail": {"id": "a", "text": "Opening door.blend in Blender", "state": "running"}},
			{"wait": 300},
			{"trail": {"id": "a", "text": "Opening door.blend in Blender", "state": "done"}},
			{
				"trail":
				{
					"id": "b",
					"text": "Editing door.gd",
					"file": "door.gd",
					"path": "res://door.gd",
					"state": "running"
				}
			},
			{"wait": 300},
			{
				"trail":
				{
					"id": "b",
					"text": "Editing door.gd",
					"file": "door.gd",
					"path": "res://door.gd",
					"state": "done",
					"added": 12,
					"removed": 3
				}
			},
			{"emit": {"kind": "progress", "text": "Exporting the model…"}},
			{"wait": 500},
			{
				"stream": "I widened **Door** by 20% and kept its hinge where it was.",
				"chunk": 12,
				"every_ms": 20
			},
			{
				"emit":
				{
					"kind": "file_preview",
					"transaction_id": "tx-widen",
					"summary": "Widened the door by 20%",
					"diff": "+ width = 1.2\n- width = 1.0\n",
					"changes": [{"path": "door.gd", "change": "modified"}],
					"media": []
				}
			},
			{"emit": {"kind": "turn_state", "state": "in_review", "transaction_id": "tx-widen"}},
		],
		THINK:
		[
			{"execution": {}},
			{"wait": 2500},
			{
				"stream": "Thought about it: the hinge should stay on the left.",
				"chunk": 10,
				"every_ms": 20
			},
		],
		KEEP_GOING:
		[
			{"execution": {}},
			{
				"stream": "Working through the harbour scene, one prop at a time. ",
				"chunk": 8,
				"every_ms": 30
			},
			{"hang": true},
		],
		BREAK:
		[
			{"execution": {}},
			{"raise": "The provider returned an internal error (HTTP 500). Try again shortly."},
		],
		QUOTA: [{"raise": "You've hit your usage limit. Upgrade or try again at 4:00 PM."}],
		DROP:
		[
			{"execution": {}},
			{"stream": "Starting on it now…", "chunk": 6, "every_ms": 20},
			{"wait": 300},
			{"exit": 1},
		],
		SLOW: [{"execution": {}}, {"timeout": "The provider didn't answer within 120 s."}],
		POLICY:
		[
			{"execution": {}},
			{"trail": {"id": "p", "text": "Calling a texture service", "state": "running"}},
			{"wait": 200},
			{"policy_stop": "a generative service was called."},
		],
		DENIED:
		[
			{"execution": {}},
			{"trail": {"id": "k", "text": "Opening sign.kra in Krita", "state": "running"}},
			{"trail": {"id": "k", "text": "Opening sign.kra in Krita", "state": "failed"}},
			{
				"activity":
				{
					"server": "krita",
					"tool": "open_document",
					"allowed": false,
					"reason": "Krita isn't enabled for this project. Turn it on in Project apps.",
					"call_id": "k"
				}
			},
			{
				"stream": "I couldn't open the sign in Krita, so I left it as it is.",
				"chunk": 12,
				"every_ms": 20
			},
		],
		PLAN:
		[
			{"execution": {}},
			{
				"emit":
				{
					"kind": "todo",
					"todo_id": "todo-shed",
					"state": "waiting",
					"steps":
					[
						"Mower body and deck",
						"Grass box on the back",
						"Shed shelf for the tools",
						"Place the mower by the shed door"
					],
					"done_when":
					[{"id": "d1", "text": "The player can reach the shed", "kind": "reach"}]
				}
			},
			{
				"stream": "Here's the plan. Edit anything, then press Go.",
				"chunk": 12,
				"every_ms": 20
			},
		],
		OTHER:
		[
			{"stream": "Gulls, rope creaks and a bell buoy.", "chunk": 6, "every_ms": 60},
		],
		"*": [{"stream": "Done.", "chunk": 5, "every_ms": 20}],
	},
}
## The runner that drives the sidebar and records the checks.
var s: RUNNER


## Connected, nothing asked yet: the start screen, the message box ready.
func _empty() -> void:
	if not await s.connect_host():
		return
	var text := s.visible_text()
	s.check(s.composer().get("send").disabled, "Send rests while the message box is empty")
	s.check(not s.prompt().placeholder_text.is_empty(), "The message box says what it's for")
	s.check(not text.contains("Connecting"), "Connected: nothing says Connecting")
	await s.shot("ready")


## No agent set up: the sidebar says how to connect one, and the composer rests.
func _first_run() -> void:
	s.dock.call("set_chat_agent", "")
	s.dock.call("set_chat_state", "error", "Journeyman isn't connected.")
	await s.frames(3)
	var text := s.visible_text()
	s.check(text.contains("Connect"), "First run offers Connect: " + text.left(160))
	s.check(not s.prompt().editable, "The message box rests until an agent is connected")
	await s.shot("no-agent")


## A short streamed reply: optimistic card at once, then the words, then done.
func _stream_short() -> void:
	if not await s.connect_host():
		return
	var timing := {}
	s.chat.connect("timing_received", func(event: Dictionary) -> void: timing.merge(event, true))
	var started := s.send(SAY_HELLO)
	await s.frames(1)
	var shown_after := (Time.get_ticks_usec() - started) / 1000.0
	s.check(
		s.card() != null and s.card().is_visible_in_tree(), "The request shows on the next frame"
	)
	s.metric("send_to_card", shown_after, "ms")
	s.check(s.prompt().text.is_empty(), "The message box clears on send")
	s.check(s.prompt().has_focus(), "The message box keeps the focus after send")
	s.check(s.composer().get("stop").visible, "Stop takes Send's place at once")
	await s.shot("sending")
	var first := await s.wait_until(func() -> bool: return not s.reply_shown().is_empty(), 8000)
	var first_ms := (Time.get_ticks_usec() - started) / 1000.0
	s.check(first, "The reply starts to show")
	s.metric("send_to_first_words", first_ms, "ms")
	await s.frames(2)
	var live: PackedStringArray = s.card().call("visible_steps")
	s.check(
		not "Thinking through your request…" in live,
		"Once words arrive, the live line no longer says it thinks: " + ", ".join(live)
	)
	await s.shot("streaming")
	await s.wait_until(func() -> bool: return not timing.is_empty(), 8000)
	await s.frames(4)
	if timing.has("first_response_ms") and timing["first_response_ms"] != null:
		s.metric("host_first_response", float(timing["first_response_ms"]), "ms")
	s.check(
		s.reply_shown().begins_with("Hello! Select something"),
		"The whole reply: " + s.reply_shown()
	)
	s.check(s.card_state() == "done", "The request ends done: " + s.card_state())
	var said := s.visible_text()
	s.check(not said.contains("0:00"), "A short turn's time is in words, never 0:00")
	s.check(said.contains("Completed in under 1 s"), "It says it took under a second")
	s.check(not s.composer().get("stop").visible, "Send is back when the reply ends")
	s.check(s.prompt().has_focus(), "The message box still has the focus at the end")
	await s.shot("done")


## A long reply with Markdown, code and a long path: frame times, wrapping, following.
func _stream_long() -> void:
	if not await s.connect_host():
		return
	s.send(EXPLAIN)
	await s.wait_until(func() -> bool: return not s.reply_shown().is_empty(), 8000)
	var worst := 0.0
	var slow := 0
	var count := 0
	var last := Time.get_ticks_usec()
	var scroll := s.transcript() as ScrollContainer
	var behind := 0
	while s.card_state() == "working" and count < 3000:
		await s.tree.process_frame
		var now := Time.get_ticks_usec()
		var frame := (now - last) / 1000.0
		last = now
		count += 1
		worst = maxf(worst, frame)
		if frame > 50.0:
			slow += 1
		var bar := scroll.get_v_scroll_bar()
		if count % 10 == 0 and bar.max_value - bar.page - scroll.scroll_vertical > 80:
			behind += 1
	s.metric("stream_frames", count, "frames")
	s.metric("stream_worst_frame", worst, "ms")
	s.metric("stream_frames_over_50ms", slow, "frames")
	await s.frames(6)
	var shown := s.reply_shown()
	s.check(
		shown.count("variant_b_final.glb") == 4 and shown.strip_edges().ends_with("final.glb"),
		"The long reply arrives whole (%d chars)" % shown.length()
	)
	s.check(behind <= 2, "The view follows the reply while it streams (%d checks behind)" % behind)
	var bar := scroll.get_v_scroll_bar()
	s.check(
		bar.max_value - bar.page - scroll.scroll_vertical <= 8, "It ends scrolled to the newest"
	)
	s.check(shown.contains("func _on_body_entered"), "The code block shows its code")
	s.check(not shown.contains("```"), "Code fences don't show")
	s.check(not shown.contains("## What"), "Headings render as headings")
	s.check(not shown.contains("*Use*"), "Italics render")
	s.check(not shown.contains("- `door.gd`"), "List items show without their dash")
	s.check(not shown.contains("\n\n\n"), "No double blank gaps")
	await CHECKS.reply_parts(s, scroll)
	scroll.scroll_vertical = 0
	await s.shot("top")
	scroll.scroll_vertical = int(bar.max_value)
	await s.shot("end")


## Scrolled up while the reply streams: the view stays, a pill offers the newest.
func _scroll_away() -> void:
	if not await s.connect_host():
		return
	s.send(EXPLAIN)
	var scroll := s.transcript() as ScrollContainer
	var bar := scroll.get_v_scroll_bar()
	await s.wait_until(func() -> bool: return bar.max_value > bar.page * 2.0, 10000)
	scroll.scroll_vertical = 0
	await s.frames(2)
	var follower: Control = scroll.find_child("NewBelow", true, false)
	await s.wait_ms(700)
	s.check(scroll.scroll_vertical < 40, "Scrolled up, it stays put (%d)" % scroll.scroll_vertical)
	s.check(follower != null and follower.visible, "A 'New' pill offers the newest")
	await s.shot("pill")
	if follower != null:
		await s.click(follower.get_child(0) as Control)
		await s.frames(4)
		s.check(bool(follower.get("following")), "The pill follows again")
		await s.wait_ms(300)
		s.check(
			bar.max_value - bar.page - scroll.scroll_vertical <= 80,
			"After the pill the view is at the newest"
		)
	await s.wait_until(func() -> bool: return s.card_state() != "working", 15000)


## Tool steps while it works, then a change ready for review.
func _tool_steps() -> void:
	if not await s.connect_host():
		return
	s.send(WIDEN)
	var stepped := await s.wait_until(
		func() -> bool: return s.visible_text().contains("Opening door.blend"), 6000
	)
	s.check(stepped, "The first tool step shows while it runs")
	await s.shot("step")
	await s.wait_until(func() -> bool: return s.visible_text().contains("Editing door.gd"), 6000)
	await s.wait_ms(150)
	await s.shot("edit")
	var ready := await s.wait_until(
		func() -> bool: return bool(s.dock.get("_review_pending")), 8000
	)
	s.check(ready, "The change waits for review")
	await s.frames(4)
	var text := s.visible_text()
	s.check(text.contains("door.gd") or text.contains("Door"), "The card names what changed")
	await s.shot("ready")


## A provider that thinks 2.5 s before its first word: feedback must come at once.
func _thinking() -> void:
	if not await s.connect_host():
		return
	var started := s.send(THINK)
	var feedback := await s.wait_until(
		func() -> bool:
			var found := s.card()
			return (
				found != null and not (found.call("visible_steps") as PackedStringArray).is_empty()
			),
		3000
	)
	s.metric("send_to_status_line", (Time.get_ticks_usec() - started) / 1000.0, "ms")
	s.check(feedback, "Something says it's working before the first word")
	await s.wait_ms(1200)
	var timer: Label = s.card().get("_timer")
	await s.shot("waiting")
	s.check(
		not s.reply_shown().is_empty() or s.card_state() == "working", "Still working, not stuck"
	)
	s.metric("timer_shown_after_1_5s", 1.0 if timer.is_visible_in_tree() else 0.0, "bool")
	s.check(timer.is_visible_in_tree(), "The elapsed time shows after about a second")
	await s.wait_until(func() -> bool: return s.card_state() == "done", 8000)


## Stop mid-stream: answers at once, keeps what was said, ends as Stopped.
func _stop() -> void:
	if not await s.connect_host():
		return
	s.send(KEEP_GOING)
	await s.wait_until(func() -> bool: return s.reply_shown().length() > 20, 8000)
	var stop: Button = s.composer().get("stop")
	s.check(stop.is_visible_in_tree(), "Stop shows while it streams")
	var pressed := Time.get_ticks_usec()
	await s.click(stop)
	s.check(stop.disabled or not stop.visible, "Stop answers on the same frame")
	await s.shot("stopping")
	var ended := await s.wait_until(func() -> bool: return s.card_state() != "working", 6000)
	s.metric("stop_to_idle", (Time.get_ticks_usec() - pressed) / 1000.0, "ms")
	s.check(ended, "The turn ends after Stop (%s)" % s.card_state())
	await s.frames(4)
	s.check(s.reply_shown().begins_with("Working through"), "What it said stays after Stop")
	s.check(s.visible_text().contains("Stopped"), "The card says Stopped")
	s.check(s.card_state() == "stopped", "The request ends stopped (%s)" % s.card_state())
	await s.wait_until(func() -> bool: return not _mark(s.card()).is_empty(), 3000)
	s.check(_mark(s.card()) == "stop", "Its mark is the stop, not a tick (%s)" % _mark(s.card()))
	s.check(s.composer().get("send").is_visible_in_tree(), "Send is back after Stop")
	await s.shot("stopped")


## Enter sends, Shift+Enter adds a line, Esc stops, Tab leaves the message box.
func _keyboard() -> void:
	if not await s.connect_host():
		return
	var field := s.prompt()
	field.grab_focus()
	field.text = "First line"
	field.set_caret_column(field.text.length())
	s.key(KEY_ENTER, true)
	await s.frames(2)
	s.check(field.text.contains("\n"), "Shift+Enter adds a line")
	s.check(s.card() == null, "Shift+Enter doesn't send")
	field.text = ""
	s.send(KEEP_GOING)
	await s.wait_until(func() -> bool: return s.reply_shown().length() > 10, 8000)
	s.check(field.has_focus(), "Focus stays in the message box while it streams")
	s.key(KEY_ESCAPE)
	await s.wait_ms(600)
	s.check(
		s.card_state() != "working" or bool(s.composer().get("stop").disabled),
		"Esc stops the reply (%s)" % s.card_state()
	)
	s.check(s.visible_text().contains("Stopped"), "After Esc the card says Stopped")
	# Tab from the message box moves on (a TextEdit can keep Tab for itself).
	field.grab_focus()
	s.key(KEY_TAB)
	await s.frames(2)
	var owner := s.tree.root.gui_get_focus_owner()
	s.check(owner != field, "Tab leaves the message box (focus: %s)" % _focus_name(owner))
	s.check(not field.text.contains("\t"), "Tab types no tab character")
	await s.shot("tab")
	field.grab_focus()
	s.key(KEY_TAB, true)
	await s.frames(2)
	owner = s.tree.root.gui_get_focus_owner()
	s.check(owner != field, "Shift+Tab leaves the message box too (focus: %s)" % _focus_name(owner))
	field.grab_focus()
	(s.composer().get("stop") as Button).pressed.emit()
	await s.wait_until(func() -> bool: return s.card_state() != "working", 6000)


## The icon leading a finished card's status line ("success", "stop", "error"), "" when none.
func _mark(card: Control) -> String:
	var mark: TextureRect = card.get("_done_mark") if card != null else null
	if mark == null or not mark.is_visible_in_tree():
		return ""
	return str(mark.get_meta("_joinery_glyph", ""))


func _focus_name(control: Control) -> String:
	return "none" if control == null else s.control_name(control)


## The provider fails: one clear line, the request can be retried.
func _provider_error() -> void:
	if not await s.connect_host():
		return
	s.send(BREAK)
	var said := await s.wait_until(
		func() -> bool: return s.visible_text().contains("HTTP 500"), 6000
	)
	s.check(said, "The provider's error is shown")
	await s.wait_until(func() -> bool: return s.card_state() != "working", 4000)
	await s.frames(4)
	s.check(s.card_state() == "failed", "The request ends failed, not done (%s)" % s.card_state())
	# The mark leads the timing line, which comes just after the turn ends.
	await s.wait_until(func() -> bool: return not _mark(s.card()).is_empty(), 3000)
	s.check(_mark(s.card()) == "error", "Its mark is the error, not a tick (%s)" % _mark(s.card()))
	s.check(s.dock.get("connection_state") == "connected", "Still connected after one failed reply")
	s.check(
		not s.notifications.is_empty(),
		"In the background, a failed request tells the desktop (%s)" % " / ".join(s.notifications)
	)
	s.check(not s.reply_shown().contains("HTTP 500"), "The error isn't shown as a reply")
	s.check(s.visible_text().count("HTTP 500") == 1, "The error is said once")
	var retry: Button = s.transcript().find_child("Retry", true, false)
	s.check(retry != null and s.on_screen(retry), "The error offers Retry")
	await s.shot("failed")
	if retry != null:
		await s.click(retry)
		var again := await s.wait_until(
			func() -> bool: return (s.transcript().get("_cards") as Array).size() == 2, 4000
		)
		s.check(again, "Retry asks again, as a new request")
		await s.wait_until(func() -> bool: return s.card_state() != "working", 4000)


## A usage limit: say which, and offer to carry on with another agent.
func _rate_limit() -> void:
	if not await s.connect_host():
		return
	s.send(QUOTA)
	# With no other agent signed in (the isolated run), it says how to get one.
	var offered := await s.wait_until(
		func() -> bool:
			var text := s.visible_text()
			return text.contains("Continue with") or text.contains("Sign one in"),
		6000
	)
	s.check(
		offered, "A quota stop offers another agent, or says how: " + s.visible_text().right(200)
	)
	s.check(s.visible_text().contains("usage limit"), "It says it was the usage limit")
	var said := s.visible_text().count("You've hit your usage limit")
	s.check(said == 1, "The usage limit is said once (%d times)" % said)
	s.check(s.reply_shown().is_empty(), "The limit isn't shown as a reply")
	await s.shot("offer")


## The host dies mid-reply: red dot, Reconnect, the partial reply stays.
func _network_drop() -> void:
	if not await s.connect_host():
		return
	s.send(DROP)
	var lost := await s.wait_until(
		func() -> bool: return s.dock.get("connection_state") == "error", 8000
	)
	s.check(lost, "The sidebar notices the lost connection")
	await s.frames(4)
	var text := s.visible_text()
	s.check(text.contains("Reconnect"), "It offers Reconnect: " + text.left(200))
	s.check(s.reply_shown().begins_with("Starting"), "The words that came stay")
	s.check(s.card_state() == "failed", "The cut-off request ends failed (%s)" % s.card_state())
	s.check(not s.composer().get("stop").visible, "Stop goes when nothing can be stopped")
	await s.shot("lost")


## The provider times out.
func _timeout() -> void:
	if not await s.connect_host():
		return
	s.send(SLOW)
	var said := await s.wait_until(func() -> bool: return s.visible_text().contains("120 s"), 6000)
	s.check(said, "The timeout is shown in words")
	await s.wait_until(func() -> bool: return s.card_state() != "working", 4000)
	await s.shot("failed")


## A Rule Zero stop: the turn ends and the sidebar says it was the project's policy.
func _rule_zero_stop() -> void:
	if not await s.connect_host():
		return
	s.send(POLICY)
	var said := await s.wait_until(
		func() -> bool: return s.visible_text().contains("Stopped by project policy"), 6000
	)
	s.check(said, "The policy stop is shown in words")
	await s.wait_until(func() -> bool: return s.card_state() != "working", 4000)
	await s.frames(4)
	s.check(s.card_state() != "working", "The turn has ended (%s)" % s.card_state())
	s.check(not s.visible_text().contains("Retry"), "No Retry offered around a policy stop")
	await CHECKS.policy_stop(s)


## One call is refused (an app not enabled): the reason shows and the turn goes on.
func _denied_call() -> void:
	if not await s.connect_host():
		return
	s.send(DENIED)
	await s.wait_until(func() -> bool: return s.card_state() == "done", 8000)
	await s.frames(4)
	var text := s.visible_text()
	s.check(s.reply_shown().contains("couldn't open"), "The turn goes on after the refusal")
	var badge: Control = s.card().find_child("Krita", true, false)
	s.check(badge == null or not badge.is_visible_in_tree(), "No Krita badge: Krita did nothing")
	s.check(text.contains("isn't enabled for this project"), "The refusal's reason is shown")
	s.check(text.contains("Not run: Krita"), "It says the step was not run: " + text.left(240))
	var refused: Control = null
	for row: Node in s.card().find_child("History", true, false).get_children():
		if str(row.get_meta("trail_id", "")) == "k":
			refused = row as Control
	var end := str(refused.get_meta("end", "")) if refused != null else "none"
	var words := str((refused.get_node("Line/Words") as Label).text) if refused != null else ""
	s.check(end == "stopped", "The refused step ends with the stop mark (%s)" % end)
	s.check(not words.begins_with("Opened"), "The refused step never reads as done: " + words)
	await s.shot("done")


## A change ready for review, then Accept.
func _review_accept() -> void:
	await _review(true)


func _review_reject() -> void:
	await _review(false)


func _review(accept: bool) -> void:
	if not await s.connect_host():
		return
	s.send(WIDEN)
	var ready := await s.wait_until(
		func() -> bool: return bool(s.dock.get("_review_pending")), 8000
	)
	s.check(ready, "The change waits for review")
	await s.frames(4)
	CHECKS.quiet_actions(s)
	await s.shot("waiting")
	var started := Time.get_ticks_usec()
	if accept:
		# The card's own shortcut, with the message box empty.
		s.prompt().grab_focus()
		s.key(KEY_ENTER, false, true)
		await s.frames(1)
	else:
		var discard: Button = s.transcript().find_child("Discard", true, false)
		s.check(discard != null and s.on_screen(discard), "Discard is under the change")
		if discard != null:
			await s.click(discard)
	s.check(
		s.card().call("awaiting_decision") == false,
		"The %s took the decision" % ("⌘↩ shortcut" if accept else "Discard button")
	)
	var word := "accepted" if accept else "Discarded"
	await s.frames(1)
	s.metric("decision_to_line", (Time.get_ticks_usec() - started) / 1000.0, "ms")
	s.check(s.visible_text().contains(word), "The card says %s at once" % word)
	# Until the host confirms, nothing may still ask for the review just decided.
	var footer: Control = s.dock.get("_footer")
	var asks := footer.is_visible_in_tree() and str(footer.call("text")).contains("review")
	s.check(
		not asks,
		"Once decided, nothing says it waits for review (footer: '%s')" % str(footer.call("text"))
	)
	await s.shot("decided")
	# The status line with its Show review link, faded under typed text, takes no clicks.
	footer.call("set_mode", "review")
	await s.frames(2)
	s.prompt().text = "typing over the status line"
	s.prompt().text_changed.emit()
	await s.frames(2)
	var hidden_button := false
	for button: Button in footer.find_children("", "Button", true, false):
		if button.is_visible_in_tree() and button.mouse_filter != Control.MOUSE_FILTER_IGNORE:
			var alpha := footer.modulate.a * button.modulate.a
			hidden_button = hidden_button or alpha < 0.05
	s.check(not hidden_button, "A faded status line takes no clicks over typed text")
	s.prompt().text = ""
	s.prompt().text_changed.emit()
	await s.frames(2)
	var show_review: Button = null
	for button: Button in footer.find_children("", "Button", true, false):
		show_review = button
	s.check(
		show_review != null and show_review.mouse_filter != Control.MOUSE_FILTER_IGNORE,
		"With the message box empty again, Show review takes clicks"
	)
	s.dock.call("_sync")
	var settled := await s.wait_until(
		func() -> bool: return not bool(s.dock.get("_review_pending")), 6000
	)
	s.metric("decision_confirmed", (Time.get_ticks_usec() - started) / 1000.0, "ms")
	s.check(settled, "Nothing waits for review after the host confirms")
	await s.frames(4)
	await s.shot("accepted" if accept else "discarded")


## Messages sent while it works wait in order and go out one at a time.
func _queued() -> void:
	if not await s.connect_host():
		return
	s.send(KEEP_GOING)
	await s.wait_until(func() -> bool: return s.reply_shown().length() > 10, 8000)
	s.send("Then add a brass handle")
	s.send("And darken the wood")
	await s.frames(3)
	var queue: Object = s.transcript().get("queue")
	s.check(int(queue.call("size_queued")) == 2, "Both messages wait in the queue")
	s.check(s.prompt().text.is_empty(), "Queued messages leave the message box")
	s.check(not s.prompt().placeholder_text.is_empty(), "The box says the next message waits")
	await s.shot("two-waiting")
	(s.composer().get("stop") as Button).pressed.emit()
	await s.wait_until(func() -> bool: return s.card_state() != "working", 6000)
	await s.frames(4)
	var paused := bool(queue.get("paused"))
	s.check(paused, "Stop pauses the queue rather than sending the next")
	await s.shot("paused")
	var next: Button = queue.get("send")
	s.check(s.on_screen(next), "Paused, 'Send next' offers the way on")
	await s.click(next)
	var sent := await s.wait_until(func() -> bool: return int(queue.call("size_queued")) < 2, 6000)
	s.check(sent, "Resumed, the next message goes out")


## Two chats at once: a message from a new chat starts at once beside the working one.
func _two_chats() -> void:
	if not await s.connect_host():
		return
	s.send(KEEP_GOING)
	await s.wait_until(func() -> bool: return s.reply_shown().length() > 10, 8000)
	var first_view := s.transcript()
	var sessions: Node = s.dock.get("_chats")
	sessions.call("new_chat")
	await s.frames(3)
	s.check(s.transcript() != first_view, "New chat shows a fresh conversation")
	var started := s.send(OTHER)
	await s.frames(2)
	var queue: Object = s.transcript().get("queue")
	s.check(int(queue.call("size_queued")) == 0, "The other chat's message doesn't wait")
	var answered := await s.wait_until(
		func() -> bool: return s.reply_shown().contains("Gulls"), 6000
	)
	s.metric("other_chat_first_words", (Time.get_ticks_usec() - started) / 1000.0, "ms")
	s.check(answered, "The other chat's reply streams in its own view")
	s.check(
		not s.reply_shown().contains("Working through"),
		"The first chat's words stay out of the second"
	)
	await s.shot("both-working")
	await s.wait_until(func() -> bool: return s.card_state() == "done", 6000)
	# Back to the first chat: still working, its reply live, with Stop.
	var page: Variant = sessions.get("page")
	sessions.call("open_chat", _working_chat(sessions, first_view))
	await s.frames(4)
	s.check(s.transcript() == first_view, "Opening the first chat brings back its live view")
	s.check(s.reply_shown().contains("Working through"), "Its reply is there")
	s.check((s.composer().get("stop") as Button).visible, "Its Stop is there")
	await s.shot("back")
	if page is Object:
		(page as Object).call("set_open", false)
	(s.composer().get("stop") as Button).pressed.emit()
	await s.wait_until(func() -> bool: return s.dock.get("_busy") == false, 6000)


## The kept chat showing [param view]; in the editor, an earlier scenario's chat with messages
## still queued is kept too.
func _working_chat(sessions: Node, view: Control) -> String:
	var kept: Dictionary = sessions.get("_kept")
	for id: String in kept:
		if kept[id] == view:
			return id
	return ""


## New chat while a reply streams, then back: the reply finished in its own chat only.
func _switch_mid_stream() -> void:
	if not await s.connect_host():
		return
	s.send(EXPLAIN)
	await s.wait_until(func() -> bool: return s.reply_shown().length() > 200, 8000)
	var first_view := s.transcript()
	var sessions: Node = s.dock.get("_chats")
	sessions.call("new_chat")
	await s.frames(3)
	s.check(s.reply_shown().is_empty(), "The new chat shows none of the other's reply")
	await s.shot("other-chat")
	await s.wait_until(func() -> bool: return s.dock.get("_busy") == false, 20000)
	s.check(s.reply_shown().is_empty(), "The reply never leaks into the chat on screen")
	sessions.call("open_chat", _working_chat(sessions, first_view))
	await s.frames(4)
	if s.transcript() == first_view:
		s.check(
			s.reply_shown().count("variant_b_final.glb") == 4,
			"The first chat's reply finished whole"
		)
	else:
		s.check(false, "The first chat comes back after its reply ended")
	await s.shot("back")


## Edit and resend: the request goes back to the message box; the turns after it go.
func _rewind() -> void:
	if not await s.connect_host():
		return
	s.send(SAY_HELLO)
	await s.wait_until(func() -> bool: return s.card_state() == "done", 8000)
	s.send("Darken the wood")
	var second := await s.wait_until(func() -> bool: return s.card_state() == "done", 8000)
	s.check(second, "A second request in the same chat finishes (%s)" % s.card_state())
	await s.wait_ms(400)
	var sessions: Node = s.dock.get("_chats")
	var rewinder: Object = sessions.get("rewinder")
	var cards: Array = s.transcript().get("_cards")
	var menu: Variant = rewinder.call("menu_of", cards[0])
	s.check(menu != null, "A sent request has its menu")
	if menu is Control:
		(menu as Control).show()
		s.check(not (menu as Button).tooltip_text.is_empty(), "Its menu button says what it does")
	rewinder.call("choose", rewinder.get("EDIT"), cards[0])
	await s.frames(3)
	s.check(rewinder.get("confirm") != null, "Edit and resend asks first")
	await s.shot("ask")
	var confirm: Node = rewinder.get("confirm")
	if confirm == null:
		return
	var go: Button = null
	for button: Button in confirm.find_children("", "Button", true, false):
		if button.text.begins_with("Rewind"):
			go = button
	s.check(go != null, "The question has a Rewind button")
	if go == null:
		return
	go.pressed.emit()
	var back := await s.wait_until(func() -> bool: return s.prompt().text == SAY_HELLO, 15000)
	s.check(back, "The request is back in the message box: '%s'" % s.prompt().text)
	await s.frames(4)
	s.check((s.transcript().get("_cards") as Array).size() <= 1, "The later turns left the chat")
	await s.shot("rewound")


## A to-do card: the steps and Go.
func _todo() -> void:
	if not await s.connect_host():
		return
	s.send(PLAN)
	var tasks: Node = s.dock.get("_tasks")
	var shown := await s.wait_until(
		func() -> bool:
			var found: Variant = tasks.get("todo_card")
			return found is Control and (found as Control).is_visible_in_tree(),
		6000
	)
	s.check(shown, "The to-do card shows")
	await s.wait_until(func() -> bool: return s.card_state() == "done", 6000)
	await s.frames(4)
	if shown:
		var todo: Control = tasks.get("todo_card")
		var go: Button = todo.get("go")
		s.check(go.is_visible_in_tree(), "Go is there")
		s.check(s.on_screen(go), "Go is on screen without scrolling")
	await s.shot("waiting")


## A playtest card while playing, then its report.
func _playtest() -> void:
	if not await s.connect_host():
		return
	var event := {"kind": "playtest", "playtest_id": "pt", "state": "playing", "seconds": 120}
	event["elapsed"] = 42.0
	s.chat.call("_handle", event)
	var tasks: Node = s.dock.get("_tasks")
	await s.frames(3)
	var found: Variant = tasks.get("playtest")
	s.check(found is Control and (found as Control).is_visible_in_tree(), "The playtest card shows")
	await s.shot("playing")
	var fixture: GDScript = load(
		get_script().resource_path.get_base_dir().path_join("playtest_fixture.gd")
	)
	event["state"] = "done"
	event["report"] = {
		"counts": {"critical": 0, "high": 1, "medium": 1, "low": 1, "note": 1},
		"items": fixture.call("items", ""),
		"seed": 662040.0,
		"played_seconds": 120.0,
		"coverage": fixture.call("coverage"),
		"reach": fixture.call("reach"),
	}
	s.chat.call("_handle", event)
	await s.frames(4)
	s.check(s.visible_text().contains("1 bug"), "The report counts by severity")
	var more: Variant = (tasks.get("playtest") as Control).get("_more")
	s.check(more is Button and s.on_screen(more), "Show all is on screen without scrolling")
	await s.shot("report")


## A new console error: the offer to fix it.
func _console_errors() -> void:
	if not await s.connect_host():
		return
	var error := "Invalid call. Nonexistent function 'jump' in base 'Player'."
	var group := {
		"id": "g1",
		"kind": "error",
		"message": error,
		"count": 3,
		"title": "player.gd:42 — Nonexistent function 'jump'",
		"status": "open",
	}
	s.chat.call("_handle", {"kind": "errors", "mode": "ask", "groups": [group], "offer": group})
	await s.frames(4)
	var text := s.visible_text()
	s.check(text.contains("jump"), "The error shows in the sidebar")
	s.check(text.contains("Fix"), "It offers to fix it: " + text.right(160))
	s.check(text.count("jump") == 1, "One place to act on it, not two (%d)" % text.count("jump"))
	s.check(not text.contains("Debug"), "One verb for it: Fix")
	await s.shot("offer")


## Errors fixed on their own: the list with a fix at work.
func _auto_fix() -> void:
	if not await s.connect_host():
		return
	var group := {
		"id": "g1",
		"kind": "error",
		"message": 'Node not found: "Score"',
		"count": 1,
		"title": 'hud.gd:7 — Node not found: "Score"',
		"status": "fixing",
		"task_id": "c1",
	}
	var fix := {"kind": "task", "task_id": "c1", "console": true, "state": "working"}
	fix.merge({"step": "Reading the error…", "elapsed_s": 21})
	s.chat.call("_handle", fix)
	s.chat.call("_handle", {"kind": "errors", "mode": "auto", "groups": [group]})
	await s.frames(4)
	var tasks: Node = s.dock.get("_tasks")
	var errors: Variant = tasks.get("errors_card")
	s.check(errors is Control and (errors as Control).is_visible_in_tree(), "The errors card shows")
	s.check(s.visible_text().contains("Score"), "The error being fixed is named")
	await s.shot("fixing")
	s.chat.call("_handle", fix.merged({"state": "done"}, true))
	group["status"] = "fixed"
	s.chat.call("_handle", {"kind": "errors", "mode": "auto", "groups": [group]})
	await s.frames(4)
	if errors is Control:
		var headline := str((errors as Control).get("headline").text)
		s.check(headline == "Fixed 1 error", "The headline says what happened: " + headline)
	await s.shot("fixed")


## Idle after a finished reply: how often the sidebar still redraws or processes.
func _idle() -> void:
	if not await s.connect_host():
		return
	s.send(SAY_HELLO)
	await s.wait_until(func() -> bool: return s.card_state() == "done", 8000)
	await s.wait_ms(1500)
	if not Engine.is_editor_hint():
		return  # A script run draws every frame; only the editor's idle means anything.
	# In the editor (low-processor mode) a frame is drawn only when something asks for one:
	# frames drawn while nothing happens are idle cost. Then the same without the message
	# box's focus, and with the sidebar hidden, to see who asks.
	var drawn := await _redraws("idle_frames_drawn_per_s")
	s.tree.root.gui_release_focus()
	await _redraws("idle_no_focus_frames_per_s")
	s.dock.visible = false
	var hidden := await _redraws("idle_sidebar_hidden_frames_per_s")
	s.dock.visible = true
	if OS.is_in_low_processor_usage_mode():
		s.check(
			drawn - hidden < 5.0,
			(
				"Idle, the sidebar asks for no redraws (%.1f frames/s with it, %.1f without)"
				% [drawn, hidden]
			)
		)
		# Nor does the rest of Joinery: a loading pulse on a hidden page once kept the whole
		# editor drawing about 100 frames a second.
		s.check(hidden < 5.0, "Idle, the editor draws nothing (%.1f frames/s)" % hidden)


## Frames drawn per second over two quiet seconds, recorded as [param name].
func _redraws(name: String) -> float:
	await s.wait_ms(300)
	var before := Engine.get_frames_drawn()
	var started := Time.get_ticks_msec()
	await s.wait_ms(2000)
	var rate := (Engine.get_frames_drawn() - before) / ((Time.get_ticks_msec() - started) / 1000.0)
	s.metric(name, rate, "fps")
	return rate


## The script for scripted_host.py, with the long reply filled in.
func script_data() -> Dictionary:
	var script: Dictionary = SCRIPT.duplicate(true)
	script["turns"][EXPLAIN][1]["stream"] = LONG_REPLY.repeat(4)
	return script
