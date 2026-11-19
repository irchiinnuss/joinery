# gdlint: disable=max-public-methods
@tool
extends ScrollContainer
## The conversation. Each request is a change card (sidebar_change_card.gd); replies are plain
## text on the dock surface; Joinery's own lines are quiet notices. Turns are SPACE.5 apart;
## inside a turn the request card and the reply are SPACE.3 apart (JoineryStack) in every
## state, and notices sit SPACE.2 below the reply.
## Message rows keep their plain source text; streaming redraws at most once a frame.
##
## The transcript's content spans the sidebar's gutter to gutter (UI_THEME.GUTTER), sharing
## the left and right edges of the header and composer. Its scrollbar is always reserved and
## sits in the dock's right gutter, past that edge, so bubbles keep one right edge with or
## without a bar. The bottom edge fades instead of cutting messages. Bold text is the body
## family at a semibold weight and the same size, never a separate display font.
##
## Short notes ("Rewound") are a transient caption over the bottom inset
## ([method show_caption]). They take no layout height: the dock's rows stay put, and while a
## caption shows the conversation gets that much room at its end, so the newest line scrolls
## clear of it instead of hiding under it.
## Offers ([method add_offer]) are one quiet line with their actions; a feature plan
## ([method add_plan]) is a card that changes nothing until the user presses Apply.
## Messages queued while Journeyman works wait in [member queue] (sidebar_queue.gd), after
## the last turn, and stay at the end as the conversation grows.
## New output (a streaming reply, the work trail, a card) keeps the end in view only while the
## user is at or near the bottom; scrolled up, they stay put and a calm "New" pill offers the
## way down (chat_follow.gd). Sending a message always follows.
## A reply's right-click menu has "Remember this" (chat_remember.gd).

signal example_chosen(text: String)
signal retry_requested(card: Control)
signal revert_requested(transaction_id: String)
## Undo revert on a change card; [param overwrite] replaces files edited since the revert.
signal unrevert_requested(transaction_id: String, overwrite: bool)
signal remember_requested(text: String)
## Connect (or "Which one?") on the empty chat's blankslate, shown while no agent is connected.
signal connect_requested

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const CARD := preload("sidebar_change_card.gd")
const FEATURE_PLAN := preload("feature_plan_card.gd")
const QUEUE := preload("sidebar_queue.gd")
const INDICATOR := preload("activity_indicator.gd")
const REMEMBER := preload("chat_remember.gd")
const EMPTY := preload("sidebar_empty.gd")
const FOLLOW := preload("chat_follow.gd")
const NOTICES := preload("chat_notices.gd")
const MARKDOWN := preload("chat_markdown.gd")
const WORDS := preload("sidebar_turn_words.gd")
const NO_AGENT := "No agent connected"
## Notice kind -> [icon ("" for none), tint role].
const NOTICE_ICONS := {
	"success": ["success", "success"],
	"rejected": ["close", "text_muted"],
	"stopped": ["close", "text_muted"],
	"info": ["", "text_muted"],
	"error": ["error", "danger"],
}

## Queued messages, after every turn and before the caption's room.
var queue: QUEUE
var _body: VBoxContainer
var _empty: EMPTY
var _turn: VBoxContainer
var _messages: Array[Dictionary] = []
var _cards: Array[Control] = []
var _log := PackedStringArray()
var _last_notice := ""
var _dirty := false
## Keeps the end in view while the user is at the bottom (chat_follow.gd).
var _follower := FOLLOW.new()
var _text_scale := 1.0
var _show_timings := true
var _scaled: Array = []
var _retry_allowed := true
var _caption: PanelContainer
## Room at the end of the conversation while the caption shows.
var _caption_room: Control
var _caption_text: Label
var _caption_action: Button
var _caption_callback: Callable
var _caption_serial := 0


func _ready() -> void:
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	vertical_scroll_mode = ScrollContainer.SCROLL_MODE_RESERVE
	follow_focus = true
	_body = VBoxContainer.new()
	_body.theme_type_variation = "JoinerySections"
	_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(_body)
	_build_empty()
	queue = QUEUE.new()
	queue.added.connect(_on_queue_added)
	_body.add_child(queue)
	_follower.watch(self)
	UI_MOTION.rubber_band(self)
	STYLE.on_theme_overrides(self, _apply_fade)
	STYLE.scroll_in_gutter(self)
	# Messages and cards share one centred column with the composer (MAX_W.column).
	STYLE.centered_column(self)
	resized.connect(_on_resized)
	_build_caption()


func _notification(what: int) -> void:
	if what == NOTIFICATION_THEME_CHANGED and _body != null:
		_rescale()


## Width the scrollbar takes beside the content, reserved whether or not it shows.
func gutter() -> int:
	var bar := get_v_scroll_bar()
	return int(bar.get_minimum_size().x) + get_theme_constant("scrollbar_h_separation")


## A fade over the bottom edge (and the top once scrolled), in the dock's ground colour.
func _apply_fade() -> void:
	if not "scroll_hint_mode" in self:
		return
	var gradient := Gradient.new()
	gradient.set_color(0, Color.WHITE)
	gradient.set_color(1, Color(1, 1, 1, 0))
	var texture := GradientTexture2D.new()
	texture.gradient = gradient
	texture.fill_from = Vector2.ZERO
	texture.fill_to = Vector2(0, 1)
	texture.width = 4
	texture.height = maxi(UI_THEME.space(self, 5), 1)
	add_theme_icon_override("scroll_hint_vertical", texture)
	add_theme_color_override("scroll_hint_vertical_color", get_theme_color("surface", "Joinery"))
	set("scroll_hint_mode", 1)


## When the view gets shorter (a banner opened above it), keep the newest message in sight.
func _on_resized() -> void:
	_empty.fit(size.y)
	if _follower.following:
		_follower.to_end()


func _build_empty() -> void:
	_empty = EMPTY.new()
	_empty.example_chosen.connect(func(text: String) -> void: example_chosen.emit(text))
	_empty.connect_requested.connect(func() -> void: connect_requested.emit())
	_body.add_child(_empty)
	for pair: Array in _empty.scalables():
		_scale(pair[0], pair[1])


## The empty view follows the connection: the examples act only while connected, and without
## an agent it becomes the Connect blankslate (sidebar_empty.gd).
func set_connection(state: String) -> void:
	_empty.set_connection(state)
	# A conversation on screen keeps it: a quiet line over its end offers Connect instead.
	if state == "none" and not _empty.visible:
		show_caption(NO_AGENT, 3600.0, "Connect", func() -> void: connect_requested.emit())
	elif state != "none" and caption_text() == NO_AGENT:
		show_caption("")


## What the empty chat starts from: the message [param targets] in the open [param scene].
func set_start_context(targets: Array, scene := "") -> void:
	_empty.set_context(targets, scene)


## A message. "You" starts a new turn with a change card; anyone else is a reply.
func add_message(who: String, text: String) -> void:
	if who == "You":
		add_request(text)
		return
	_fold_interim(who)
	_hide_empty()
	var inset := _inset(_current_turn())
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	inset.add_child(column)
	# The reply's text, then a box per code block (chat_markdown.gd), SPACE.3 apart.
	var body := VBoxContainer.new()
	body.add_theme_constant_override("separation", UI_THEME.space(self, 3))
	column.add_child(body)
	var label := _reply_label(who)
	body.add_child(label)
	var summary := UI.quiet("")
	summary.icon_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	summary.alignment = HORIZONTAL_ALIGNMENT_LEFT
	summary.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	summary.theme_type_variation = "JoineryQuietButton"
	UI.bind_height(summary, "xs")
	STYLE.hug_left(summary)
	summary.hide()
	column.add_child(summary)
	var details := UI.label("", "JoineryNumeric", true)
	details.hide()
	column.add_child(details)
	summary.pressed.connect(_toggle_details.bind(summary, details))
	_scale(summary, "caption")
	_scale(details, "caption")
	(
		_messages
		. append(
			{
				"who": who,
				"text": text,
				"label": label,
				"body": body,
				"parts": [label],
				"panel": null,
				"summary": summary,
				"details": details,
				"dirty": true,
			}
		)
	)
	_dirty = true


## A label for [param who]'s reply: selectable, scaled, with "Remember this".
func _reply_label(who := "Journeyman") -> RichTextLabel:
	var label := RichTextLabel.new()
	_configure_text(label)
	# A reply's blocks (paragraphs, headings, list items) sit SPACE.3 apart (chat_markdown.gd).
	label.add_theme_constant_override("paragraph_separation", UI_THEME.space(self, 3))
	REMEMBER.offer(label, remember_requested)
	label.accessibility_name = who + " message"
	return label


## A reply another follows while the turn still works was a step, not the answer: it becomes
## the card's live activity line (its words on hover), so only the last reply stays as text.
func _fold_interim(who: String) -> void:
	var card := current_card()
	if _messages.is_empty() or _messages[-1]["who"] != who or card == null:
		return
	if card.state != "working" or _messages[-1]["panel"] != null:
		return
	var record: Dictionary = _messages.pop_back()
	(record["body"] as Control).get_parent().queue_free()
	var words := str(record["text"]).replace("**", "").replace("`", "").strip_edges()
	if not words.is_empty():
		card.add_step(words.get_slice("\n", 0), words)


## Start a turn with the user's request and return its change card.
func add_request(text: String, targets: Array = []) -> Control:
	_hide_empty()
	_turn = VBoxContainer.new()
	_turn.theme_type_variation = "JoineryStack"
	_add_turn(_turn)
	var card := CARD.new(text, targets)
	card.set_retry_allowed(_retry_allowed)
	_turn.add_child(card)
	card.retry_requested.connect(func() -> void: retry_requested.emit(card))
	card.outcome_placing.connect(_place_outcome.bind(_turn))
	card.review_placing.connect(_place_outcome.bind(_turn))
	card.revert_requested.connect(func(id: String) -> void: revert_requested.emit(id))
	card.unrevert_requested.connect(
		func(id: String, overwrite: bool) -> void: unrevert_requested.emit(id, overwrite)
	)
	for pair: Array in card.scalables():
		_scale(pair[0], pair[1])
	_cards.append(card)
	(
		_messages
		. append(
			{
				"who": "You",
				"text": text,
				"label": card.text_label,
				"panel": card,
				"summary": null,
				"details": null,
				"dirty": false,
			}
		)
	)
	_last_notice = ""
	_follower.follow()
	_dirty = true
	return card


## Take back a request that never started, with its turn (it waits in the queue, chat_turns.gd).
func remove_request(card: Control) -> void:
	_cards.erase(card)
	for message: Dictionary in _messages.duplicate():
		if message.get("panel") == card:
			_messages.erase(message)
	if card.get_parent() == _turn:
		_turn = null
	card.get_parent().queue_free()
	_dirty = true


## Whether unsent cards offer Retry (not while a connection banner owns the fix).
func set_retry_allowed(on: bool) -> void:
	_retry_allowed = on
	for card: Control in _cards:
		if is_instance_valid(card):
			card.set_retry_allowed(on)


## The card of the latest request, or null.
func current_card() -> Control:
	return _cards[-1] if not _cards.is_empty() else null


func append_delta(who: String, text: String, new_message: bool) -> void:
	if new_message or _messages.is_empty() or _messages[-1]["who"] != who:
		add_message(who, "")
	_messages[-1]["text"] += text
	_messages[-1]["dirty"] = true
	_dirty = true


func finish_response() -> void:
	pass


## A line from Joinery itself, not a chat message. Kinds: success, rejected, stopped, info,
## error, divider, policy (a Rule Zero stop). Consecutive identical notices are dropped.
func add_notice(text: String, kind := "info") -> void:
	if text.is_empty() or text + kind == _last_notice:
		return
	_last_notice = text + kind
	_log.append("Joinery  " + text)
	_hide_empty()
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	row.accessibility_name = "Status: " + text
	if kind in ["divider", "policy"]:
		_inset(_current_turn()).add_child(NOTICES.special(kind, text, example_chosen.emit))
		_dirty = true
		return
	var spec: Array = NOTICE_ICONS.get(kind, NOTICE_ICONS["info"])
	# A plain note has no icon, and no empty slot where one would be.
	if not str(spec[0]).is_empty():
		var picture := STYLE.glyph(spec[1])
		STYLE.set_glyph(picture, spec[0], spec[1])
		row.add_child(picture)
	var label := UI.label(text, "JoineryCaption", true)
	row.add_child(label)
	_scale(label, "caption")
	if row.get_child_count() > 1:
		STYLE.first_line(row, row.get_child(0), label)
	var lines := text.split("\n", true, 1)
	if lines.size() > 1:
		# One line, and the whole story behind its ›: "Change not applied · … ›".
		NOTICES.expandable(row, label, lines[0], lines[1])
	_inset(_current_turn()).add_child(row)
	UI_MOTION.fade_in(row)
	_dirty = true


## The request failed ([param text] is why): one line in the error style, never as [param _who]'s
## reply, with Retry for the request. A policy stop is never retried (chat_notices.gd).
func add_error(_who: String, text: String) -> void:
	var shown := _log.size()
	var policy := text.begins_with(NOTICES.POLICY_STOP)
	add_notice(text, "policy" if policy else "error")
	var card := current_card()
	if _log.size() == shown or policy or card == null or not _retry_allowed:
		return
	NOTICES.retry(_inset(_current_turn()).get_child(-1), retry_requested.emit.bind(card))


## The outcome of a review or a turn ("Change accepted", "Stopped"). It is shown on
## [param card] when that card is waiting for it, otherwise as a notice.
func add_decision(text: String, kind: String, card: Control) -> void:
	if card != null and is_instance_valid(card) and card.decide(kind, text):
		if text + kind != _last_notice:
			_log.append("Joinery  " + text)
		_last_notice = text + kind
		_dirty = true
		return
	add_notice(text, kind)


## A card's ready row and decision line end its turn: after the reply and its timing line.
func _place_outcome(row: Control, turn: VBoxContainer) -> void:
	if is_instance_valid(turn) and turn.is_inside_tree():
		_inset(turn).add_child(row)


## The card whose transaction is [param transaction_id], or null.
func card_for(transaction_id: String) -> Control:
	for card: Control in _cards:
		if is_instance_valid(card) and card.transaction_id == transaction_id:
			return card
	return null


## Show the turn's timing line ("Completed in 0:12"). It goes into the request's card, where the
## finished steps fold behind it; a turn without a card keeps it under the reply. Returns false
## when the turn has no reply yet.
func show_timing(event: Dictionary) -> bool:
	if _messages.is_empty() or _messages[-1]["who"] == "You":
		return false
	var record: Dictionary = _messages[-1]
	var seconds := float(event.get("total_ms", 0)) / 1000.0
	var summary: Button = record["summary"]
	summary.text = WORDS.timing_line(str(event.get("outcome", "")), seconds)
	summary.tooltip_text = "Show timing details"
	summary.accessibility_name = "Show response timing details"
	if not summary.has_meta("_joinery_icon"):
		UI.bind_icon(summary, "disclosure_closed")
	summary.add_theme_font_override("font", UI_THEME.numeric_font(self))
	summary.visible = _show_timings
	var lines := PackedStringArray()
	if not str(event.get("model", "")).is_empty():
		var route := [str(event["model"]), str(event.get("mode", "auto")).capitalize()]
		if not str(event.get("effort", "")).is_empty():
			route.append(str(event["effort"]).capitalize())
		lines.append(" · ".join(route))
	if event.get("first_response_ms") != null:
		lines.append("First response " + WORDS.duration(float(event["first_response_ms"]) / 1000.0))
	for phase: String in event.get("phases_ms", {}):
		var phase_seconds := float(event["phases_ms"][phase]) / 1000.0
		lines.append("%s %s" % [phase.capitalize(), WORDS.duration(phase_seconds)])
	record["details"].text = "\n".join(lines)
	var card := current_card()
	if card != null and card.get_parent() == _turn:
		summary.set_meta("_joinery_card", card)
		summary.set_meta("_joinery_open", false)
		UI.bind_icon(summary, "disclosure_closed")
		card.adopt_timing(summary, record["details"])
	return true


func set_text_scale(scale: float) -> void:
	_text_scale = scale
	_rescale()


func set_show_timings(on: bool) -> void:
	_show_timings = on
	for record: Dictionary in _messages:
		if record["summary"] == null:
			continue
		var summary: Button = record["summary"]
		summary.visible = on and not summary.text.is_empty()
		if not on:
			record["details"].hide()
		var card: Variant = summary.get_meta("_joinery_card", null)
		if card is Control and is_instance_valid(card):
			if not on:
				card.set_details_open(false)
				summary.set_meta("_joinery_open", false)
				UI.bind_icon(summary, "disclosure_closed")
			card.refresh_layout()


func get_parsed_text() -> String:
	var lines := PackedStringArray()
	for record: Dictionary in _messages:
		lines.append(str(record["who"]) + "  " + str(record["text"]))
	lines.append_array(_log)
	return "\n\n".join(lines)


## Every notice shown so far, in order (including decisions shown on cards).
func notice_texts() -> PackedStringArray:
	var texts := PackedStringArray()
	for line: String in _log:
		texts.append(line.trim_prefix("Joinery  "))
	return texts


func _toggle_details(summary: Button, details: Label) -> void:
	var open := not bool(summary.get_meta("_joinery_open", details.visible))
	summary.set_meta("_joinery_open", open)
	UI.bind_icon(summary, "disclosure_open" if open else "disclosure_closed")
	var card: Variant = summary.get_meta("_joinery_card", null)
	if card is Control and is_instance_valid(card):
		# The card shows its steps and the details together, fading them in.
		card.set_details_open(open, true)
	if open:
		if not details.text.is_empty():
			UI_MOTION.fade_in(details)
	else:
		details.hide()


## Empty the conversation for another chat: every turn, reply and notice goes and the empty
## state shows again. Queued messages stay.
func clear() -> void:
	for child: Node in _body.get_children():
		if child != _empty and child != queue and child != _caption_room:
			_body.remove_child(child)
			child.queue_free()
	_messages.clear()
	_cards.clear()
	_log.clear()
	_turn = null
	_last_notice = ""
	_empty.show()
	_follower.follow()


func _hide_empty() -> void:
	if _empty != null and _empty.visible:
		_empty.hide()


func _current_turn() -> VBoxContainer:
	if _turn == null:
		_turn = VBoxContainer.new()
		_turn.theme_type_variation = "JoineryStack"
		_add_turn(_turn)
	return _turn


## Turns go before the queued messages and the caption's room.
func _add_turn(turn: VBoxContainer) -> void:
	_body.add_child(turn)
	if queue != null:
		_body.move_child(turn, queue.get_index())


## A message was queued: its text follows the text size and the newest line stays in sight.
func _on_queue_added(card: Control) -> void:
	_hide_empty()
	for pair: Array in card.scalables():
		_scale(pair[0], pair[1])
	_follower.follow()
	_dirty = true


## The column replies and notices share in a turn. Like cards and bubbles, it spans the
## sidebar's gutter to gutter (UI_THEME.GUTTER): there is no second, inner text edge.
func _inset(turn: VBoxContainer) -> VBoxContainer:
	if turn.get_child_count() > 0 and turn.get_child(-1).has_meta("joinery_inset"):
		return turn.get_child(-1).get_child(0)
	var inset := MarginContainer.new()
	inset.set_meta("joinery_inset", true)
	turn.add_child(inset)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryRow"
	inset.add_child(column)
	return column


func _configure_text(label: RichTextLabel) -> void:
	label.fit_content = true
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.scroll_active = false
	label.selection_enabled = true
	label.bbcode_enabled = true
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_scale(label, "body")


## Keep [param control]'s text at the TYPE step [param type_name] times the text size setting.
func _scale(control: Control, type_name: String) -> void:
	_scaled.append([control, type_name])
	if is_inside_tree():
		_apply_scale(control, type_name)


func _rescale() -> void:
	var live: Array = []
	for pair: Array in _scaled:
		if is_instance_valid(pair[0]):
			_apply_scale(pair[0], pair[1])
			live.append(pair)
	_scaled = live
	_dirty = true


func _apply_scale(control: Control, type_name: String) -> void:
	var size := roundi(UI_THEME.font_size(self, type_name) * _text_scale)
	if control is RichTextLabel:
		for key: String in ["normal_font_size", "bold_font_size", "mono_font_size"]:
			control.add_theme_font_size_override(key, size)
		_semibold(control as RichTextLabel)
	else:
		control.add_theme_font_size_override("font_size", size)


## Bold is the body font's semibold weight, so emphasis changes weight only.
func _semibold(label: RichTextLabel) -> void:
	# The transcript is in the tree even when the label is not yet, so it resolves the theme.
	label.add_theme_font_override("bold_font", UI_THEME.medium_font(self))


func _process(_delta: float) -> void:
	if _caption != null and _caption.visible:
		_place_caption()
	if not _dirty or _body == null:
		return
	_dirty = false
	for record: Dictionary in _messages:
		if record["dirty"]:
			MARKDOWN.render(record["body"], record["parts"], str(record["text"]), _reply_label)
			record["dirty"] = false
	if _follower.following:
		_follower.to_end()


# --- Transient caption ---------------------------------------------------------------------------


## A short note over the bottom inset ("Rewound") that clears after
## [param seconds]. [param action] adds a link that runs [param callback]. It takes no layout
## height: it floats over the transcript's faded bottom edge.
func show_caption(text: String, seconds := 3.0, action := "", callback := Callable()) -> void:
	_caption_serial += 1
	var serial := _caption_serial
	_caption_text.text = text
	_caption_text.tooltip_text = text
	_caption_action.text = action
	_caption_action.visible = not action.is_empty() and callback.is_valid()
	_caption_callback = callback
	if text.is_empty():
		_hide_caption()
		return
	_place_caption()
	_body.move_child(_caption_room, -1)
	_caption_room.custom_minimum_size.y = _caption.size.y + UI_THEME.space(self, 2)
	if _follower.following:
		_follower.to_end()
	if not _caption.visible:
		UI_MOTION.fade_in(_caption)
	if is_inside_tree():
		get_tree().create_timer(seconds).timeout.connect(
			func() -> void:
				if serial == _caption_serial:
					_hide_caption()
		)


## The caption's text ("" when hidden).
func caption_text() -> String:
	return _caption_text.text if _caption.visible else ""


func _build_caption() -> void:
	_caption_room = Control.new()
	_caption_room.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_body.add_child(_caption_room)
	_caption = PanelContainer.new()
	_caption.top_level = true
	_caption.z_index = 1
	_caption.mouse_filter = Control.MOUSE_FILTER_PASS
	_caption.accessibility_name = "Note"
	_caption.hide()
	STYLE.on_theme_overrides(
		_caption,
		func() -> void:
			var box := StyleBoxFlat.new()
			box.bg_color = _caption.get_theme_color("surface_raised", "Joinery")
			box.set_corner_radius_all(UI_THEME.radius(_caption, "md"))
			var pad := float(UI_THEME.space(_caption, 2))
			box.content_margin_left = pad
			box.content_margin_right = pad
			box.content_margin_top = float(UI_THEME.space(_caption, 1))
			box.content_margin_bottom = float(UI_THEME.space(_caption, 1))
			_caption.add_theme_stylebox_override("panel", box)
	)
	# Internal, so the scroll container keeps laying out only the conversation.
	add_child(_caption, false, Node.INTERNAL_MODE_BACK)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	_caption.add_child(row)
	_caption_text = UI.label("", "JoineryCaption")
	# The note keeps its full width: the caption grows to fit it, up to the transcript's.
	_caption_text.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_caption_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(_caption_text)
	_caption_action = UI.link("")
	_caption_action.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI.bind_height(_caption_action, "xs")
	_caption_action.pressed.connect(
		func() -> void:
			if _caption_callback.is_valid():
				_caption_callback.call()
			_hide_caption()
	)
	row.add_child(_caption_action)


## Bottom of the visible transcript, on the content's left edge, left of the scrollbar.
func _place_caption() -> void:
	var inset := float(UI_THEME.space(self, 2))
	var width := maxf(0.0, size.x - gutter())
	var wanted := _caption.get_combined_minimum_size()
	_caption.size = Vector2(minf(maxf(wanted.x, 0.0), width), wanted.y)
	var origin := get_global_rect().position
	_caption.global_position = origin + Vector2(0.0, size.y - _caption.size.y - inset)


func _hide_caption() -> void:
	_caption_serial += 1
	_caption_room.custom_minimum_size.y = 0.0
	if _caption.visible:
		UI_MOTION.exit(_caption)


# --- Offers and plans ----------------------------------------------------------------------------


## One quiet line from Joinery with its actions, as [[label, callable], …]; the first is the
## accent link. Each action runs its callable; the line stays until [method remove_offer].
func add_offer(text: String, actions: Array = []) -> Control:
	_hide_empty()
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	row.accessibility_name = "Suggestion: " + text
	row.set_meta("joinery_offer", text)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(column)
	var label := UI.label(text, "JoineryCaption", true)
	column.add_child(label)
	_scale(label, "caption")
	if not actions.is_empty():
		# In a narrow sidebar the links flow onto the next line.
		var links := HFlowContainer.new()
		STYLE.on_theme_overrides(
			links,
			func() -> void:
				links.add_theme_constant_override("h_separation", UI_THEME.space(links, 2))
				links.add_theme_constant_override("v_separation", UI_THEME.space(links, 1))
		)
		column.add_child(links)
		for action: Array in actions:
			# The first action is the offer; the rest (Not now, Dismiss) stay quiet.
			var first := links.get_child_count() == 0
			var button := UI.link(str(action[0])) if first else UI.quiet_link(str(action[0]))
			button.accessibility_name = str(action[0])
			UI.bind_height(button, "xs")
			STYLE.hug_left(button)
			var callback: Callable = action[1]
			button.pressed.connect(func() -> void: callback.call())
			links.add_child(button)
	_inset(_current_turn()).add_child(row)
	_log.append("Joinery  " + text)
	UI_MOTION.fade_in(row)
	_dirty = true
	return row


## Take an offer line away.
func remove_offer(row: Control) -> void:
	if is_instance_valid(row):
		UI_MOTION.exit(row, null, row.queue_free)


## Every offer line still shown, oldest first.
func offer_texts() -> PackedStringArray:
	var texts := PackedStringArray()
	for row: Node in _body.find_children("*", "HBoxContainer", true, false):
		if row.has_meta("joinery_offer") and not row.is_queued_for_deletion():
			texts.append(str(row.get_meta("joinery_offer")))
	return texts


## A feature plan from `joinery feature … --plan --json` as a card: what it adds, which
## settings change, and notes. Nothing changes until Apply (feature_plan_card.gd).
func add_plan(plan: Dictionary, title: String) -> FEATURE_PLAN:
	_hide_empty()
	_turn = VBoxContainer.new()
	_turn.theme_type_variation = "JoineryStack"
	_add_turn(_turn)
	var card := FEATURE_PLAN.new()
	card.build(plan, title)
	_turn.add_child(card)
	for pair: Array in card.scalables():
		_scale(pair[0], pair[1])
	_log.append("Joinery  " + title)
	UI_MOTION.fade_in(card)
	_last_notice = ""
	_dirty = true
	return card
