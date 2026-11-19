# gdlint: disable=max-public-methods
@tool
extends VBoxContainer
## One request in the transcript, kept as a change card so the session reads as a history you
## can trace and revert. The user's bubble holds only the request text. Under it, on the
## same edges, Journeyman's side: the apps at work and a quiet timer, the live
## steps, the change when it is ready, and inline errors. A ready change shows a tiny picture,
## one line of what changed and Discard / Accept (sidebar_review_row.gd), so a small change is
## decided right here; a bigger one offers Review, which opens the Review panel. Either way the
## decision runs through the panel. The decision line ("Change accepted", with "Revert" on
## accepted file changes) is placed by the transcript at the end of the turn, after the reply
## ([signal outcome_placing]).
##
## Revert asks first, in place (change_revert.gd), and is not optimistic: "Reverting…" until
## Joinery confirms, then "Reverted" with Undo revert, or the error with Revert again. A change
## whose restore data is gone says "Too old to revert" ([method set_revertible]).
##
## When asset work needs the look approved first, the card holds the request (the art gate,
## sidebar_art_gate.gd): the current step reads "Waiting for the look", over a card with a
## miniature of the look, "Agree the look first", "Review the look" and "Describe my own".
## After approval the card leaves, the step reads "Look approved", and the dock sends the same
## request again, so nothing is retyped.
##
## The status line is the turn's one header in every state: the app glyph and name, then
## "· 0:14" while it runs and "· Completed in 1:12" with its disclosure chevron when done. Steps
## always sit below it, so only its content changes as the turn moves on. The status line is
## CONTROL_H.xs tall in every state, so the gap under the bubble never changes.
##
## A turn keeps one rhythm: the bubble, the progress (status line and steps), the thumbnail and
## the error are SPACE.3 apart (JoineryStack); rows inside the progress stay SPACE.1 apart.
##
## While the turn works its trail shows under the status line (sidebar_trail.gd) as ONE live
## line that replaces itself: the current step (with the clock), and under it, faint, the
## call's progress or the agent's newest short thought. Opening the folded line shows the
## whole trail: every call, thought, edit size, picture and test result.
##
## When the turn ends the steps fold away behind the reply's timing line ("Completed in 1:12"),
## which the transcript moves into the card's status line ([method adopt_timing]); opening it
## shows the steps again with the timing details.
##
## Under the done list: what was guessed and what isn't as asked (sidebar_departures.gd); under
## an accepted change's decision line, "Not played since this change" (sidebar_unplayed.gd).
##
## States: sending, failed (not sent), working, gated, ready, applying, accepted, rejected,
## reverted, stopped, done.

signal retry_requested
## The request could not be sent; [param reason] says why ("" when unknown).
signal failed(reason: String)
signal revert_requested(transaction_id: String)
## Undo revert: put the reverted change back; [param overwrite] replaces files edited since.
signal unrevert_requested(transaction_id: String, overwrite: bool)
## The decision line is about to show; the transcript moves [param row] to the end of the turn.
## Without a listener the row is shown under the card's own steps.
signal outcome_placing(row: Control)
## The ready change's row (picture, line, Discard / Accept) is about to show; the transcript
## moves [param row] after the reply, so the explanation is read before the decision. Without a
## listener the row stays under the card's own steps.
signal review_placing(row: Control)
## "Review the look" on the art gate.
signal gate_review_requested
## "Describe my own" on the art gate.
signal gate_describe_requested
## A file named on the work trail was pressed ([param path] is res://).
signal file_opened(path: String)

const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const ATTACHMENT_ROW := preload("sidebar_attachments.gd")
const REVIEW_ROW := preload("sidebar_review_row.gd")
const ART_GATE := preload("sidebar_art_gate.gd")
const DEPARTURES := preload("sidebar_departures.gd")
const UNPLAYED := preload("sidebar_unplayed.gd")
const TRAIL := preload("sidebar_trail.gd")
const WORDS := preload("sidebar_turn_words.gd")
const REFUSALS := preload("sidebar_refusals.gd")
const CHANGE_REVERT := preload("change_revert.gd")
const APP_BADGE := preload("sidebar_app_badge.gd")
## Apps recognised in step text; the card shows each one it sees once.
const APPS := ["Blender", "Krita", "GIMP", "Material Maker", "Surge XT", "SuperCollider", "Godot"]
## Shown instead of Revert once the change's restore data is gone.
const TOO_OLD := CHANGE_REVERT.TOO_OLD
## Step lines that only say the assistant is busy; the footer shows these, not the card.
const GENERIC_STEPS := ["Thinking…", "Preparing your request…", "Sending…"]
## The live line while the reply's words arrive ([method reply_started]).
const WRITING := "Writing the reply…"
## Outcome kind -> [icon, tint role]. "auto" is a change Joinery kept without asking
## (`[agents] auto_accept`): a quiet line, "Accepted automatically", with Undo.
const OUTCOMES := {
	"success": ["success", "success"],
	"auto": ["success", "text_muted"],
	"rejected": ["close", "text_muted"],
	"stopped": ["stop", "text_muted"],
	"reverted": ["reload", "text_muted"],
	"error": ["error", "danger"],
	"info": ["", "text_muted"],
}

var request := ""
var targets: Array = []
var state := "sending"
var transaction_id := ""
## Live scene edits kept without asking (review_flow.gd): Undo takes them back first, then
## reverts the files, if any. Returns "" or why it couldn't.
var undo_live := Callable()
## The change's name and file count (`joinery transactions list`) for Revert's question.
var change_summary := ""
var file_count := 0
## The RichTextLabel holding the request text (selectable).
var text_label: RichTextLabel
var _bubble: PanelContainer
var _activity: MarginContainer
var _column: VBoxContainer
## The status line, the failure line, the steps and the timing details: one tight group.
var _progress: VBoxContainer
var _status: HBoxContainer
var _apps: HBoxContainer
## " · " between the apps and the timer or timing line ("Blender · 0:14").
var _joiner: Label
var _timer: Label
var _sending: Control
var _failure: HBoxContainer
var _failure_text: Label
var _retry: Button
var _reason := ""
var _retry_allowed := true
## The work trail: steps, tool calls and thoughts, newest first.
var _steps: TRAIL
## Each part of the request and what was done about it, ticked (set_done).
var _done_list: VBoxContainer
var _departures: DEPARTURES
## "Not played since this change", under the decision line, and the ids it last heard.
var _unplayed := UNPLAYED.new()
var _unplayed_ids: Array = []
## The ready change: picture, one line, Discard / Accept or Review.
var _ready_row: REVIEW_ROW
var _outcome: HBoxContainer
var _outcome_icon: TextureRect
var _outcome_text: Label
## Revert, Undo revert, their progress and questions (change_revert.gd), then its parts.
var _line: CHANGE_REVERT
var _revert: Button
var _reverting: HBoxContainer
var _too_old: Label
var _gate: ART_GATE
## The "Waiting for the look" step while the gate holds the request.
var _gate_step: Control
var _error: Label
var _summary: Button
## "✓" leading the status line once the work is done.
var _done_mark: TextureRect
## The state [method finish] ended the turn in ("done", "stopped" or "failed"), "" before.
var _finished_as := ""
## The "Not run: …" lines of calls the project's gate refused ([method _refuse]).
var _refusals := REFUSALS.new()
## "5 steps · 0:14 ›": the finished steps folded while the change waits (until the reply's
## timing line takes over).
var _fold: Button
var _seconds := 0
var _details: Label
var _seen_apps: Array[String] = []
## app -> {call id (or step text): its state}: a badge shows while one of them didn't fail.
var _app_calls: Dictionary = {}
var _last_step := ""
var _previous_outcome: Array = []


func _init(text := "", context: Array = []) -> void:
	request = text
	targets = context.duplicate(true)
	theme_type_variation = "JoineryStack"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_bubble = PanelContainer.new()
	_bubble.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	# Not a card: a quiet rule on the leading edge, the words SPACE.3 after it, so the
	# request reads apart from the replies while only a decision is lifted.
	_bubble.theme_type_variation = "JoineryRequest"
	add_child(_bubble)
	text_label = RichTextLabel.new()
	text_label.fit_content = true
	text_label.scroll_active = false
	text_label.selection_enabled = true
	text_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	text_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text_label.accessibility_name = "Your request"
	text_label.text = request
	# Attached images sit under the text, as small thumbnails.
	var images := ATTACHMENT_ROW.strip(targets)
	if images == null:
		_bubble.add_child(text_label)
	else:
		var stack := VBoxContainer.new()
		stack.theme_type_variation = "JoineryTight"
		stack.add_child(text_label)
		stack.add_child(images)
		_bubble.add_child(stack)
	# The theme's paragraph and line spacing both land under the last line; drop the first and
	# match the second above the text, so the request sits in the middle of its bubble.
	STYLE.on_theme_overrides(
		text_label,
		func() -> void:
			text_label.add_theme_constant_override("paragraph_separation", 0)
			var lead := text_label.get_theme_constant("line_separation")
			var box := StyleBoxEmpty.new()
			box.content_margin_top = lead
			text_label.add_theme_stylebox_override("normal", box)
			text_label.add_theme_stylebox_override("focus", box)
	)
	# The assistant's side shares the bubble's edges, SPACE.3 under it.
	_activity = MarginContainer.new()
	add_child(_activity)
	_column = VBoxContainer.new()
	_column.theme_type_variation = "JoineryStack"
	_activity.add_child(_column)
	_progress = VBoxContainer.new()
	_progress.theme_type_variation = "JoineryTight"
	_column.add_child(_progress)
	_status = _row(_progress)
	_status.theme_type_variation = "JoineryRow"
	UI.bind_height(_status, "xs", false)
	_sending = INDICATOR.new()
	_sending.set("running", true)
	_sending.tooltip_text = "Sending"
	_status.add_child(_sending)
	_done_mark = _icon("success", "success")
	_done_mark.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_done_mark.hide()
	_status.add_child(_done_mark)
	_apps = HBoxContainer.new()
	_apps.theme_type_variation = "JoineryRowTight"
	_apps.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_apps.hide()
	_status.add_child(_apps)
	_joiner = UI.label("·", "JoineryCaption")
	_joiner.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_joiner.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_joiner.hide()
	_status.add_child(_joiner)
	_timer = UI.label("", "JoineryNumeric")
	_timer.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_timer.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_timer.accessibility_name = "Time spent on this request"
	_timer.hide()
	_status.add_child(_timer)
	_fold = UI.quiet_link("")
	_fold.name = "Fold"
	_fold.toggle_mode = true
	_fold.accessibility_name = "Show the steps"
	_fold.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI.bind_height(_fold, "xs")
	_fold.toggled.connect(
		func(open: bool) -> void:
			_show_all_steps(open)
			_steps.visible = open and _steps.has_lines()
			if _steps.visible:
				UI_MOTION.fade_in(_steps)
			_sync_activity()
	)
	_fold.hide()
	_status.add_child(_fold)
	_failure = _row(_progress)
	_failure.add_child(_icon("error", "danger"))
	_failure_text = UI.label("Not sent", "JoineryCaption", true)
	_failure.add_child(_failure_text)
	_retry = UI.link("Retry")
	_retry.accessibility_name = "Send this request again"
	_retry.pressed.connect(func() -> void: retry_requested.emit())
	UI.bind_height(_retry, "xs")
	_failure.add_child(_retry)
	STYLE.first_line(_failure, _failure.get_child(0), _failure_text)
	_failure.hide()
	_steps = TRAIL.new()
	_steps.file_opened.connect(func(path: String) -> void: file_opened.emit(path))
	_steps.hide()
	_progress.add_child(_steps)
	_progress.add_child(_refusals)
	_done_list = VBoxContainer.new()
	_done_list.theme_type_variation = "JoineryTight"
	_done_list.accessibility_name = "What was done"
	_done_list.hide()
	_column.add_child(_done_list)
	_departures = DEPARTURES.new()
	_departures.visibility_changed.connect(_sync_activity)
	_column.add_child(_departures)
	add_to_group(&"joinery_unplayed")
	_ready_row = REVIEW_ROW.new()
	_ready_row.other_ways_pressed.connect(_try_other_ways)
	_column.add_child(_ready_row)
	_error = UI.label("", "JoineryError", true)
	_error.hide()
	_column.add_child(_error)
	# The decision line lives outside the card until the transcript places it.
	_outcome = HBoxContainer.new()
	_outcome.theme_type_variation = "JoineryRowTight"
	_outcome.accessibility_name = "Decision"
	_outcome_icon = STYLE.glyph()
	_outcome.add_child(_outcome_icon)
	# A kept change's notes sit under its line; they wrap in a narrow sidebar. A wrapping
	# label never trims: wrapping with an ellipsis sizes it to no lines at all.
	_outcome_text = UI.label("", "JoineryCaption", true)
	_outcome.add_child(_outcome_text)
	_line = CHANGE_REVERT.new()
	_line.home = _column
	_line.revert.pressed.connect(_on_revert)
	_line.revert_confirmed.connect(_do_revert)
	_line.unrevert_requested.connect(
		func(overwrite: bool) -> void:
			_error.hide()
			_sync_activity()
			unrevert_requested.emit(transaction_id, overwrite)
	)
	_outcome.add_child(_line)
	_revert = _line.revert
	_reverting = _line.working
	_too_old = _line.too_old
	_outcome.hide()
	_set_sending_look(true)


func _notification(what: int) -> void:
	if what == NOTIFICATION_PREDELETE:
		if is_instance_valid(_line):
			_line.close_ask()
		if is_instance_valid(_outcome) and _outcome.get_parent() == null:
			_outcome.free()
		if is_instance_valid(_ready_row) and _ready_row.get_parent() == null:
			_ready_row.free()
		if is_instance_valid(_unplayed) and _unplayed.get_parent() == null:
			_unplayed.free()


## Controls whose text follows the transcript text size, as [control, TYPE step] pairs.
func scalables() -> Array:
	return [[text_label, "body"], [_outcome_text, "caption"], [_failure_text, "caption"]]


## The assistant acknowledged the request.
func set_sent() -> void:
	if state != "sending" and state != "failed":
		return
	state = "working"
	_set_sending_look(false)
	_failure.hide()


## The request never reached the assistant: show "Not sent" with Retry.
func set_failed(reason := "") -> void:
	state = "failed"
	_set_sending_look(false)
	text_label.theme_type_variation = "JoineryFaintRichText"
	_reason = reason
	_sync_failure()
	UI_MOTION.fade_in(_failure)
	_sync_activity()
	failed.emit(reason)


## True for a request that never reached the assistant ("Not sent"), not a turn that ran and
## ended failed ([method finish]); both are in the "failed" state.
func not_sent() -> bool:
	return state == "failed" and _finished_as != "failed"


## Whether Retry is offered. While the connection banner shows its own fix, the card says
## only "Not sent" and Retry waits until the assistant is back.
func set_retry_allowed(on: bool) -> void:
	_retry_allowed = on
	_sync_failure()


func _sync_failure() -> void:
	var why := _reason if _retry_allowed else ""
	_failure_text.text = "Not sent" if why.is_empty() else "Not sent. " + why
	_failure_text.tooltip_text = why
	_retry.visible = _retry_allowed


## Back to sending after Retry, or when the art gate lets a held request go.
func set_sending() -> void:
	if state == "gated":
		_look_approved()
	state = "sending"
	_failure.hide()
	_set_sending_look(true)


## A progress line from the assistant: the previous step gets its tick and steps aside, this
## one is the live line. Apps named in the text join the status line. [param detail] is the
## whole text on hover (a reply folded into a step keeps its words there).
func add_step(text: String, detail := "") -> void:
	set_sent()
	var line := text.strip_edges()
	_note_apps(line)
	_sync_activity()
	if line.is_empty() or line == _last_step or line in GENERIC_STEPS:
		return
	_last_step = line
	# While a tool call runs, its progress is the quiet line under it.
	if detail.is_empty() and _steps.on_progress(line):
		_sync_activity()
		return
	_steps.add_line(line, detail)
	_show_all_steps(false)
	_steps.show()
	_sync_activity()


## The reply's first words: while the live line still says it thinks ("Thinking through your
## request…"), it gets its tick and the live line says the reply is being written. Words between
## steps leave the steps as they are (they become the trail's thoughts).
func reply_started() -> void:
	if state == "working" and _last_step.begins_with("Thinking") and shows_step(_last_step):
		add_step(WRITING)


## A test run as it goes (joinery/test_progress.py): the live line says the test it checks
## now; its line in the trail ends as the run's summary with every result under it.
func add_tests(event: Dictionary) -> void:
	if state in ["sending", "failed"]:
		set_sent()
	_steps.add_tests(event)
	if not (_fold.visible or _summary != null):
		_show_all_steps(false)
		_steps.visible = _steps.has_lines()
	_sync_activity()


## A line of the work trail from the host: {id, text, state, note, file, added, removed,
## picture} (bridge/trail.py). Apps it names join the status line. A call the project's gate
## refused ({kind: "tool_activity", allowed: false, reason, call_id}) goes to [method _refuse].
func add_trail(event: Dictionary) -> void:
	if state in ["sending", "failed"]:
		set_sent()
	if event.get("kind") == "tool_activity":
		_refuse(event)
		return
	_note_apps(str(event.get("text", "")), str(event.get("id", "")), str(event.get("state", "")))
	_steps.add_call(event)
	if _fold.visible or _summary != null:
		_sync_activity()
		return
	_show_all_steps(false)
	_steps.visible = _steps.has_lines()
	_sync_activity()


## A refused call never reads as done: its line ends with the stop mark, and one quiet line on
## the card says why (sidebar_refusals.gd).
func _refuse(event: Dictionary) -> void:
	if bool(event.get("allowed", true)):
		return
	_steps.refuse(str(event.get("call_id", "")))
	_refusals.add(event)
	_word_refusals()
	_sync_activity()


## An app named in [param line] joins the status line. Its badge shows only while the app did
## something: once every step naming it failed ([param state] of its call [param id]), as when
## the app isn't enabled, the badge goes, so the status line never credits an app with nothing.
func _note_apps(line: String, id := "", state := "") -> void:
	for app: String in APPS:
		if not line.contains(app):
			continue
		if not _seen_apps.has(app):
			_seen_apps.append(app)
			var badge := APP_BADGE.new(app)
			badge.name = app
			_apps.add_child(badge)
		var calls: Dictionary = _app_calls.get_or_add(app, {})
		calls[id if not id.is_empty() else line] = state
		var did := calls.values().any(func(each: String) -> bool: return each != "failed")
		(_apps.get_node(NodePath(app)) as Control).visible = did
	_apps.visible = _apps.get_children().any(func(badge: Control) -> bool: return badge.visible)


## The whole trail ([param all], when the folded line is opened), or the newest lines.
func _show_all_steps(all: bool) -> void:
	_steps.show_all(all)


## The trail's lines on show, newest first, for tests.
func visible_steps() -> PackedStringArray:
	return _steps.texts() if _steps.visible else PackedStringArray()


## True while [param text] is the live line on this card (or the progress under it), so
## nothing else needs to repeat it.
func shows_step(text: String) -> bool:
	return state == "working" and _steps.visible and _steps.shows(text.strip_edges())


## Take the reply's timing line ([param summary], a disclosure button) and its [param details]:
## the summary joins the status line after the apps, and the finished steps fold behind it.
func adopt_timing(summary: Button, details: Label) -> void:
	if _summary == summary:
		_word_refusals()
		return
	_summary = summary
	_details = details
	_fold.hide()
	summary.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	summary.reparent(_status, false)
	_status.move_child(summary, _joiner.get_index() + 1)
	details.reparent(_progress, false)
	_progress.move_child(details, _steps.get_index() + 1)
	set_details_open(false)
	_word_refusals()


## A turn that ended done with a call refused never reads "Completed" with a tick: its timing
## line says how many steps were not run, behind the neutral stop mark. A failed turn keeps the
## error mark and "Failed after".
func _word_refusals() -> void:
	if _refusals.count() == 0 or _finished_as != "done":
		return
	STYLE.set_glyph(_done_mark, OUTCOMES["stopped"][0], OUTCOMES["stopped"][1])
	if _summary != null:
		_summary.text = WORDS.with_refusals(_summary.text, _refusals.count())


## Show or fold the finished steps and timing details under the summary; [param animate]
## fades the steps in.
func set_details_open(open: bool, animate := false) -> void:
	_show_all_steps(open)
	_steps.visible = open and _steps.has_lines()
	if _steps.visible and animate:
		UI_MOTION.fade_in(_steps)
	if _details != null:
		_details.visible = open and not _details.text.is_empty()
	_sync_activity()


## Re-check which parts show, after the transcript hid or showed the summary.
func refresh_layout() -> void:
	_sync_activity()


## Seconds spent so far; the timer appears after the first second, so a silent wait shows it
## is still going. Each second only its text changes: no motion that pulls the eye from the work.
func set_elapsed(seconds: int) -> void:
	if state == "working":
		_seconds = seconds
	if seconds < 1 or state != "working":
		return
	_timer.text = UI_MOTION.format_clock(seconds)
	if not _timer.visible:
		UI_MOTION.fade_in(_timer)
		_sync_activity()


## The turn ended. [param outcome] is "done", "cancelled" or "failed": the card ends "done",
## "stopped" or "failed", and the mark leading its status line is the tick, the stop or the
## error. A request held by the art gate stays held.
func finish(outcome: String) -> void:
	if state == "sending":
		set_failed()
		return
	if state == "gated":
		_timer.hide()
		_tick_current("success")
		_sync_activity()
		return
	_timer.hide()
	var kind := "success" if outcome == "done" else "stopped" if outcome == "cancelled" else "error"
	_tick_current(kind)
	if state == "working":
		state = {"success": "done", "stopped": "stopped"}.get(kind, "failed")
		_finished_as = state
		STYLE.set_glyph(_done_mark, OUTCOMES[kind][0], OUTCOMES[kind][1])
		_word_refusals()
	_sync_activity()


## A change is waiting for a decision. [param media] is the review's media list (its first
## "after" image is the picture until the panel has one); [param review] is the Review panel
## (review_panel.gd) that decides it, kept from an earlier call when null.
func set_ready(media: Array = [], review: Object = null) -> void:
	set_sent()
	_timer.hide()
	_tick_current("success")
	state = "ready"
	_fold_steps()
	_error.hide()
	_outcome.hide()
	_place_review()
	_ready_row.waiting = true
	_ready_row.show_for(media, review)
	_sync_activity()


## The run is done: its steps fold into "5 steps · 0:14 ›" on the status line.
func _fold_steps() -> void:
	var count := _steps.step_count()
	if count == 0 or _summary != null:
		return
	var words := "%d step%s" % [count, "" if count == 1 else "s"]
	if _seconds >= 3:
		words += " · " + UI_MOTION.format_clock(_seconds)
	_fold.text = words + "  ›"
	_fold.tooltip_text = "Show the steps"
	_fold.set_pressed_no_signal(false)
	_steps.hide()
	_fold.show()


## The agent's list of what it did for each part of the request ("Made the mug in Blender",
## "Put it on Table"), shown under the steps as quiet ticked lines. An empty list hides it.
func set_done(items: Array) -> void:
	for child: Node in _done_list.get_children():
		_done_list.remove_child(child)
		child.queue_free()
	for item: Variant in items:
		var line := str(item).strip_edges()
		if line.is_empty():
			continue
		var row := _row(_done_list)
		row.add_child(_icon("check", "text_muted"))
		var label := UI.label(line, "JoineryCaption", true)
		label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row.add_child(label)
	_done_list.visible = _done_list.get_child_count() > 0
	if _done_list.visible:
		UI_MOTION.fade_in(_done_list)
	_sync_activity()


## The lines of the done list, in order.
func done_lines() -> PackedStringArray:
	var lines := PackedStringArray()
	for row: Node in _done_list.get_children():
		lines.append((row.get_child(1) as Label).text)
	return lines


## What the agent [param guessed] and where it did something [param not_as_asked], with why:
## one compact line that opens to each (sidebar_departures.gd). Both empty hide it.
func set_departures(guessed: Array, not_as_asked: Array) -> void:
	_departures.set_lines(guessed, not_as_asked)


## The changes nobody has played since ({ids}, played_watch.gd), to mark this one if accepted.
func show_unplayed(data: Dictionary) -> void:
	_unplayed_ids = (data.get("ids") as Array).duplicate() if data.get("ids") is Array else []
	_sync_unplayed()


func unplayed_shown() -> bool:
	return _unplayed.visible


func _sync_unplayed() -> void:
	var waiting := state == "accepted" and transaction_id in _unplayed_ids
	if _unplayed.sync(waiting, _outcome, _column):
		_sync_activity()


## "Try other ways": a few more versions of this request beside this change (sidebar_variants.gd).
func _try_other_ways() -> void:
	if is_inside_tree() and not transaction_id.is_empty():
		get_tree().call_group(
			&"joinery_variants", &"try_other_ways", request, transaction_id, _ready_row.line_text()
		)


## The ready change's row in this card (picture, line, Discard / Accept).
func review_row() -> REVIEW_ROW:
	return _ready_row


## Show the decision. [param kind]: "success", "auto", "rejected", "stopped", "error" or
## "info". With [param optimistic], the previous look is kept so [method roll_back] can restore
## it.
func set_outcome(kind: String, text: String, optimistic := false) -> void:
	set_sent()
	_previous_outcome = [state, _ready_row.visible] if optimistic else []
	_timer.hide()
	_ready_row.waiting = false
	_ready_row.hide()
	_error.hide()
	var kept := kind in ["success", "auto"]
	_tick_current("success" if kept else "stopped")
	var ends := {"rejected": "rejected", "stopped": "stopped", "reverted": "reverted"}
	state = "accepted" if kept else str(ends.get(kind, "done"))
	_line.close_ask()
	var spec: Array = OUTCOMES.get(kind, OUTCOMES["info"])
	_outcome_icon.visible = not str(spec[0]).is_empty()
	if _outcome_icon.visible:
		STYLE.set_glyph(_outcome_icon, spec[0], spec[1])
	_outcome_text.text = text
	# Kept without asking, the one action reads Undo; it is the same revert.
	_revert.text = "Undo" if kind == "auto" else "Revert"
	_revert.accessibility_name = (
		"Undo this change" if kind == "auto" else "Revert this accepted change"
	)
	# Revert only once the accept is confirmed: an accept still on its way may yet roll back.
	_sync_revert(kept and not optimistic)
	_place_outcome()
	_outcome.show()
	_sync_activity()
	if kind == "success":
		UI_MOTION.pulse(_outcome)
	else:
		UI_MOTION.fade_in(_outcome)
	_sync_unplayed()


## Undo an optimistic outcome after the action failed, and say why under the card.
func roll_back(message := "") -> void:
	if _previous_outcome.is_empty():
		return
	state = str(_previous_outcome[0])
	_ready_row.waiting = bool(_previous_outcome[1])
	_ready_row.visible = _ready_row.waiting
	_previous_outcome = []
	_outcome.hide()
	show_error(message)


## An inline error under the card ("" hides it).
func show_error(message: String) -> void:
	_error.text = message
	if message.is_empty():
		_error.hide()
	else:
		UI_MOTION.fade_in(_error)
	_sync_activity()


## True while the card is waiting for a decision in the Review panel.
func awaiting_decision() -> bool:
	return state in ["ready", "applying"]


## Settle a decision reported by the editor. An optimistic outcome of the same kind is kept
## (no second animation); otherwise the outcome is shown now. Returns false when the card is
## not waiting for a decision, or a stop arrives after the work finished.
func decide(kind: String, text: String) -> bool:
	var target: String = (
		{"success": "accepted", "auto": "accepted", "rejected": "rejected", "stopped": "stopped"}
		. get(kind, "done")
	)
	if not _previous_outcome.is_empty() and state == target:
		_previous_outcome = []
		_outcome_text.text = text
		_sync_revert(kind in ["success", "auto"])
		return true
	# The card already says it ("Change accepted"): a second report of the same decision is
	# not shown again as a notice under it.
	if state == target and _outcome.visible and _outcome_text.text == text:
		return true
	var open := awaiting_decision() or not _previous_outcome.is_empty()
	if kind == "stopped":
		open = state in ["sending", "working"]
	if not open:
		return false
	set_outcome(kind, text)
	return true


## The revert finished. On success the line reads "Reverted" with Undo revert; on failure it
## stays accepted, offers Revert again and shows why in place.
func revert_finished(ok: bool, message := "") -> void:
	if ok:
		state = "reverted"
		_sync_unplayed()
		_settle_line("Reverted", "reload", "text_muted")
		return
	_sync_revert(true)
	show_error("Couldn't revert. " + message if not message.is_empty() else "Couldn't revert.")


## Undo revert finished: "Restored" with Revert again, the question about files edited since
## the revert ([param conflict], named in [param message]), or why it couldn't.
func unrevert_finished(ok: bool, message := "", conflict: Array = []) -> void:
	if ok:
		state = "accepted"
		_line.revertible = true
		_settle_line("Restored", "success", "success")
		_sync_unplayed()
		return
	_sync_revert(false)
	if not conflict.is_empty():
		_line.ask_conflict(message)
	else:
		var why := "Couldn't undo the revert."
		show_error(why + " " + message if not message.is_empty() else why)


func _settle_line(words: String, icon: String, role: String) -> void:
	_outcome_text.text = words
	STYLE.set_glyph(_outcome_icon, icon, role)
	_outcome_icon.show()
	_sync_revert(state == "accepted")
	_sync_activity()
	UI_MOTION.pulse(_outcome)


## This change as `joinery transactions list` reports it ({summary, file_count, …}).
func describe(entry: Dictionary) -> void:
	change_summary = str(entry.get("summary", change_summary))
	file_count = int(entry.get("file_count", file_count))


## Whether this change can still be reverted (`revertible` from `joinery transactions
## list`). A change that can't shows "Too old to revert" instead of the button.
func set_revertible(on: bool) -> void:
	_line.revertible = on
	if state == "accepted" and not _line.busy():
		_sync_revert(true)


## True while a revert, or an Undo revert, of this change is running.
func reverting() -> bool:
	return _line.busy()


## Hold the request until the look is approved. [param app] names the app that was about to
## make something ("Blender"), or "" for the one the card saw at work; [param look] is the
## miniature's data ({palette, references}).
func show_art_gate(_app := "", look: Dictionary = {}) -> void:
	set_sent()
	state = "gated"
	_timer.hide()
	_tick_current("success")
	if _gate == null:
		_gate = ART_GATE.new()
		_gate.review_requested.connect(func() -> void: gate_review_requested.emit())
		_gate.describe_requested.connect(func() -> void: gate_describe_requested.emit())
		_column.add_child(_gate)
	_gate.show_look(look)
	if _gate_step == null:
		_gate_step = _steps.add_gate("Waiting for the look")
		_steps.show()
	_column.move_child(_gate, _column.get_child_count() - 1)
	_gate.show()
	UI_MOTION.fade_in(_gate)
	_sync_activity()


## True while the art gate holds this request.
func gated() -> bool:
	return state == "gated"


## The art gate's heading ("" when there is no gate).
func gate_text() -> String:
	return _gate.heading() if _gate != null and _gate.visible else ""


## The look was approved: the gate's card leaves and its step reads "Look approved".
func _look_approved() -> void:
	if _gate != null and _gate.visible:
		UI_MOTION.exit(_gate, null, Callable(), false)
	if _gate_step != null:
		_steps.end_gate(_gate_step, "Look approved")
		_gate_step = null


## Revert, "Reverting…" or "Too old to revert" on the accepted line; Undo revert on a
## reverted one.
func _sync_revert(accepted: bool) -> void:
	var file_change := accepted and not transaction_id.is_empty()
	var live := accepted and undo_live.is_valid()
	_line.sync(file_change, live, state == "reverted" and not transaction_id.is_empty())


## Revert asks first, in place (change_revert.gd); only its answer runs it (_do_revert).
func _on_revert() -> void:
	if state == "accepted" and not _line.busy():
		_line.ask_revert(display_name(), file_count)


## The change's name: its summary, else the request, else the ready row's line.
func display_name() -> String:
	if not change_summary.is_empty():
		return change_summary
	return request if not request.strip_edges().is_empty() else _ready_row.line_text()


## Undo revert: put the change back as it was accepted (the button, or the toast's Undo).
func undo_revert() -> void:
	if state == "reverted" and not _line.busy():
		_line.start_unrevert(false)


func _do_revert() -> void:
	# Not optimistic: the line says "Reverting…" until Joinery confirms (revert_finished).
	if state != "accepted" or _line.busy():
		return
	_error.hide()
	if undo_live.is_valid():
		var why: String = undo_live.call()
		if not why.is_empty():
			revert_finished(false, why)
			return
		undo_live = Callable()
		if transaction_id.is_empty():
			revert_finished(true)
			return
	_line.start("Reverting…")
	_sync_activity()
	revert_requested.emit(transaction_id)


func _set_sending_look(on: bool) -> void:
	_sending.set("running", on)
	# Faint through the theme, not alpha, so the pending text keeps its contrast.
	text_label.theme_type_variation = "JoineryFaintRichText" if on else ""
	_sync_activity()


## The ready row goes where the transcript puts it (after the reply), or under the steps.
func _place_review() -> void:
	if _ready_row.get_parent() != _column:
		return
	_column.remove_child(_ready_row)
	review_placing.emit(_ready_row)
	var placed := _ready_row.get_parent()
	if placed == null:
		_column.add_child(_ready_row)
		_column.move_child(_ready_row, maxi(_progress.get_index(), _done_list.get_index()) + 1)
		return
	# While it waits it stays the turn's last line: whatever the turn adds later goes above it.
	placed.child_entered_tree.connect(
		func(added: Node) -> void:
			if added != _ready_row and _ready_row.get_parent() == placed:
				placed.move_child.call_deferred(_ready_row, -1)
	)


## The decision line goes where the transcript puts it (after the reply), or under the steps.
func _place_outcome() -> void:
	if _outcome.get_parent() != null:
		return
	outcome_placing.emit(_outcome)
	if _outcome.get_parent() == null:
		_column.add_child(_outcome)


## The assistant's side shows only when something in it does; the status line likewise.
func _sync_activity() -> void:
	_place_timer()
	var timed := _summary != null and _summary.visible
	var clock := _timer.visible and _timer.get_parent() == _status
	var ended := state in ["accepted", "reverted", "done"] or state == _finished_as
	_done_mark.visible = _fold.visible or (timed and ended)
	_joiner.visible = _apps.visible and (timed or clock or _fold.visible)
	_status.visible = _sending.visible or _apps.visible or clock or timed or _fold.visible
	_progress.visible = _any_visible(_progress)
	_activity.visible = _any_visible(_column)


## While Journeyman works, the clock rides at the end of the live step's line; before the first
## step, and once the steps fold, it is back on the status line.
func _place_timer() -> void:
	var live := _steps.live_line()
	var inline := live != null and _steps.visible and not _fold.visible
	var target: Node = live if inline else _status
	if _timer.get_parent() == target:
		return
	_timer.get_parent().remove_child(_timer)
	target.add_child(_timer)
	if target == _status:
		_status.move_child(_timer, _fold.get_index())


static func _any_visible(parent: Node) -> bool:
	for child: Node in parent.get_children():
		if child is CanvasItem and (child as CanvasItem).visible:
			return true
	return false


func _tick_current(kind: String) -> void:
	_steps.settle(kind)


## "Exporting the model…" -> "Exported the model" (sidebar_trail.gd).
static func past_tense(text: String) -> String:
	return TRAIL.past_tense(text)


func _icon(icon_name: String, role: String) -> TextureRect:
	var picture := STYLE.glyph(role)
	STYLE.set_glyph(picture, icon_name, role)
	return picture


func _row(parent: Control) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	parent.add_child(row)
	return row
