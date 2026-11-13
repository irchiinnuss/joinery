@tool
extends VBoxContainer
## The art direction as a sidebar page, shown in place of the chat like Settings and Chats:
## a back arrow and the title, whether this version is approved, then the rules read as a
## document. Top to bottom: the update offer for a first-format bible, what to fix first (one
## line each, from the bible checks), the rules people decide (Feel, Look, Palette, Sound,
## Never) in one card, with the sections not written yet named on one "Still open" line, the
## references board, and the technical rules folded away. "Approve these rules", the page's
## one accent action, stays at the bottom with what approving does.
##
## Only a click on Approve approves: nothing else on the page, and nothing an agent sends,
## reaches [member _on_approve]. Asking Journeyman fills the message box and goes back to the
## chat; changes to the rules come back as reviewed changes, then need approving again.
##
## Opening the page (/art, the art gate's "Review the look", Settings) shows it at once: the
## last rules the sidebar heard, or "Reading the art direction…" until the bridge answers.
## Without a sidebar to hold it, art_review_dialog.gd shows the same rules in a window.

signal closed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const DIALOG := preload("art_review_dialog.gd")
const TITLE := "Art direction"
const APPROVE := "Approve these rules"
const CONSEQUENCE := (
	"Approving lets Journeyman make art and sound that follows these rules. "
	+ "If they change, you'll be asked again."
)
const APPROVED := "Approved. Art and sound Journeyman makes follows these rules."
const LOADING := "Reading the art direction…"
const NO_ANSWER := "Journeyman didn't answer. Check that it's connected, then try again."
## Seconds to wait for the bridge before saying it didn't answer.
const WAIT := 15.0
const META := "joinery_art_page"

var dock: Control
## The art review event on screen ({text, sha256, approved, version, references,
## suggestions}); empty while loading.
var event: Dictionary = {}
## Runs when the user clicks Approve, and only then.
var _on_approve := Callable()
var _scroll: ScrollContainer
var _body: VBoxContainer
var _state: Label
var _content: VBoxContainer
var _waiting: Control
var _waiting_text: Label
var _indicator: Control
var _retry: Button
var _foot: VBoxContainer
var _consequence: Label
var _approve: Button
var _match: Button
## Bumped on every open, so a stale wait timer does nothing.
var _ticket := 0


## Add the hidden page to [param sidebar]'s content column, opening on its
## art_review_requested signal. Null when the sidebar has no content column.
static func attach(sidebar: Control) -> Control:
	if sidebar == null:
		return null
	if sidebar.has_meta(META):
		return sidebar.get_meta(META)
	var content: Variant = sidebar.get("_content")
	if not content is Container:
		return null
	var page: Control = load("res://addons/joinery/art_page.gd").new()
	page.dock = sidebar
	page.hide()
	page.closed.connect(page.set_open.bind(false))
	(content as Container).add_child(page)
	sidebar.set_meta(META, page)
	if sidebar.has_signal("art_review_requested"):
		sidebar.connect("art_review_requested", page.open_waiting)
	return page


## Show [param review] (an art review event) on [param sidebar]'s page, with
## [param on_approve] for its Approve. False when there is no page to show it on.
static func present(sidebar: Control, review: Dictionary, on_approve: Callable) -> bool:
	var page := attach(sidebar)
	if page == null:
		return false
	page.call("show_review", review, on_approve)
	return true


func _ready() -> void:
	name = "ArtDirection"
	accessibility_name = TITLE
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	theme_type_variation = "JoineryRow"
	UI_THEME.watch(self)
	_scroll = ScrollContainer.new()
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(_scroll)
	UI_MOTION.rubber_band(_scroll)
	_body = VBoxContainer.new()
	_body.theme_type_variation = "JoinerySections"
	_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_scroll.add_child(_body)
	_build_title()
	_waiting = _build_waiting()
	_body.add_child(_waiting)
	_content = VBoxContainer.new()
	_content.name = "Content"
	_content.theme_type_variation = "JoinerySections"
	_body.add_child(_content)
	STYLE.page_edges(_scroll, _body)
	_build_foot()


## Show or hide the page in place of the conversation, restoring what was there.
func set_open(open: bool) -> void:
	var content := get_parent()
	if content == null:
		visible = open
		return
	for child: Node in content.get_children():
		if child == self or not child is CanvasItem:
			continue
		var item := child as CanvasItem
		if open:
			if not item.has_meta("before_art"):
				item.set_meta("before_art", item.visible)
			item.visible = false
		elif item.has_meta("before_art"):
			item.visible = item.get_meta("before_art")
			item.remove_meta("before_art")
	if not open:
		# Opened again, the page waits for the bridge's answer rather than show old rules
		# with an Approve that no longer means anything.
		event = {}
		_on_approve = Callable()
	if open and not visible:
		_scroll.scroll_vertical = 0
	visible = open
	if not open:
		_ticket += 1


## Open the page while the bridge reads the rules, saying so; and saying so too if no answer
## comes. Rules already on screen stay until the fresh copy replaces them.
func open_waiting() -> void:
	if not (visible and not event.is_empty()):
		_show_waiting(LOADING, true)
	set_open(true)
	_ticket += 1
	var ticket := _ticket
	if is_inside_tree():
		get_tree().create_timer(WAIT).timeout.connect(
			func() -> void:
				if ticket == _ticket and visible and event.is_empty():
					_show_waiting(NO_ANSWER, false)
		)


## Show [param review] with [param on_approve] for Approve, and open the page.
func show_review(review: Dictionary, on_approve: Callable) -> void:
	var reopened := visible and not event.is_empty()
	var scroll := _scroll.scroll_vertical
	set_open(true)
	_on_approve = on_approve
	_fill(review)
	if reopened:
		# A fresher copy of the rules on screen: keep the reader's place.
		_scroll.set_deferred("scroll_vertical", scroll)
	else:
		UI_MOTION.stagger(_content.get_children())


func _fill(review: Dictionary) -> void:
	event = review.duplicate(true)
	_waiting.hide()
	for child: Node in _content.get_children():
		_content.remove_child(child)
		child.queue_free()
	var approved := bool(event.get("approved", false))
	_state.text = "Approved" if approved else "Draft, not approved yet"
	_state.show()
	if int(event.get("version", 2)) == 1:
		_content.add_child(_upgrade_offer())
	var offers := _offers(event.get("suggestions", []))
	if offers != null:
		_content.add_child(offers)
	var parts := DIALOG.sections(str(event.get("text", "")))
	_content.add_child(_rules(parts))
	_content.add_child(_board(event.get("references", [])))
	_content.add_child(_technical(parts))
	_consequence.text = APPROVED if approved else CONSEQUENCE
	_approve.visible = not approved
	_approve.disabled = not _on_approve.is_valid()
	_match.visible = approved and dock != null and dock.has_method("run_command")
	_foot.show()


func _show_waiting(text: String, running: bool) -> void:
	event = {}
	for child: Node in _content.get_children():
		_content.remove_child(child)
		child.queue_free()
	_state.hide()
	_foot.hide()
	_waiting_text.text = text
	_indicator.call("set_running", running)
	_retry.visible = not running
	_waiting.show()


# --- Building -----------------------------------------------------------------------------------


func _build_title() -> void:
	var top := VBoxContainer.new()
	top.theme_type_variation = "JoineryTight"
	_body.add_child(top)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	top.add_child(row)
	var back := UI.icon_button("back", "Back to chat")
	back.name = "Back"
	back.accessibility_name = "Close the art direction and return to the chat"
	var escape := InputEventKey.new()
	escape.keycode = KEY_ESCAPE
	back.shortcut = Shortcut.new()
	back.shortcut.events = [escape]
	back.shortcut_in_tooltip = false
	back.pressed.connect(func() -> void: closed.emit())
	row.add_child(back)
	var title := UI.label(TITLE, "JoineryTitle")
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(title)
	_state = UI.label("", "JoineryCaption", true)
	_state.name = "State"
	top.add_child(_inset(_state))


func _build_waiting() -> Control:
	var group := VBoxContainer.new()
	group.name = "Waiting"
	group.theme_type_variation = "JoineryTight"
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	group.add_child(row)
	_indicator = INDICATOR.new()
	_indicator.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(_indicator)
	_waiting_text = UI.label("", "JoineryCaption", true)
	row.add_child(_waiting_text)
	_retry = UI.quiet_link("Try again")
	_retry.name = "Retry"
	_retry.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	STYLE.hug_left(_retry)
	_retry.pressed.connect(
		func() -> void:
			if dock != null and dock.has_signal("art_review_requested"):
				dock.emit_signal("art_review_requested")
	)
	group.add_child(_retry)
	group.hide()
	return group


## The bottom of the page, outside the scroll: what approving does, and Approve on the
## trailing edge (or, once approved, "Make the project match").
func _build_foot() -> void:
	_foot = VBoxContainer.new()
	_foot.name = "Foot"
	_foot.theme_type_variation = "JoineryRow"
	_foot.hide()
	add_child(_foot)
	_consequence = UI.label("", "JoineryCaption", true)
	_consequence.name = "Consequence"
	_foot.add_child(_consequence)
	_approve = UI.primary(APPROVE)
	_approve.name = "Approve"
	_approve.size_flags_horizontal = Control.SIZE_SHRINK_END
	_approve.pressed.connect(_on_approve_pressed)
	_foot.add_child(_approve)
	_match = UI.secondary("Make the project match")
	_match.name = "Match"
	_match.tooltip_text = "Fix what drifts from these rules, as reviewed changes (/match)"
	_match.size_flags_horizontal = Control.SIZE_SHRINK_END
	STYLE.filled(_match)
	_match.pressed.connect(
		func() -> void:
			closed.emit()
			dock.call("run_command", "/match")
	)
	_foot.add_child(_match)


## A human click on Approve: the one way this page approves.
func _on_approve_pressed() -> void:
	if event.is_empty() or bool(event.get("approved", false)):
		return
	var approve := _on_approve
	_on_approve = Callable()
	closed.emit()
	if approve.is_valid():
		approve.call()


func _upgrade_offer() -> Control:
	var card := _card()
	card.name = "Upgrade"
	var column: VBoxContainer = card.get_child(0)
	column.add_child(STYLE.card_title("These rules use an older layout", true))
	column.add_child(
		UI.label(
			"Updating keeps every line and adds the new sections. You'll approve it again after.",
			"JoineryCaption",
			true
		)
	)
	var update := UI.secondary("Update to the new layout")
	update.name = "Update"
	update.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	STYLE.filled(update)
	STYLE.on_card(update)
	update.disabled = dock == null or not dock.has_method("upgrade_bible")
	update.pressed.connect(
		func() -> void:
			closed.emit()
			dock.call("upgrade_bible")
	)
	column.add_child(update)
	return card


func _offers(suggestions: Array) -> Control:
	var lines: Array[String] = []
	for suggestion: Variant in suggestions:
		var text := DIALOG.offer_text(str(suggestion))
		if (
			text.is_empty()
			or DIALOG.OWN_OFFERS.any(func(own: String) -> bool: return text.begins_with(own))
		):
			continue
		lines.append(text)
	if lines.is_empty():
		return null
	var section := _section("To do first")
	section.name = "Offers"
	for text: String in lines.slice(0, DIALOG.MAX_OFFERS):
		var row := HBoxContainer.new()
		row.theme_type_variation = "JoineryRow"
		row.set_meta("offer", text)
		var label := UI.label(text, "", true)
		label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row.add_child(label)
		if _can_ask():
			var ask := UI.quiet_link("Ask")
			ask.name = "Ask"
			ask.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
			ask.tooltip_text = "Ask Journeyman about this"
			ask.accessibility_name = "Ask Journeyman: " + text
			STYLE.hug_right(ask)
			ask.pressed.connect(_ask.bind("Help me with the art direction. " + text))
			row.add_child(ask)
		section.body.add_child(row)
	return section


## The decided sections in one card, each its name over its words. The card ends on one
## line: the sections not written yet with one Ask to settle them, or, when all are written,
## one way to ask for a change.
func _rules(parts: Array) -> Control:
	var card := _card()
	card.name = "Rules"
	var column: VBoxContainer = card.get_child(0)
	column.theme_type_variation = "JoineryStack"
	var words := DIALOG.decided(parts)
	var open: Array[String] = []
	for spec: Array in DIALOG.SECTIONS:
		var title := str(spec[0])
		var text := str(words.get(title, ""))
		if text.is_empty():
			open.append(title)
			continue
		var item := VBoxContainer.new()
		item.theme_type_variation = "JoineryTight"
		item.add_child(STYLE.card_title(title))
		var body := _rich(DIALOG.to_bbcode(text))
		body.name = title
		item.add_child(body)
		column.add_child(item)
	if not open.is_empty():
		column.add_child(_still_open(open, open.size() == DIALOG.SECTIONS.size()))
	elif _can_ask():
		var ask := UI.quiet_link("Ask Journeyman to change it")
		ask.name = "AskChange"
		ask.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
		STYLE.hug_left(ask)
		ask.pressed.connect(_ask.bind("Change the art direction: "))
		column.add_child(ask)
	return card


## "Still open: Look, Palette, Sound" with Ask on its trailing edge, like a check's line.
func _still_open(open: Array[String], none: bool) -> Control:
	var row := HBoxContainer.new()
	row.name = "StillOpen"
	row.theme_type_variation = "JoineryRow"
	var names := ", ".join(open)
	var line := "Still open: " + names
	if none:
		line = "Nothing decided yet. Journeyman will ask about the feel, look, palette and sound."
	var label := UI.label(line, "JoineryCaption", true)
	label.name = "OpenLine"
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(label)
	if _can_ask():
		var ask := UI.quiet_link("Ask")
		ask.name = "AskOpen"
		ask.tooltip_text = "Ask Journeyman to help decide these"
		ask.accessibility_name = "Ask Journeyman to help decide: " + names
		ask.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		STYLE.hug_right(ask)
		ask.pressed.connect(_ask.bind("Help me decide the art direction: %s." % names.to_lower()))
		row.add_child(ask)
	return row


func _board(references: Array) -> Control:
	var section := _section("References")
	section.name = "References"
	var pictures := references.filter(func(item: Variant) -> bool: return item is Dictionary)
	if pictures.is_empty():
		section.body.add_child(
			UI.label(
				"None yet. Add your own photos, sketches or Krita boards to art/reference.",
				"JoineryCaption",
				true
			)
		)
		return section
	var board := HFlowContainer.new()
	board.name = "Board"
	STYLE.on_theme_overrides(
		board,
		func() -> void:
			board.add_theme_constant_override("h_separation", UI_THEME.space(board, 3))
			board.add_theme_constant_override("v_separation", UI_THEME.space(board, 3))
	)
	section.body.add_child(board)
	for reference: Dictionary in pictures:
		board.add_child(DIALOG.reference_tile(reference))
	return section


func _technical(parts: Array) -> Control:
	var section := UI.section("Technical rules", true)
	section.name = "Technical"
	section.body.theme_type_variation = "JoineryStack"
	section.body.add_child(
		UI.label(
			"Preset for you. Joinery checks every asset against these before you review it.",
			"JoineryCaption",
			true
		)
	)
	var rules := VBoxContainer.new()
	rules.name = "Rules"
	rules.theme_type_variation = "JoineryStack"
	section.body.add_child(rules)
	for part: Array in parts:
		var heading := str(part[0])
		if DIALOG.is_decided(heading) or heading.to_lower() == "references":
			continue
		var body := DIALOG.clean(str(part[1]))
		if body.is_empty():
			continue
		# Each rule its name over its words, like the decided ones; "(checked)" is for writers.
		var item := VBoxContainer.new()
		item.theme_type_variation = "JoineryTight"
		item.add_child(STYLE.card_title(heading.replace("(checked)", "").strip_edges(), true))
		item.add_child(_rich(DIALOG.to_bbcode(body)))
		rules.add_child(item)
	# A bible with no technical rules has nothing to unfold.
	section.visible = rules.get_child_count() > 0
	return section


# --- Helpers -----------------------------------------------------------------------------------


func _can_ask() -> bool:
	return dock != null and dock.has_method("fill_composer")


## Start a message with [param text] and go back to the chat to finish it.
func _ask(text: String) -> void:
	closed.emit()
	dock.call("fill_composer", text)


## A filled transcript card (no border) with a column inside.
func _card() -> PanelContainer:
	var card := PanelContainer.new()
	card.theme_type_variation = "JoineryChangeCard"
	STYLE.boxed(card, "JoineryChangeCard", 3)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryRow"
	card.add_child(column)
	return card


## A section under a JoinerySection heading, its heading on the page's text edge.
func _section(title: String) -> UI.Section:
	var section := UI.section(title)
	var header := section.header
	section.remove_child(header)
	section.add_child(_inset(header))
	section.move_child(section.get_child(-1), 0)
	return section


func _inset(control: Control) -> MarginContainer:
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	inset.add_child(control)
	return inset


static func _rich(bbcode: String) -> RichTextLabel:
	var content := RichTextLabel.new()
	content.bbcode_enabled = true
	content.fit_content = true
	content.scroll_active = false
	content.selection_enabled = true
	content.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	content.text = bbcode
	return content
