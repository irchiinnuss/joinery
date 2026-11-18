@tool
extends VBoxContainer
## Agents working beside Journeyman (parallel tasks, ADR 0036), between the transcript and the
## footer: one card each (sidebar_task_card.gd) with its title, apps, step, timer and its own
## Stop. It also runs the rest of the sidebar's side of parallel work:
##
## - "Run alongside" in the composer while Journeyman works starts the typed message as a task;
## - the header says "3 agents working" while two or more agents work;
## - Stop on Journeyman's own request asks, in one quiet line, whether to stop the tasks too;
## - a task's change opens its review by itself once ready (review_opener.gd: never while
##   the user types, never over the main request's review, not when Joinery kept it);
## - a finished task leaves one line in the transcript ("Lamp: change accepted.") and its card
##   steps out; its change is decided in the Review tab like any other;
## - a console error fix ("console": true, ADR 0075) has no card and leaves no line: its step and
##   timer go on its rows in the Errors list (errors_card.gd), the one place it shows.
##
## It hears the host through the chat bridge's task_event ([method on_event]).
##
## The playtester (ADR 0038) is an agent at work too: "/playtest" starts it, and its card
## (playtest_card.gd) sits here with the tasks while it plays. Its written report goes to the
## transcript, each of its "Fix:" lines as an "Ask Journeyman to fix" offer, and "Fix this" on
## a finding puts a ready request in the composer ([method on_playtest]).
##
## "Make it match" (ADR 0045) has its card here too (sidebar_match.gd): the project's scores
## against the art bible, while its fixes run as tasks with cards of their own. So do the
## accessibility and performance checks (ADR 0049, ADR 0050), with the same card, and the
## sound pass (sidebar_sound_pass.gd): what the level is missing, while its sounds are made.
## The design check has a card of its own (sidebar_design.gd), so does "/tidy" (sidebar_tidy.gd),
## and so does a request's to-do (sidebar_todo.gd): its steps and "done when" items, Go, and each
## item's result.

## The most of the sidebar's height the cards take before they scroll.
const CARDS_SHARE := 0.4
const CARD := preload("sidebar_task_card.gd")
const DEBUG := preload("sidebar_debug.gd")
const DESIGN := preload("sidebar_design.gd")
const TIDY := preload("sidebar_tidy.gd")
const CUT := preload("sidebar_cut.gd")
const TODO := preload("sidebar_todo.gd")
const MATCH := preload("sidebar_match.gd")
const SOUND_PASS := preload("sidebar_sound_pass.gd")
const IDLE := preload("sidebar_idle.gd")
const MEMORY := preload("sidebar_memory.gd")
const MIX := preload("sidebar_mix.gd")
const COMMANDS := preload("sidebar_commands.gd")
const PLAYTEST_CARD := preload("playtest_card.gd")
const PLAYTESTER := preload("playtester_setting.gd")
const VARIANTS := preload("sidebar_variants.gd")
const SCENE_VARIANT := preload("sidebar_scene_variant.gd")
const UI_MOTION := preload("ui_motion.gd")
const STYLE := preload("sidebar_style.gd")
const UI_THEME := preload("ui_theme.gd")
## Pixels the cards must fall under their share before a pinned action goes back to its card,
## so it doesn't flicker on the edge.
const PIN_SLACK := 8.0
## Seconds a finished task's card stays before it steps out.
const LINGER := 4.0
## Seconds a ready change waits before its review opens: one Joinery keeps without asking
## (`[agents] auto_accept`) is accepted by then and opens nothing.
const OPEN_DELAY := 0.8
## Final state -> how its transcript line ends.
const ENDINGS := {
	"accepted": "change accepted.",
	"discarded": "change discarded.",
	"done": "done, nothing needed changing.",
	"failed": "didn't finish.",
	"stopped": "stopped.",
}

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The chat bridge (chat_bridge.gd), once attached; its playtest events come here.
var bridge: Object:
	set(value):
		bridge = value
		if bridge != null and bridge.has_signal("playtest_event"):
			if not bridge.is_connected("playtest_event", on_playtest):
				bridge.connect("playtest_event", on_playtest)
		# Journeyman's work trail goes to its request's card.
		if bridge != null and bridge.has_signal("trail_event") and dock != null:
			var trail := Callable(dock, "add_trail")
			if not bridge.is_connected("trail_event", trail):
				bridge.connect("trail_event", trail)
		if bridge != null and bridge.has_signal("tests_event") and dock != null:
			var tests := Callable(dock, "add_trail")
			if not bridge.is_connected("tests_event", tests):
				bridge.connect("tests_event", tests)
		if variants != null:
			variants.call("follow", bridge)
		if todo != null:
			todo.call("listen", bridge)
		if debug != null:
			debug.call("listen", bridge)
## The playtest card, while there is one.
var playtest: Control
## The match card (match_card.gd), while there is one.
var match_card: Control
## The accessibility and performance cards (match_card.gd), while there are.
var access_card: Control
var perf_card: Control
## The design check's card (design_card.gd), while there is one.
var design_card: Control
## The tidy card (tidy_card.gd): what the game never uses, while there is one.
var tidy_card: Control
## The cut card (cut_card.gd): the polish list and proposed cuts, while there is one.
var cut_card: Control
## A project command's form (command_card.gd), while there is one.
var command_card: Control
## Requests tried several ways (sidebar_variants.gd): their ways' events go there.
var variants: Node
## The sound pass card (sound_pass_card.gd), while there is one.
var sound_card: Control
## A request's to-do (sidebar_todo.gd) and its card (todo_card.gd), while there is one.
var todo: Node
var todo_card: Control
## Console errors (sidebar_debug.gd) and their list (errors_card.gd), once there is one.
var debug: Node
var errors_card: Control
var _cards: Dictionary = {}
## Console error fixes followed by the Errors list: task id -> its last state.
var _console: Dictionary = {}
var _main_busy := false
var _offer: Control
## The cards' area: their scroll, and under it the strip that keeps a card's primary action in
## sight while the cards scroll ([method _fit]).
var _area: Control
var _scroll: ScrollContainer
var _pin: MarginContainer
## The action row now in [member _pin], and the card it belongs to.
var _pinned: Control
var _pinned_card: Control
var _fitting := false


static func attach(sidebar: Control, content: Control, before: Control) -> VBoxContainer:
	var tasks: VBoxContainer = load("res://addons/joinery/sidebar_tasks.gd").new()
	tasks.dock = sidebar
	# The cards scroll on their own once they pass a share of the sidebar, so the chat above
	# them keeps room and still scrolls.
	var area := VBoxContainer.new()
	area.name = "TaskArea"
	area.add_theme_constant_override("separation", 0)
	content.add_child(area)
	content.move_child(area, before.get_index())
	tasks._area = area
	var scroll := ScrollContainer.new()
	scroll.name = "TaskScroll"
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	# Wheel and touchpad scroll it; a bar would narrow the cards off the chat's edges, so the
	# cards fade out at the edge that has more, as the transcript does.
	scroll.vertical_scroll_mode = ScrollContainer.SCROLL_MODE_SHOW_NEVER
	scroll.add_theme_stylebox_override("panel", StyleBoxEmpty.new())
	STYLE.scroll_fade(scroll)
	area.add_child(scroll)
	tasks._scroll = scroll
	tasks.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(tasks)
	# While the cards scroll, the newest card's primary action (Go, Show all) waits here.
	var pin := MarginContainer.new()
	pin.name = "TaskPin"
	pin.hide()
	area.add_child(pin)
	tasks._pin = pin
	var fit: Callable = tasks._fit
	tasks.minimum_size_changed.connect(fit)
	tasks.visibility_changed.connect(fit)
	pin.minimum_size_changed.connect(fit)
	sidebar.resized.connect(fit)
	# Agents' cards share the chat's centred column, and so does their pinned action.
	STYLE.centered_column(tasks)
	STYLE.centered_column(pin)
	var composer: Object = sidebar.get("_composer")
	if composer != null and composer.has_signal("alongside"):
		composer.connect("alongside", tasks.run_alongside)
	# "/debug" and the offer after a new error in Output start a debugging task.
	tasks.debug = DEBUG.attach(sidebar, tasks)
	if tasks.bridge != null:
		tasks.debug.call("listen", tasks.bridge)
	# "/match" and Make it match: the project against the art bible.
	MATCH.attach(sidebar, tasks)
	# "/access" and "/perf": the game's accessibility and performance.
	MATCH.attach(sidebar, tasks, "access")
	MATCH.attach(sidebar, tasks, "perf")
	# "/design": the game against its own brief.
	DESIGN.attach(sidebar, tasks)
	# "/tidy": what the game never uses, removed as one reviewed change.
	TIDY.attach(sidebar, tasks)
	# "/cut": one polish list from every check, written as one reviewed change.
	CUT.attach(sidebar, tasks)
	tasks.variants = VARIANTS.attach(sidebar, tasks)
	# A bare "/variant": an inherited scene of the scene open in the editor.
	SCENE_VARIANT.attach(sidebar, tasks)
	# "/sound-pass": the sounds the level lacks.
	SOUND_PASS.attach(sidebar, tasks)
	# "/idle": the runs waiting for when you're away.
	IDLE.attach(sidebar, tasks)
	# "/remember" and Remember this: a line for JOURNEYMAN.md, through Review.
	MEMORY.attach(sidebar, tasks)
	# "/mix": the buses and the music mix, through Review (Godot's Audio panel does the rest).
	MIX.attach(sidebar, tasks)
	# The project's own commands (.joinery/commands): slash entries with a small form.
	COMMANDS.attach(sidebar, tasks)
	# A request's to-do: its steps, Go, and what "done" means, checked.
	tasks.todo = TODO.attach(sidebar, tasks)
	if tasks.bridge != null:
		tasks.todo.call("listen", tasks.bridge)
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("playtest_requested"):
		slash.connect("playtest_requested", tasks.start_playtest)
	return tasks


func _init() -> void:
	name = "Tasks"
	theme_type_variation = "JoineryTight"
	accessibility_name = "Agents working alongside"
	hide()


## Every task card, oldest first.
func cards() -> Array[Control]:
	var found: Array[Control] = []
	for card: Control in _cards.values():
		if is_instance_valid(card):
			found.append(card)
	return found


func card(task_id: String) -> Control:
	var found: Control = _cards.get(task_id)
	return found if is_instance_valid(found) else null


## Tasks still working.
func working_count() -> int:
	var count := 0
	for each: Control in cards():
		if each.call("working"):
			count += 1
	if variants != null:
		count += int(variants.call("working_count"))
	return count


## Agents at work beside Journeyman's own request: the tasks, and messages from other chats
## running as their chat's own turn (chat_turns.gd). They share the project's [agents] parallel.
func agents_count() -> int:
	var turns := _chat_turns()
	return working_count() + (int(turns.call("working_count")) if turns != null else 0)


func _chat_turns() -> Object:
	var chats: Variant = dock.get("_chats") if dock != null else null
	var turns: Variant = chats.get("turns") if chats is Object else null
	return turns if turns is Object else null


## Journeyman's own request started or ended: the header count and "Run alongside" follow it.
func set_main_busy(busy: bool) -> void:
	_main_busy = busy
	if not busy and is_instance_valid(_offer) and working_count() == 0:
		_drop_offer()
	_sync()


## One event from a task (chat_bridge.gd's task_event).
func on_event(event: Dictionary) -> void:
	var id := str(event.get("task_id", ""))
	var turns := _chat_turns()
	if turns != null and turns.call("take", event):
		# Another chat's own turn: it shows in that chat, never as a card.
		_sync()
		return
	if event.get("kind") == "task" and dock != null:
		# An agent may have come free: a message waiting in the chat on screen may start.
		dock.call_deferred("_drain")
		dock.call_deferred("_sync")
	if variants != null and variants.call("take", event):
		_sync()
		return
	if bool(event.get("console", false)) or _console.has(id):
		_follow_console(id, event)
		return
	match str(event.get("kind", "")):
		"task":
			var each := card(id)
			if each == null:
				each = _add(id, str(event.get("title", "Task")))
			var before := str(each.get("state"))
			each.call("apply", event)
			var after := str(each.get("state"))
			if after != before and ENDINGS.has(after):
				_finished(each, event)
			elif after != before and after == "in_review":
				open_review_soon(id, str(each.get("transaction_id")))
		"progress":
			var each := card(id)
			if each != null:
				each.call("set_step", str(event.get("text", "")))
		"trail":
			# A task's call as it starts is its live line, as on Journeyman's own card.
			var each := card(id)
			var starting := str(event.get("state", "")) == "running"
			if each != null and starting and str(event.get("note", "")) != "thought":
				each.call("set_step", str(event.get("text", "")))
		"tests":
			# The test it checks now, then the run's summary.
			var each := card(id)
			var said := str(event.get("summary" if event.get("done") else "now", ""))
			if each != null and not said.is_empty():
				each.call("set_step", said)
	_sync()


## A console error fix: the Errors list shows it; it ends without a transcript line.
func _follow_console(id: String, event: Dictionary) -> void:
	var before := str(_console.get(id, ""))
	var state := str(event.get("state", before)) if event.get("kind") == "task" else before
	_console[id] = state
	if debug != null:
		debug.call("track", event)
	if ENDINGS.has(state):
		_console.erase(id)
	elif state == "in_review" and before != "in_review":
		open_review_soon(id, str(event.get("transaction_id", "")))


## A task's change is ready: its review opens by itself after OPEN_DELAY, unless it was decided
## meanwhile or the main request's own review is waiting (it is in the Review tab's list).
func open_review_soon(task_id: String, transaction_id: String) -> void:
	var opener: Object = dock.get("_opener") if dock != null else null
	if opener == null or transaction_id.is_empty() or not is_inside_tree():
		return
	get_tree().create_timer(OPEN_DELAY).timeout.connect(
		func() -> void:
			var each := card(task_id)
			var state := str(each.get("state")) if each != null else str(_console.get(task_id))
			if state != "in_review" or bool(dock.get("_review_pending")):
				return
			opener.call("request", _show_review.bind(transaction_id), "task:" + transaction_id)
	)


func _show_review(transaction_id: String) -> void:
	if bridge != null and bridge.has_method("show_task_review"):
		bridge.call("show_task_review", transaction_id)


## "Run alongside": [param text] starts now as its own task.
func run_alongside(text: String) -> void:
	var composer: Object = dock.get("_composer")
	if bridge == null or not bridge.has_method("start_task"):
		return
	var targets: Array = composer.call("target_snapshot") if composer != null else []
	if not bridge.call("start_task", text, targets):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)
		return
	if composer != null:
		(composer.get("prompt") as TextEdit).clear()
		var images: Object = composer.get("attachments")
		if images != null:
			images.call("clear")
	dock.call("show_status", "Started alongside", 2.0)


## Stop on Journeyman's own request: when tasks still work, ask once whether to stop them too.
func offer_stop_all() -> void:
	var count := working_count()
	if count == 0 or is_instance_valid(_offer):
		return
	var transcript: Object = dock.get("_transcript")
	if transcript == null:
		return
	var text := (
		"A task is still working. Stop it too?"
		if count == 1
		else "%d tasks are still working. Stop them too?" % count
	)
	_offer = transcript.call(
		"add_offer", text, [["Stop all", _stop_all], ["Keep them", _drop_offer]]
	)


## "/playtest [focus]": the playtester starts; its card shows at once.
func start_playtest(focus: String) -> void:
	if bridge == null or not bridge.has_method("start_playtest"):
		return
	if not PLAYTESTER.enabled():
		dock.call("show_status", "The playtester is off. Turn it on in Settings.", 4.0)
		return
	if is_instance_valid(playtest) and playtest.call("working"):
		dock.call("show_status", "A playtest is already running.", 3.0)
		return
	if not bridge.call("start_playtest", focus):
		dock.call("show_status", "Journeyman isn't connected.", 3.0)
		return
	_playtest_card("")


## One event from a playtest (chat_bridge.gd's playtest_event).
func on_playtest(event: Dictionary) -> void:
	if not PLAYTESTER.enabled() and not is_instance_valid(playtest):
		# Turned off for the project: no card (the host plays nothing then either).
		return
	var id := str(event.get("playtest_id", ""))
	var card := playtest
	if not is_instance_valid(card) or not str(card.get("playtest_id")) in ["", id]:
		card = _playtest_card(id)
	card.set("playtest_id", id)
	var before := str(card.get("state"))
	card.call("apply", event)
	if str(card.get("state")) != before:
		match str(card.get("state")):
			"done":
				_playtest_report(event)
			"failed":
				var transcript: Object = dock.get("_transcript")
				if transcript != null:
					transcript.call(
						"add_notice", "Playtest: " + str(card.call("step_text")), "error"
					)
	_sync()


func _playtest_card(id: String) -> Control:
	if is_instance_valid(playtest):
		playtest.queue_free()
	playtest = PLAYTEST_CARD.new(id)
	playtest.connect("stop_pressed", _stop_playtest)
	playtest.connect("close_pressed", _close_playtest)
	playtest.connect("fix_requested", prefill)
	add_child(playtest)
	show()
	UI_MOTION.enter(playtest)
	_sync()
	return playtest


## The playtester's words in the transcript, and each follow-up it proposed as an offer.
func _playtest_report(event: Dictionary) -> void:
	var transcript: Object = dock.get("_transcript")
	if transcript == null:
		return
	var summary := str(event.get("summary", ""))
	if not summary.is_empty() and str(event.get("origin", "")) != "agent":
		transcript.call("add_message", "Playtester", summary)
	var followups: Variant = event.get("followups", [])
	if followups is Array:
		for request: Variant in followups:
			var text := str(request)
			transcript.call("add_offer", text, [["Ask Journeyman to fix", prefill.bind(text)]])


## Puts [param text] in the composer, ready to send as a normal request.
func prefill(text: String) -> void:
	var composer: Object = dock.get("_composer")
	if composer == null:
		return
	var prompt := composer.get("prompt") as TextEdit
	prompt.text = text
	prompt.set_caret_line(prompt.get_line_count() - 1)
	prompt.set_caret_column(prompt.get_line(prompt.get_line_count() - 1).length())
	prompt.grab_focus()
	dock.call("show_status", "Request ready: edit it or send it", 3.0)


func _stop_playtest() -> void:
	if bridge != null and bridge.has_method("cancel_playtest"):
		bridge.call("cancel_playtest", str(playtest.get("playtest_id")))


func _close_playtest() -> void:
	var card := playtest
	playtest = null
	UI_MOTION.exit(
		card,
		null,
		func() -> void:
			card.queue_free()
			sync_visible()
	)
	_sync()


func _stop_all() -> void:
	_drop_offer()
	for each: Control in cards():
		each.call("mark_stopping")
	if bridge != null and bridge.has_method("cancel_tasks"):
		bridge.call("cancel_tasks")


func _drop_offer() -> void:
	var transcript: Object = dock.get("_transcript")
	if transcript != null and is_instance_valid(_offer):
		transcript.call("remove_offer", _offer)
	_offer = null


func _add(id: String, title: String) -> Control:
	var each: Control = CARD.new(id, title)
	each.connect("stop_pressed", _on_stop.bind(id))
	each.connect("review_pressed", _on_review.bind(each))
	_cards[id] = each
	add_child(each)
	if not visible:
		show()
	UI_MOTION.enter(each)
	return each


func _on_stop(id: String) -> void:
	if bridge != null and bridge.has_method("cancel_task"):
		bridge.call("cancel_task", id)


func _on_review(each: Control) -> void:
	if bridge != null and bridge.has_method("show_task_review"):
		bridge.call("show_task_review", str(each.get("transaction_id")))


## A task is over: one line in the transcript, then its card steps out.
func _finished(each: Control, event: Dictionary) -> void:
	var state := str(each.get("state"))
	var transcript: Object = dock.get("_transcript")
	if transcript != null:
		var kind := "error" if state == "failed" else "info"
		var line := "%s: %s" % [str(each.get("title")), ENDINGS[state]]
		var message := str(event.get("message", ""))
		if (state == "failed" or str(each.get("role")) == "debugger") and not message.is_empty():
			line = "%s: %s" % [str(each.get("title")), message]
		transcript.call("add_notice", line, kind)
	if not is_inside_tree():
		_remove(str(each.get("task_id")))
		return
	get_tree().create_timer(LINGER).timeout.connect(
		func() -> void:
			if is_instance_valid(each) and not each.call("working"):
				UI_MOTION.exit(each, null, _remove.bind(str(each.get("task_id"))))
	)


func _remove(id: String) -> void:
	var each: Control = _cards.get(id)
	_cards.erase(id)
	if is_instance_valid(each):
		each.queue_free()
	sync_visible()
	_sync()


## Shown while any card is: a task's, the playtest's, the match card, a variant strip or
## the sound pass's.
func sync_visible() -> void:
	visible = (
		not _cards.is_empty()
		or is_instance_valid(playtest)
		or is_instance_valid(match_card)
		or is_instance_valid(access_card)
		or is_instance_valid(perf_card)
		or is_instance_valid(design_card)
		or is_instance_valid(tidy_card)
		or is_instance_valid(cut_card)
		or is_instance_valid(command_card)
		or is_instance_valid(sound_card)
		or is_instance_valid(todo_card)
		or (is_instance_valid(errors_card) and errors_card.visible)
		or _has_variants()
	)


func _has_variants() -> bool:
	return variants != null and bool(variants.call("has_cards"))


func _sync() -> void:
	var working := agents_count()
	var testing := is_instance_valid(playtest) and bool(playtest.call("working"))
	var header: Object = dock.get("_header") if dock != null else null
	if header != null and header.has_method("show_agents"):
		header.call("show_agents", working + (1 if _main_busy else 0) + (1 if testing else 0))
	var composer: Object = dock.get("_composer") if dock != null else null
	if composer != null and composer.has_method("set_alongside"):
		var limit := int(bridge.get("parallel")) if bridge != null else 1
		# In another chat Send itself starts at once (chat_turns.gd): nothing to run alongside.
		var parked := dock != null and bool(dock.call("_parked"))
		composer.call("set_alongside", _main_busy and not parked and working < limit - 1)
	if working_count() == 0 and is_instance_valid(_offer):
		_drop_offer()


## The cards take at most CARDS_SHARE of the sidebar, then scroll. While they do, the newest
## card's primary action (its [code]pinned_actions()[/code] row: the to-do's Go, the playtest's
## Show all) moves to the strip under them, so it is never cut off; it goes back once they fit.
func _fit() -> void:
	if _fitting or _scroll == null or dock == null:
		return
	_fitting = true
	# Never over a page shown in the conversation's place (Chats, Settings).
	STYLE.show_part(_area, visible)
	var cap := dock.size.y * CARDS_SHARE
	var row := _action_row()
	var need := get_combined_minimum_size().y
	if _pinned != null:
		need += _pin.get_combined_minimum_size().y
	var keep := _pinned != null and row == _pinned and need > cap - PIN_SLACK
	var want: Control = row if need > cap or keep else null
	if want != _pinned:
		_unpin()
		if want != null:
			_pin_row(want)
	var below := _pin.get_combined_minimum_size().y if _pinned != null else 0.0
	var inner := get_combined_minimum_size().y
	var room := maxf(cap - below, 0.0)
	_scroll.custom_minimum_size.y = minf(inner, room)
	# Clip only while scrolling: a clip cuts the cards' shadows into a dark band.
	_scroll.clip_contents = inner > room
	_fitting = false


## The newest shown card's primary action row, if it has one to show.
func _action_row() -> Control:
	for index: int in range(get_child_count() - 1, -1, -1):
		var each := get_child(index) as Control
		if each == null or not each.visible or not each.has_method("pinned_actions"):
			continue
		var row: Variant = each.call("pinned_actions")
		if row is Control and (row as Control).visible:
			return row
	return null


func _pin_row(row: Control) -> void:
	var card: Control = row.get_parent() as Control
	while card != null and card.get_parent() != self:
		card = card.get_parent() as Control
	if card == null:
		return
	row.set_meta("_joinery_pin_home", [row.get_parent(), row.get_index()])
	row.reparent(_pin, false)
	_pinned = row
	_pinned_card = card
	# The action keeps the card's inner edge.
	var box := card.get_theme_stylebox("panel")
	var side := box.get_margin(SIDE_LEFT) if box != null else 0.0
	_pin.add_theme_constant_override("margin_left", int(side))
	_pin.add_theme_constant_override("margin_right", int(side))
	_pin.add_theme_constant_override("margin_top", UI_THEME.space(self, 1))
	_pin.add_theme_constant_override("margin_bottom", 0)
	if not card.tree_exiting.is_connected(_on_pinned_card_exiting):
		card.tree_exiting.connect(_on_pinned_card_exiting.bind(card))
	STYLE.show_part(_pin, true)


## The pinned row goes back where it was in its card.
func _unpin() -> void:
	var row := _pinned
	_pinned = null
	_pinned_card = null
	if _pin != null:
		STYLE.show_part(_pin, false)
	if not is_instance_valid(row):
		return
	var home: Array = row.get_meta("_joinery_pin_home", [])
	row.remove_meta("_joinery_pin_home")
	var parent: Node = home[0] if home.size() == 2 else null
	if not is_instance_valid(parent) or parent.is_queued_for_deletion():
		row.queue_free()
		return
	row.reparent(parent, false)
	parent.move_child(row, mini(int(home[1]), parent.get_child_count() - 1))


func _on_pinned_card_exiting(card: Control) -> void:
	if card != _pinned_card:
		return
	if card.is_queued_for_deletion():
		# Its action leaves with it.
		var row := _pinned
		_pinned = null
		_pinned_card = null
		STYLE.show_part(_pin, false)
		if is_instance_valid(row):
			row.queue_free()
	else:
		_unpin.call_deferred()
	_fit.call_deferred()
