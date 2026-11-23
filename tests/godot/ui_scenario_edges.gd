extends RefCounted
## Edge cases of the sidebar, one function each (`_<name>` for scenario <name>), beside the
## everyday ones in ui_scenario_cases.gd: rapid input, odd and very long content, races between
## chats and reviews, a host that misbehaves, the editor around the sidebar changing, and a saved
## chat reopened. [member s] is the runner (ui_scenarios.gd); [constant SCRIPT]'s turns join the
## scripted provider's.

const RUNNER := preload("ui_scenarios.gd")
const KEEP_GOING := "Keep going until I stop you"
## The words the scripted reply to [constant KEEP_GOING] streams, whole.
const KEEP_GOING_WORDS := [
	"Working", "through", "the", "harbour", "scene", "one", "prop", "at", "a"
]
const SAY_HELLO := "Say hello"
const WIDEN := "Make the door a little wider"
const POLICY := "Make the sign texture with a service"
const DENIED := "Paint the sign in Krita"
const ODD := "Show me odd text"
const HUGE := "Write a very long reply"
const GARBAGE := "Answer through a noisy pipe"
const FAIL_SLOWLY := "Fail after a while"
const TOGETHER := "Finish at the same time"
const LIVE := "Stream while the editor changes"
const OTHER := "Name three harbour sounds"
## A to-do whose turn goes on talking after the card comes in.
const PLAN_SLOWLY := "Plan the tool shed and talk me through it"
const BUSY_STATUS := "Model the sign in Blender, then paint it in Krita"
## Emoji, CJK, right-to-left and combining text in one reply.
const SCRIPTS := "Lanterns 🏮🚢 — 港の灯台 · 등대 · منارة الميناء · מגדלור · é ok"
## Markdown that never closes, and stars inside code.
const MALFORMED := "Use `a * b * c` and **bold that never ends\n\n```gdscript\nvar x := 2 * 3\n"
## A path long enough to need wrapping in any sidebar.
const LONG_PATH := (
	"res://art/props/harbour/doors/weathered_oak_double_door_with_brass_hinges_and_rusted_"
	+ "studs_and_a_very_long_name_that_keeps_going_variant_b_final_final.glb"
)
## Turns these scenarios add to the scripted provider's (scripted_host.py).
const SCRIPT := {
	ODD:
	[
		{"execution": {}},
		{"trail": {"id": "o", "text": "Opening " + LONG_PATH, "file": LONG_PATH, "state": "done"}},
		{"stream": "ODD", "chunk": 7, "every_ms": 4},
	],
	HUGE: [{"execution": {}}, {"stream": "HUGE", "chunk": 1500, "every_ms": 8}],
	GARBAGE:
	[
		{"execution": {}},
		{"raw": "this is not json\n"},
		{"raw": '{"kind": "message_delta", "text": "half a li'},
		{"raw": "{broken\n"},
		{"raw_split": {"kind": "message_delta", "text": "灯台は、", "new_message": true}},
		{"stream": "still here.", "chunk": 4, "every_ms": 10, "new": false},
	],
	FAIL_SLOWLY:
	[
		{"execution": {}},
		{"stream": "Starting the hinge…", "chunk": 4, "every_ms": 30},
		{"wait": 500},
		{"raise": "The provider returned an internal error (HTTP 502)."},
	],
	TOGETHER: [{"wait": 600}, {"stream": "Together.", "chunk": 9, "every_ms": 1}],
	BUSY_STATUS:
	[
		{"execution": {}},
		{"trail": {"id": "b", "text": "Opening sign.blend in Blender", "state": "running"}},
		{"trail": {"id": "b", "text": "Opening sign.blend in Blender", "state": "done"}},
		{"trail": {"id": "k", "text": "Opening sign.kra in Krita", "state": "running"}},
		{"trail": {"id": "k", "text": "Opening sign.kra in Krita", "state": "failed"}},
		{
			"activity":
			{
				"server": "krita",
				"tool": "open_document",
				"allowed": false,
				"reason": "Krita isn't enabled for this project.",
				"call_id": "k"
			}
		},
		{"stream": "The sign is modelled; painting it needs Krita.", "chunk": 12, "every_ms": 20},
	],
	LIVE:
	[
		{"execution": {}},
		{"stream": "The harbour lamps flicker as the tide comes in. ", "chunk": 6, "every_ms": 25},
		{"stream": "Each lamp gets its own light.", "chunk": 6, "every_ms": 25, "new": false},
	],
	PLAN_SLOWLY:
	[
		{"execution": {}},
		{
			"emit":
			{
				"kind": "todo",
				"todo_id": "todo-slow",
				"state": "waiting",
				"steps": ["Shed walls and roof", "Shelf for the tools"],
				"done_when":
				[{"id": "d1", "text": "The player can reach the shed", "kind": "reach"}]
			}
		},
		{
			"stream": "Here's the plan: walls and a roof first, then a shelf. Edit it, then Go.",
			"chunk": 3,
			"every_ms": 60
		},
	],
}

## The runner that drives the sidebar and records the checks.
var s: RUNNER


## The odd reply, filled in: scripts, one 5,000-character word, broken Markdown.
static func odd_reply() -> String:
	return SCRIPTS + "\n\n" + "x".repeat(5000) + "\n\n" + MALFORMED


## About 50 KB of reply, in paragraphs as replies come.
static func huge_reply() -> String:
	var paragraph := "The tide comes in over the harbour wall and the lamps hold steady. ".repeat(6)
	return (paragraph.strip_edges() + "\n\n").repeat(50 * 1024 / (paragraph.length() + 2) + 1)


## Send twice with one click each, Send then Stop at once, Stop twice, and Esc while the command
## menu is open: one request each time, and the sidebar is ready again after.
func _rapid_input() -> void:
	if not await s.connect_host():
		return
	var field := s.prompt()
	var send: Button = s.composer().get("send")
	field.grab_focus()
	field.text = SAY_HELLO
	field.text_changed.emit()
	await s.frames(1)
	s.click(send)
	await s.click(send)
	var cards: Array = s.transcript().get("_cards")
	var queue: Object = s.transcript().get("queue")
	s.check(cards.size() == 1, "A double click on Send sends once (%d cards)" % cards.size())
	s.check(int(queue.call("size_queued")) == 0, "…and queues nothing")
	await s.wait_until(func() -> bool: return s.card_state() == "done", 8000)
	# Send, then Stop on the very next frame.
	s.send(KEEP_GOING)
	await s.frames(1)
	(s.composer().get("stop") as Button).pressed.emit()
	var ended := await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 6000)
	s.check(ended, "Stop right after Send ends the request (%s)" % s.card_state())
	await s.frames(4)
	s.check(s.card_state() in ["stopped", "failed"], "…as stopped (%s)" % s.card_state())
	s.check(send.is_visible_in_tree(), "Send is back after an early Stop")
	s.send(SAY_HELLO)
	var again := await s.wait_until(func() -> bool: return s.card_state() == "done", 8000)
	s.check(again, "The next request after an early Stop runs (%s)" % s.card_state())
	# Stop twice.
	s.send(KEEP_GOING)
	await s.wait_until(func() -> bool: return _turn_text().contains("Working through"), 8000)
	var stop: Button = s.composer().get("stop")
	await s.click(stop)
	await s.click(stop)
	await s.wait_until(func() -> bool: return s.card_state() != "working", 6000)
	await s.frames(4)
	s.check(s.card_state() == "stopped", "Stop twice ends it stopped (%s)" % s.card_state())
	var said := _turn_text().count("Stopped")
	s.check(said == 1, "Stop twice says Stopped once (%d): %s" % [said, _turn_text()])
	# Esc while the command menu is open closes the menu only (sent once the last turn is over).
	await s.wait_until(func() -> bool: return not bool(s.dock.call("_occupied")), 4000)
	s.send(KEEP_GOING)
	await s.wait_until(func() -> bool: return _turn_text().contains("Working through"), 8000)
	field.grab_focus()
	field.text = "/"
	field.set_caret_column(1)
	field.text_changed.emit()
	await s.frames(2)
	var menu: Control = s.dock.get("_command_popup")
	s.check(menu.visible, "Typing / opens the command menu while it works")
	s.key(KEY_ESCAPE)
	await s.frames(2)
	s.check(not menu.visible, "Esc closes the menu")
	s.check(s.card_state() == "working", "…and doesn't stop the reply (%s)" % s.card_state())
	field.text = ""
	field.text_changed.emit()
	s.key(KEY_ESCAPE)
	await s.wait_until(func() -> bool: return s.card_state() != "working", 6000)
	s.check(s.card_state() == "stopped", "A second Esc stops it (%s)" % s.card_state())
	await s.shot("after")


## Emoji, CJK, right-to-left text, a 5,000-character word, broken Markdown and a step with a
## very long path: all of it shows, inside the column.
func _odd_content() -> void:
	if not await s.connect_host():
		return
	s.send(ODD)
	var done := await s.wait_until(func() -> bool: return s.card_state() == "done", 20000)
	s.check(done, "The odd reply finishes (%s)" % s.card_state())
	await s.frames(6)
	var shown := s.reply_shown()
	s.check(shown.contains("港の灯台") and shown.contains("منارة"), "CJK and Arabic show as sent")
	s.check(shown.contains("🏮"), "Emoji show")
	s.check(shown.contains("x".repeat(5000)), "The 5,000-character word arrives whole")
	s.check(shown.contains("a * b * c"), "Stars inside code stay as typed")
	s.check(shown.contains("var x := 2 * 3"), "An unclosed code block still shows its code")
	_inside_column("odd")
	await s.shot("odd")


## About 50 KB of reply in big pieces: it arrives whole, frames stay short, the view follows.
func _huge_reply() -> void:
	if not await s.connect_host():
		return
	s.send(HUGE)
	var worst := 0.0
	var last := Time.get_ticks_usec()
	var count := 0
	while s.card_state() != "done" and count < 4000:
		await s.tree.process_frame
		var now := Time.get_ticks_usec()
		worst = maxf(worst, (now - last) / 1000.0)
		last = now
		count += 1
	s.metric("huge_worst_frame", worst, "ms")
	await s.frames(6)
	# Paragraph breaks show as breaks, not as the blank lines typed: compare the words.
	var expected := huge_reply().replace("\n", "")
	var shown := s.reply_shown().replace("\n", "")
	s.check(
		shown.length() == expected.length(),
		"The 50 KB reply arrives whole (%d of %d chars)" % [shown.length(), expected.length()]
	)
	s.check(worst < 500.0, "No frame takes half a second while it streams (%.0f ms)" % worst)
	var scroll := s.transcript() as ScrollContainer
	var bar := scroll.get_v_scroll_bar()
	s.check(bar.max_value - bar.page - scroll.scroll_vertical <= 8, "It ends at the newest")
	_inside_column("huge")


## A 300-message chat (100 requests; 2,000 can outrun a scenario's time limit on a busy
## machine): it opens with its newest requests in under a second, builds the older ones as
## the user scrolls up, keeps them in order, and leaves nothing behind when a new chat
## replaces it.
func _long_chat() -> void:
	if not await s.connect_host():
		return
	await s.frames(4)
	var nodes_before := Performance.get_monitor(Performance.OBJECT_NODE_COUNT)
	var entries: Array = []
	for index: int in 100:
		entries.append({"type": "user", "text": "Request %d: move lamp %d" % [index, index]})
		entries.append({"type": "agent", "text": "Moved **lamp %d** to the pier." % index})
		entries.append({"type": "turn", "outcome": "done", "total_ms": 1200})
	var sessions: Node = s.dock.get("_chats")
	var started := Time.get_ticks_msec()
	sessions.call("render", entries)
	await s.frames(2)
	var took := Time.get_ticks_msec() - started
	s.metric("long_chat_open", took, "ms")
	s.check(took < 1000, "300 messages open in under 1 s (%d ms)" % took)
	var scroll := s.transcript() as ScrollContainer
	var bar := scroll.get_v_scroll_bar()
	await s.frames(4)
	s.check(bar.max_value - bar.page - scroll.scroll_vertical <= 8, "It opens at the newest")
	var cards: Array = s.transcript().get("_cards")
	s.check(
		str(cards[-1].get("request")) == "Request 99: move lamp 99", "The newest request is last"
	)
	# Scrolling up builds the older requests above, a frame's worth at a time, and the view
	# stays where the user reads.
	var worst := 0.0
	var steps := 0
	while int(s.transcript().call("older_count")) > 0 and steps < 600:
		var last := Time.get_ticks_usec()
		scroll.scroll_vertical = 0
		await s.tree.process_frame
		worst = maxf(worst, (Time.get_ticks_usec() - last) / 1000.0)
		steps += 1
	await s.frames(2)
	s.metric("long_chat_older_frame", worst, "ms")
	s.check(worst < 250.0, "Building older requests keeps frames short (%.0f ms)" % worst)
	s.check(
		bar.max_value - bar.page - scroll.scroll_vertical > bar.page,
		"Older requests going in above don't pull the view to the end"
	)
	cards = s.transcript().get("_cards")
	s.check(cards.size() == 100, "Every request is there once scrolled up (%d)" % cards.size())
	var ordered := true
	for index: int in cards.size():
		if str(cards[index].get("request")) != "Request %d: move lamp %d" % [index, index]:
			ordered = false
	s.check(ordered, "…in the order they were sent")
	await s.shot("top")
	worst = 0.0
	for step: int in 20:
		var last := Time.get_ticks_usec()
		scroll.scroll_vertical = int(bar.max_value * step / 19.0)
		await s.tree.process_frame
		worst = maxf(worst, (Time.get_ticks_usec() - last) / 1000.0)
	s.metric("long_chat_scroll_frame", worst, "ms")
	sessions.call("new_chat")
	for index: int in 10:
		await s.frames(2)
	var nodes_after := Performance.get_monitor(Performance.OBJECT_NODE_COUNT)
	s.metric("long_chat_nodes_left", nodes_after - nodes_before, "nodes")
	s.check(
		nodes_after - nodes_before < 200,
		"A new chat frees the long one (%d nodes more than before)" % (nodes_after - nodes_before)
	)


## A host that writes a line that is not JSON, splits a line across reads (in the middle of a
## character) and goes on: the reply shows, nothing breaks.
func _noisy_host() -> void:
	if not await s.connect_host():
		return
	s.send(GARBAGE)
	var done := await s.wait_until(func() -> bool: return s.card_state() == "done", 8000)
	s.check(done, "The turn ends after the noise (%s)" % s.card_state())
	await s.frames(4)
	s.check(s.reply_shown().contains("灯台は、still here."), "The split line shows whole")
	s.check(s.dock.get("connection_state") == "connected", "Still connected")


## A host that never says it is ready: the sidebar says it connects, Send rests, and closing the
## chat ends the host.
func _never_ready() -> void:
	await s.connect_host(false, {"connect_ms": 600000})
	# Long enough for the host to be up and waiting: closing then must still end it.
	await s.wait_ms(2500)
	s.check(s.dock.get("connection_state") == "connecting", "It says it is connecting")
	s.check(not bool(s.chat.call("is_ready")), "The chat isn't ready")
	var field := s.prompt()
	field.grab_focus()
	field.text = SAY_HELLO
	field.text_changed.emit()
	s.key(KEY_ENTER)
	await s.frames(3)
	s.check((s.transcript().get("_cards") as Array).is_empty(), "Enter sends nothing yet")
	s.check(field.text == SAY_HELLO, "…and keeps the message in the box")
	await s.shot("connecting")
	field.text = ""


## A failed turn with a message queued behind it, then Retry: the retry runs, and the queued
## message is neither lost nor sent twice.
func _queued_retry() -> void:
	if not await s.connect_host():
		return
	s.send(FAIL_SLOWLY)
	await s.wait_until(func() -> bool: return s.reply_shown().length() > 4, 8000)
	s.send("Then oil the hinge")
	var queue: Object = s.transcript().get("queue")
	s.check(int(queue.call("size_queued")) == 1, "The message waits in the queue")
	await s.wait_until(func() -> bool: return s.card_state() == "failed", 8000)
	await s.frames(4)
	s.check(int(queue.call("size_queued")) == 1, "A failed turn keeps the queued message")
	var retry: Button = s.transcript().find_child("Retry", true, false)
	s.check(retry != null and s.on_screen(retry), "The failed turn offers Retry")
	if retry == null:
		return
	await s.click(retry)
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 8000)
	await s.wait_ms(300)
	var requests := PackedStringArray()
	for card: Control in s.transcript().get("_cards"):
		requests.append(str(card.get("request")))
	s.check(requests.count(FAIL_SLOWLY) == 2, "Retry asks again (%s)" % ", ".join(requests))
	var sent := requests.count("Then oil the hinge") + int(queue.call("size_queued"))
	s.check(sent == 1, "The queued message is there once (%s)" % ", ".join(requests))
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 8000)
	await s.shot("retried")


## Accept pressed twice (the shortcut, then the button): one decision, one line.
func _accept_twice() -> void:
	if not await s.connect_host():
		return
	var decisions := []
	s.chat.connect("file_decision", func(event: Dictionary) -> void: decisions.append(event))
	s.send(WIDEN)
	await s.wait_until(func() -> bool: return bool(s.dock.get("_review_pending")), 8000)
	await s.frames(4)
	s.prompt().grab_focus()
	s.key(KEY_ENTER, false, true)
	await s.frames(1)
	s.dock.call("_on_accept")
	s.key(KEY_ENTER, false, true)
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_review_pending")), 6000)
	await s.wait_ms(1800)
	s.check(decisions.size() == 1, "Accept twice decides once (%d decisions)" % decisions.size())
	var said := s.visible_text().count("Change accepted")
	s.check(said == 1, "It says Change accepted once (%d)" % said)


## The host goes away while a change waits for review: Accept then can't reach it, and the card
## must not claim the change was accepted. Back online, Accept works.
func _accept_offline() -> void:
	if not await s.connect_host():
		return
	s.send(WIDEN)
	await s.wait_until(func() -> bool: return bool(s.dock.get("_review_pending")), 8000)
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 4000)
	s.kill_hosts()
	await s.wait_until(func() -> bool: return s.dock.get("connection_state") == "error", 6000)
	await s.frames(2)
	s.check(bool(s.dock.get("_review_pending")), "The change still waits after the host went")
	s.dock.call("_on_accept")
	await s.frames(3)
	s.check(
		not s.visible_text().contains("Change accepted"),
		"Offline, Accept doesn't claim the change was accepted"
	)
	s.check(s.card().call("awaiting_decision"), "Offline, the change still waits for a decision")
	await s.shot("offline-accept")
	if not await s.connect_host():
		return
	s.dock.call("_on_accept")
	var settled := await s.wait_until(
		func() -> bool: return not bool(s.dock.get("_review_pending")), 6000
	)
	s.check(settled, "Back online, Accept decides it")


## Leaving a chat while its reply streams, while its change waits for review: back again, it is
## all there, and the review still takes a decision.
func _switch_in_review() -> void:
	if not await s.connect_host():
		return
	s.send(WIDEN)
	var sessions: Node = s.dock.get("_chats")
	await s.wait_until(func() -> bool: return s.visible_text().contains("Editing door.gd"), 6000)
	var first := s.transcript()
	sessions.call("new_chat")
	await s.frames(3)
	s.check(s.transcript() != first, "New chat while it works shows a fresh conversation")
	await s.wait_until(func() -> bool: return bool(s.dock.get("_review_pending")), 8000)
	await s.frames(3)
	s.check(not s.visible_text().contains("Widened"), "The change stays in its own chat")
	sessions.call("open_chat", _kept_id(sessions, first))
	await s.frames(4)
	s.check(s.transcript() == first, "Back, the chat with the change is on screen")
	s.check(bool(s.card().call("awaiting_decision")), "Its change still waits for a decision")
	sessions.call("new_chat")
	await s.frames(2)
	sessions.call("open_chat", _kept_id(sessions, first))
	await s.frames(2)
	s.dock.call("_on_reject")
	var settled := await s.wait_until(
		func() -> bool: return not bool(s.dock.get("_review_pending")), 6000
	)
	s.check(settled, "Discard after switching back decides it")
	await s.frames(3)
	s.check(s.visible_text().count("Change discarded") == 1, "It says Change discarded once")


## Journeyman's own turn and another chat's finish at the same moment: each ends in its view.
func _finish_together() -> void:
	if not await s.connect_host():
		return
	s.send(TOGETHER)
	await s.frames(2)
	var first := s.transcript()
	var sessions: Node = s.dock.get("_chats")
	sessions.call("new_chat")
	await s.frames(2)
	s.send(TOGETHER)
	var second := s.transcript()
	await s.wait_until(
		func() -> bool: return not bool(s.dock.get("_busy")) and s.card_state() == "done", 8000
	)
	await s.wait_ms(400)
	s.check(s.card_state() == "done", "The chat on screen ends done (%s)" % s.card_state())
	s.check(s.reply_shown() == "Together.", "Its reply is its own: " + s.reply_shown())
	sessions.call("open_chat", _kept_id(sessions, first))
	await s.frames(3)
	var back := s.transcript()
	s.check(back == first or back != second, "The first chat comes back")
	s.check(s.card_state() == "done", "The first chat's turn ends done too (%s)" % s.card_state())
	s.check(s.reply_shown() == "Together.", "Its reply is its own: " + s.reply_shown())
	s.check(not (s.composer().get("stop") as Button).visible, "Nothing is left running")


## Stop pressed in another chat before the host took its message: that message never runs
## out of sight, and nothing it would have said shows anywhere.
func _other_chat_stop() -> void:
	if not await s.connect_host():
		return
	var states := PackedStringArray()
	s.chat.connect(
		"task_event",
		func(event: Dictionary) -> void:
			if event.get("kind") == "task" and event.get("origin") == "chat":
				states.append(str(event.get("state", "")))
	)
	s.send(KEEP_GOING)
	await s.wait_until(func() -> bool: return s.reply_shown().length() > 10, 8000)
	var sessions: Node = s.dock.get("_chats")
	sessions.call("new_chat")
	await s.frames(2)
	s.send(OTHER)
	(s.composer().get("stop") as Button).pressed.emit()
	await s.frames(2)
	var mine: Control = s.transcript().call("current_card")
	var state := str(mine.get("state")) if mine != null else "none"
	s.check(state == "failed", "Stopped before it started, it says so (%s)" % state)
	await s.wait_ms(1500)
	s.check(not states.has("done"), "The stopped message never runs (%s)" % ", ".join(states))
	s.check(not s.visible_text().contains("Gulls"), "Nothing it would have said shows")
	s.check(not (s.composer().get("stop") as Button).visible, "This chat has nothing running")
	sessions.call("open_chat", _kept_id(sessions, _first_view(sessions)))
	await s.frames(2)
	(s.composer().get("stop") as Button).pressed.emit()
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 6000)


## The host dies while two chats work: both requests end, and neither chat is left with Stop.
func _crash_two_chats() -> void:
	if not await s.connect_host():
		return
	s.send(KEEP_GOING)
	await s.wait_until(func() -> bool: return s.reply_shown().length() > 10, 8000)
	var first := s.transcript()
	var sessions: Node = s.dock.get("_chats")
	sessions.call("new_chat")
	await s.frames(2)
	s.send(KEEP_GOING)
	await s.wait_until(func() -> bool: return s.reply_shown().length() > 10, 8000)
	s.kill_hosts()
	await s.wait_until(func() -> bool: return s.dock.get("connection_state") == "error", 6000)
	await s.frames(4)
	var mine: Control = s.transcript().call("current_card")
	var state := str(mine.get("state")) if mine != null else "none"
	s.check(state != "working", "This chat's request ends (%s)" % state)
	s.check(not (s.composer().get("stop") as Button).visible, "This chat has no Stop left")
	var turns: Object = sessions.get("turns")
	s.check(int(turns.call("working_count")) == 0, "No other chat counts as working")
	sessions.call("open_chat", _kept_id(sessions, first))
	await s.frames(3)
	s.check(s.card_state() != "working", "The first chat's request ends (%s)" % s.card_state())
	s.check(not (s.composer().get("stop") as Button).visible, "The first chat has no Stop left")


## Revert pressed on an accepted change, then another chat opened while Journeyman works in the
## first: back there, the change says how the revert went, never "Reverting…" for good.
func _revert_elsewhere() -> void:
	if not await s.connect_host():
		return
	# A reply cut off by Stop ends on a whole word with "…", live and reopened.
	s.send(KEEP_GOING)
	await s.wait_until(
		func() -> bool: return s.reply_shown().begins_with("Working through t"), 8000
	)
	await s.click(s.composer().get("stop"))
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 8000)
	await s.frames(3)
	var cut := s.reply_shown()
	var whole := cut.trim_suffix("…").split(" ")[-1] in KEEP_GOING_WORDS
	s.check(
		cut.ends_with("time.") or (cut.ends_with("…") and whole),
		"A stopped reply ends on a whole word with … (or its full stop): " + cut
	)
	await s.wait_ms(200)
	s.send(WIDEN)
	await s.wait_until(func() -> bool: return bool(s.dock.get("_review_pending")), 8000)
	await s.frames(3)
	s.dock.call("_on_accept")
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_review_pending")), 6000)
	var change: Control = s.card()
	change.call("set_revertible", true)
	s.send(KEEP_GOING)
	await s.wait_until(func() -> bool: return s.reply_shown().length() > 10, 8000)
	var first := s.transcript()
	change.call("_on_revert")
	s.check(str(change.get("state")) == "accepted", "Revert pressed on the accepted change")
	var sessions: Node = s.dock.get("_chats")
	sessions.call("new_chat")
	var slash: Node = s.dock.get("_slash")
	var settled := await s.wait_until(
		func() -> bool: return (slash.get("_jobs") as Dictionary).is_empty(), 20000
	)
	s.check(settled, "The revert finished")
	await s.frames(3)
	sessions.call("open_chat", _kept_id(sessions, first))
	await s.frames(3)
	var reverting: Control = change.get("_reverting")
	s.check(not reverting.visible, "Back in its chat, the change no longer says Reverting…")
	(s.composer().get("stop") as Button).pressed.emit()
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 6000)


## A queued message goes out the moment the turn before it ends: that turn still gets its own
## timing line and outcome, and the next one isn't marked with them.
func _queue_follows() -> void:
	if not await s.connect_host():
		return
	s.send(SAY_HELLO)
	await s.wait_until(func() -> bool: return s.reply_shown().length() > 4, 8000)
	s.send("Darken the wood")
	var cards: Array = s.transcript().get("_cards")
	await s.wait_until(func() -> bool: return cards.size() == 2, 8000)
	await s.wait_until(
		func() -> bool: return str((cards[-1] as Control).get("state")) == "done", 8000
	)
	await s.wait_ms(300)
	for index: int in cards.size():
		var card: Control = cards[index]
		var said := s.visible_text(card.get_parent())
		s.check(
			said.contains("Completed in"),
			"Request %d has its own timing line: %s" % [index + 1, said.left(200)]
		)
		s.check(str(card.get("state")) == "done", "Request %d ends done" % (index + 1))
	# Sent the moment a failed turn ends, a message goes out; it never waits in a paused queue.
	s.send("Try the broken provider")
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 8000)
	s.send(SAY_HELLO)
	await s.frames(2)
	var queue: Object = s.transcript().get("queue")
	s.check(int(queue.call("size_queued")) == 0, "Right after a failed turn, a message goes out")
	await s.wait_until(func() -> bool: return s.card_state() == "done", 8000)


## The kept chat Journeyman works in.
func _first_view(sessions: Node) -> Control:
	var kept: Dictionary = sessions.get("_kept")
	return kept.values()[0] if not kept.is_empty() else null


## While a reply streams, timings are hidden and shown again in Settings, the sidebar is moved
## to another dock slot, the theme switches, the sidebar is resized and the editor loses focus: the
## reply finishes whole.
func _editor_life() -> void:
	if not await s.connect_host():
		return
	s.send(LIVE)
	await s.wait_until(func() -> bool: return s.reply_shown().length() > 10, 8000)
	s.dock.call("apply_preferences", {"show_timings": false})
	s.dock.call("apply_preferences", {"show_timings": true})
	var theme: GDScript = load("res://addons/joinery/ui_theme.gd")
	theme.call("set_pref", "theme", "light" if s.theme_name.contains("dark") else "dark")
	await s.frames(3)
	var parent := s.dock.get_parent()
	if not Engine.is_editor_hint():
		# Moved to another dock slot, or made floating: out of the tree and back in one go.
		parent.remove_child(s.dock)
		parent.add_child(s.dock)
		s.dock.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		s.tree.root.size = Vector2i(s.width + 200, s.tree.root.size.y)
	s.dock.notification(Control.NOTIFICATION_APPLICATION_FOCUS_OUT)
	await s.frames(3)
	theme.call("set_pref", "theme", "match")
	s.dock.notification(Control.NOTIFICATION_APPLICATION_FOCUS_IN)
	var done := await s.wait_until(func() -> bool: return s.card_state() == "done", 8000)
	if not Engine.is_editor_hint():
		s.tree.root.size = Vector2i(s.width, s.tree.root.size.y)
	await s.frames(4)
	s.check(done, "The reply finishes (%s): %s" % [s.card_state(), _turn_text().left(300)])
	s.check(s.reply_shown().ends_with("own light."), "It arrives whole: " + s.reply_shown())
	s.check(not (s.composer().get("stop") as Button).visible, "Send is back")
	await s.shot("after")


## A saved chat with a reply, a refused step, a policy stop, a failed turn and an accepted
## change: reopened (as the editor does at start, with motion left as the editor has it), it
## shows the same.
func _reopen() -> void:
	if not await s.connect_host():
		return
	var asks := [SAY_HELLO, DENIED, POLICY, "Try the broken provider"]
	for text: String in asks:
		s.send(text)
		await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 8000)
		await s.wait_ms(200)
	# A reply cut off by Stop ends on a whole word with "…", live and reopened.
	s.send(KEEP_GOING)
	await s.wait_until(
		func() -> bool: return s.reply_shown().begins_with("Working through t"), 8000
	)
	await s.click(s.composer().get("stop"))
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 8000)
	await s.frames(3)
	var cut := s.reply_shown()
	var whole := cut.trim_suffix("…").split(" ")[-1] in KEEP_GOING_WORDS
	s.check(
		cut.ends_with("time.") or (cut.ends_with("…") and whole),
		"A stopped reply ends on a whole word with … (or its full stop): " + cut
	)
	await s.wait_ms(200)
	s.send(WIDEN)
	await s.wait_until(func() -> bool: return bool(s.dock.get("_review_pending")), 8000)
	await s.frames(3)
	s.dock.call("_on_accept")
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_review_pending")), 6000)
	await s.wait_ms(400)
	var live := _summary()
	var sessions: Node = s.dock.get("_chats")
	var id := str(sessions.call("current_id"))
	s.check(not id.is_empty(), "The chat was saved under an id")
	sessions.call("new_chat")
	await s.frames(2)
	# In the editor nothing set motion on or off: a restored chat must not depend on it.
	var motion: GDScript = load("res://addons/joinery/ui_motion.gd")
	var key: String = motion.get("_ENABLED_META")
	var was: Variant = Engine.get_meta(key) if Engine.has_meta(key) else null
	Engine.remove_meta(key)
	sessions.call("open_chat", id)
	if was != null:
		Engine.set_meta(key, was)
	await s.frames(4)
	var reopened := _summary()
	for part: String in live:
		s.check(
			reopened[part] == live[part],
			"Reopened, %s is the same (live %s, reopened %s)" % [part, live[part], reopened[part]]
		)
	s.dock.call("apply_preferences", {"show_timings": false})
	s.dock.call("apply_preferences", {"show_timings": true})
	await s.frames(2)
	# Viewed read-only (with Continue), it still fits the sidebar it is in.
	var room := (s.dock.get_parent() as Control).size.x
	var wide := s.dock.get_combined_minimum_size().x
	s.check(
		wide <= room + 1.0, "The earlier chat fits the sidebar (%.0f of %.0f px)" % [wide, room]
	)
	await s.shot("reopened")


## A turn that used an app and had a call refused: its status line (the app, then "Done in
## under 1 s, 1 step not run") fits even the narrowest sidebar.
func _busy_status() -> void:
	if not await s.connect_host():
		return
	s.send(BUSY_STATUS)
	await s.wait_until(func() -> bool: return s.visible_text().contains("not run"), 8000)
	await s.frames(3)
	var room := (s.dock.get_parent() as Control).size.x
	var wide := s.dock.get_combined_minimum_size().x
	s.check(wide <= room + 1.0, "The turn's status line fits (%.0f of %.0f px)" % [wide, room])
	s.check(s.visible_text().contains("1 step not run"), "It still says the step was not run")
	await s.shot("status")


## What a chat shows, part by part, to compare a live chat with the same chat reopened.
func _summary() -> Dictionary:
	var cards: Array = s.transcript().get("_cards")
	var states := PackedStringArray()
	for card: Control in cards:
		states.append(str(card.get("state")))
	var text := s.visible_text()
	var replies := PackedStringArray()
	var markdown: GDScript = load("res://addons/joinery/chat_markdown.gd")
	for message: Dictionary in s.transcript().get("_messages"):
		if message["who"] == "Journeyman":
			replies.append(str(markdown.call("shown", message["parts"])).strip_edges())
	return {
		"requests": cards.size(),
		"outcomes": ", ".join(states),
		"replies": " / ".join(replies),
		"policy stop notice": s.transcript().find_child("PolicyStop", true, false) != null,
		"accepted line": text.count("Change accepted"),
		"error line": text.count("HTTP 500"),
		"steps not run": text.count("not run"),
		"completed lines": text.count("Completed"),
	}


## Every part of the last reply, and every trail line, stays inside the conversation's column.
func _inside_column(state: String) -> void:
	var scroll := s.transcript() as ScrollContainer
	var column := scroll.get_global_rect()
	var outside := PackedStringArray()
	for node: Node in scroll.find_children("*", "Control", true, false):
		var control := node as Control
		if not control.is_visible_in_tree() or control.top_level:
			continue
		if control is ScrollContainer or control.get_parent() is ScrollContainer:
			continue
		if _in_sideways_box(control, scroll):
			continue
		if control.get_global_rect().end.x > column.end.x + 1.0:
			outside.append(s.control_name(control))
	s.check(
		outside.is_empty(),
		(
			"%s: everything stays inside the conversation (%s)"
			% [state, ", ".join(outside.slice(0, 3))]
		)
	)


## Code boxes scroll sideways on purpose: what is inside one may be wider.
static func _in_sideways_box(control: Control, scroll: ScrollContainer) -> bool:
	var parent := control.get_parent()
	while parent != null and parent != scroll:
		if parent is ScrollContainer:
			return true
		parent = parent.get_parent()
	return false


## The words shown in the latest request's turn (its card, reply and lines).
func _turn_text() -> String:
	var card := s.card()
	return s.visible_text(card.get_parent()) if card != null else ""


func _kept_id(sessions: Node, view: Control) -> String:
	var kept: Dictionary = sessions.get("_kept")
	for id: String in kept:
		if kept[id] == view:
			return id
	return ""


## What the chat host hands the sidebar when a paired phone asks (Joinery Remote, ADR 0093).
func _from_phone(action: Dictionary, ref: String) -> void:
	(
		s
		. chat
		. call(
			"_handle",
			{
				"kind": "remote_action",
				"ref": ref,
				"action": action,
				"device": {"id": "d_1", "name": "Ana's iPhone"},
			}
		)
	)


## A phone accepts the change waiting at the editor: the sidebar does it as its own Accept, the
## card settles as after a click, the transcript names the phone and nothing takes the focus.
func _remote_decision() -> void:
	if not await s.connect_host():
		return
	s.send(WIDEN)
	var ready := await s.wait_until(
		func() -> bool: return bool(s.dock.get("_review_pending")), 8000
	)
	s.check(ready, "The change waits for review")
	await s.frames(2)
	var focused: Control = s.dock.get_viewport().gui_get_focus_owner()
	_from_phone({"kind": "decision", "transaction_id": "tx-widen", "accept": true}, "r1")
	await s.frames(1)
	s.check(s.card().call("awaiting_decision") == false, "The phone's Accept took the decision")
	s.check(s.visible_text().contains("Accepted from Ana's iPhone"), "The transcript names it")
	var settled := await s.wait_until(
		func() -> bool: return not bool(s.dock.get("_review_pending")), 8000
	)
	s.check(settled, "The host's decision closes the review")
	s.check(str(s.card().get("state")) == "accepted", "The card reads accepted")
	s.check(
		s.dock.get_viewport().gui_get_focus_owner() == focused, "A phone's decision takes no focus"
	)
	await s.shot("decided")
	# A second Accept from the phone finds nothing waiting at the editor.
	_from_phone({"kind": "decision", "transaction_id": "tx-widen", "accept": true}, "r2")
	await s.frames(2)
	s.check(
		s.visible_text().count("Accepted from Ana's iPhone") == 1,
		"A decision the editor no longer has is not shown twice"
	)


## A phone sends a message while the user types one: it goes as a request of its own, said to be
## from the phone, and the user's draft stays. One sent while Journeyman works waits in the queue;
## the phone's Stop is the Stop button.
func _remote_prompt() -> void:
	if not await s.connect_host():
		return
	s.prompt().text = "my own draft"
	_from_phone({"kind": "prompt", "text": SAY_HELLO}, "p1")
	var replied := await s.wait_until(func() -> bool: return s.visible_text().contains("Hello!"))
	s.check(replied, "The phone's message ran as a request")
	s.check(s.visible_text().contains("Sent from Ana's iPhone"), "The transcript names the phone")
	s.check(s.prompt().text == "my own draft", "The user's draft is untouched")
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 8000)
	_from_phone({"kind": "prompt", "text": KEEP_GOING}, "p2")
	var working := await s.wait_until(func() -> bool: return bool(s.dock.get("_busy")), 8000)
	s.check(working, "The sidebar shows Journeyman at work on the phone's request")
	var queue: Object = s.transcript().get("queue")
	_from_phone({"kind": "prompt", "text": SAY_HELLO}, "p3")
	await s.frames(2)
	s.check(int(queue.call("size_queued")) == 1, "A message sent meanwhile waits in the queue")
	s.check(
		not s.visible_text().contains("Queued from"), "No extra line cuts into the running reply"
	)
	await s.shot("queued")
	_from_phone({"kind": "cancel"}, "p4")
	var stopped := await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 8000)
	s.check(stopped, "The phone's Stop stopped the request")
	s.check(s.visible_text().contains("Stopped from Ana's iPhone"), "The stop names the phone")


## Go pressed while the turn that proposed the to-do still runs is not refused: it waits in
## the queue ("Starting…" on the card) and Stop takes it back. Go from the phone does the same,
## a message typed after it waits behind it, and the to-do starts once Journeyman is free.
func _todo_go_busy() -> void:
	if not await s.connect_host():
		return
	var tasks: Node = s.dock.get("_tasks")
	var queue: Object = s.transcript().get("queue")
	s.send(PLAN_SLOWLY)
	var shown := await s.wait_until(
		func() -> bool:
			var found: Variant = tasks.get("todo_card")
			return found is Control and (found as Control).is_visible_in_tree(),
		8000
	)
	s.check(shown, "The to-do card shows")
	if not shown:
		return
	var todo: Control = tasks.get("todo_card")
	var go: Button = todo.get("go")
	s.check(bool(s.dock.call("_occupied")), "The turn that proposed it still runs")
	go.pressed.emit()
	await s.frames(2)
	s.check(not s.visible_text().contains("Wait for Journeyman"), "Go is not refused")
	s.check(int(queue.call("size_queued")) == 1, "Go waits in the queue")
	s.check(go.disabled, "Go rests while it waits")
	s.check(s.visible_text(todo).contains("Starting"), "The to-do says it is starting")
	await s.shot("go-waiting")
	(s.composer().get("stop") as Button).pressed.emit()
	await s.frames(2)
	s.check(int(queue.call("size_queued")) == 0, "Stop takes the waiting Go back")
	s.check(not go.disabled, "Go is offered again")
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 8000)
	# Again, from the phone, with a message typed after it: the to-do goes first, by itself.
	s.send(PLAN_SLOWLY)
	await s.wait_until(func() -> bool: return s.card_state() == "working", 8000)
	await s.wait_until(func() -> bool: return _turn_text().contains("plan"), 8000)
	s.check(bool(s.dock.call("_occupied")), "The turn is still running")
	_from_phone({"kind": "todo_go", "todo_id": "todo-slow"}, "g1")
	s.send(SAY_HELLO)
	await s.frames(2)
	s.check(int(queue.call("size_queued")) == 2, "Go and the message wait in order")
	var started := await s.wait_until(
		func() -> bool: return s.card() != null and str(s.card().get("request")) == "Go", 15000
	)
	s.check(started, "The to-do starts once Journeyman is free")
	s.check(not s.visible_text().contains("Wait for Journeyman"), "Nothing asked to wait")
	var left: PackedStringArray = queue.call("texts")
	s.check(left == PackedStringArray([SAY_HELLO]), "The message waits behind it (%s)" % left)
	# Stop on the to-do's work keeps a typed message (only a waiting Go is taken back).
	(s.composer().get("stop") as Button).pressed.emit()
	await s.wait_until(func() -> bool: return not bool(s.dock.get("_busy")), 8000)
	s.check(int(queue.call("size_queued")) == 1, "Stop keeps the typed message queued")


## A phone's request still holds the project the editor opens: the sidebar says so and offers
## "Stop it now" or "Wait" (joinery.remote.handoff); Stop sends the host its line, and the
## offer goes once the editor has the project.
func _remote_handover() -> void:
	var waiting := "A request from your phone is still running in this project."
	s.chat.call("_handle", {"kind": "handover", "state": "waiting", "text": waiting})
	await s.frames(2)
	s.check(s.visible_text().contains(waiting), "The sidebar says the phone has it")
	s.check(s.visible_text().contains("Stop it now"), "It offers to stop the phone's request")
	await s.shot("handover")
	var stop: Button = null
	for button: Node in s.dock.find_children("*", "Button", true, false):
		if button is Button and (button as Button).text == "Stop it now":
			stop = button
	s.check(stop != null, "Stop it now is a button")
	if stop != null:
		stop.pressed.emit()
	await s.frames(2)
	s.check(not s.visible_text().contains("Stop it now"), "The offer goes once answered")
	s.chat.call("_handle", {"kind": "handover", "state": "done", "text": "The editor has it."})
	await s.frames(2)
	s.check(s.visible_text().contains("The editor has it."), "And the sidebar says when done")
