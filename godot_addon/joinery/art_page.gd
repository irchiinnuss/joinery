@tool
extends VBoxContainer
## The look as a sidebar page, shown in place of the chat like Settings and Chats: a back arrow
## and "The look", then the look itself as a moodboard (art_look.gd draws the parts). The palette
## strip and the reference pictures lead; then the feel as one sentence and tags, Never as chips,
## Sound, and the checked rules folded away. A section not decided yet shows "Not decided yet"
## with "Decide with me ›" in its own place; when nothing is decided the page is one blankslate.
## The sticky footer says Draft or "Approved · 12 Nov", with "Approve look" (the page's one
## accent action) or, once approved, "Make it match".
##
## Only a click on Approve approves: nothing else on the page, and nothing an agent sends,
## reaches [member _on_approve]. Asking Journeyman fills the message box and goes back to the
## chat; changes to the look come back as reviewed changes, then need approving again.
##
## A draft in the first layout is brought to the new one without asking (the dock's
## upgrade_bible, silently); an approved one keeps its layout until its next edit is approved.
##
## Opening the page (/art, the art gate's "Review the look", Settings) shows it at once: the
## last look the sidebar heard or, after a second without an answer, a skeleton of the page.
## Without a sidebar to hold it, art_review_dialog.gd shows the same look in a window.
##
## The same page shows the game brief (/brief, [method show_document]): read-only, each section
## its caption over its words, with "Ask Journeyman to change it" at its end. Edits to it are
## reviewed changes like any other; the brief has nothing to approve.

signal closed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const DIALOG := preload("art_review_dialog.gd")
const LOOK := preload("art_look.gd")
const BLOCKS := preload("ui_blocks.gd")
const TITLE := "The look"
const APPROVE := "Approve look"
const APPROVE_TIP := "Journeyman makes art and sound to match. Changes ask again."
const DRAFT := "Draft"
const NO_ANSWER := "Journeyman didn't answer. Check that it's connected, then try again."
## Seconds to wait for the bridge before saying it didn't answer.
const WAIT := 15.0
## Seconds before the skeleton shows: a quick answer shows nothing in between.
const SKELETON_AFTER := 1.0
## Below this column width (logical px; a 340 px sidebar) the Change links always show.
const NARROW := 340
## From this column width (logical px) the swatches are SWATCH_H.lg tall.
const WIDE := 600
const META := "joinery_art_page"

var dock: Control
## The art review event on screen ({text, sha256, approved, version, palette, references,
## gaps, suggestions, approved_on}); empty while loading.
var event: Dictionary = {}
## Runs when the user clicks Approve, and only then.
var _on_approve := Callable()
var _scroll: ScrollContainer
var _body: VBoxContainer
var _title: Label
var _content: VBoxContainer
var _waiting: VBoxContainer
var _waiting_text: Label
var _retry: Button
var _foot: PanelContainer
var _state: Label
var _approve: Button
var _match: Button
## Bumped on every open, so a stale wait timer does nothing.
var _ticket := 0
## The reference shown full width, or {} on the look.
var _expanded: Dictionary = {}


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


## Show the project document [param text] (Markdown) under [param title] on
## [param sidebar]'s page; "Ask Journeyman to change it" starts a message with [param ask].
## False when there is no page to show it on.
static func present_document(sidebar: Control, title: String, text: String, ask := "") -> bool:
	var page := attach(sidebar)
	if page == null:
		return false
	page.call("show_document", title, text, ask)
	return true


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
	theme_type_variation = "JoineryFlush"
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
	_body.resized.connect(_fit_width)
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
		# Opened again, the page waits for the bridge's answer rather than show an old look
		# with an Approve that no longer means anything.
		event = {}
		_on_approve = Callable()
		_expanded = {}
	if open and not visible:
		_scroll.scroll_vertical = 0
	visible = open
	if not open:
		_ticket += 1


## Open the page while the bridge reads the look: nothing for a second, then a skeleton of the
## page; and saying so if no answer comes. A look already on screen stays until the fresh copy
## replaces it.
func open_waiting() -> void:
	if not (visible and not event.is_empty()):
		_show_waiting()
	set_open(true)
	_ticket += 1
	var ticket := _ticket
	if is_inside_tree():
		get_tree().create_timer(SKELETON_AFTER).timeout.connect(
			func() -> void:
				if ticket == _ticket and visible and event.is_empty():
					_skeleton_in()
		)
		get_tree().create_timer(WAIT).timeout.connect(
			func() -> void:
				if ticket == _ticket and visible and event.is_empty():
					_no_answer()
		)


## Show [param text], a project document, read-only under [param title], and open the page.
func show_document(title: String, text: String, ask := "") -> void:
	set_open(false)
	set_open(true)
	_set_title(title)
	_clear()
	_waiting.hide()
	_foot.hide()
	_title.tooltip_text = "Journeyman reads this before every request. Changes to it are reviewed."
	var column := VBoxContainer.new()
	column.name = "Document"
	column.theme_type_variation = "JoineryStack"
	var parts := DIALOG.sections(text)
	if parts.is_empty():
		column.add_child(_rich(DIALOG.to_bbcode(text)))
	for part: Array in parts:
		var body := DIALOG.clean(str(part[1]))
		if body.is_empty():
			continue
		var item := VBoxContainer.new()
		item.theme_type_variation = "JoineryTight"
		item.add_child(LOOK.heading(str(part[0])))
		item.add_child(_rich(DIALOG.to_bbcode(body)))
		column.add_child(item)
	if not ask.is_empty() and _can_ask():
		var change := UI.quiet_link("Ask Journeyman to change it")
		change.name = "AskChange"
		change.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
		STYLE.hug_left(change)
		change.pressed.connect(_ask.bind(ask))
		column.add_child(change)
	_content.add_child(column)
	UI_MOTION.stagger(column.get_children())


## Show [param review] with [param on_approve] for Approve, and open the page.
func show_review(review: Dictionary, on_approve: Callable) -> void:
	var reopened := visible and not event.is_empty()
	var scroll := _scroll.scroll_vertical
	var skeleton := _waiting.get_node_or_null("Skeleton") as CanvasItem
	var loading := skeleton != null and skeleton.visible and _waiting.visible
	set_open(true)
	_ticket += 1
	_on_approve = on_approve
	_set_title(TITLE)
	_title.tooltip_text = ""
	_expanded = {}
	_fill(review)
	_upgrade_draft()
	if reopened:
		# A fresher copy of the look on screen: keep the reader's place.
		_scroll.set_deferred("scroll_vertical", scroll)
	elif loading:
		UI_MOTION.crossfade(skeleton, _content)
	else:
		UI_MOTION.stagger(_content.get_children())


func _fill(review: Dictionary) -> void:
	event = review.duplicate(true)
	_waiting.hide()
	_clear()
	var approved := bool(event.get("approved", false))
	var parts := DIALOG.sections(str(event.get("text", "")))
	var words := DIALOG.decided(parts)
	var refs := LOOK.references(event)
	var palette: Array = event.get("palette", [])
	var nothing := (
		refs.is_empty()
		and not LOOK.chosen(palette)
		and DIALOG.SECTIONS.all(
			func(spec: Array) -> bool: return str(words.get(spec[0], "")).is_empty()
		)
	)
	if nothing and not approved:
		_content.add_child(_blankslate())
		_foot.hide()
		return
	_content.add_child(_palette(palette))
	_content.add_child(_board(refs))
	_content.add_child(_feel(parts, str(words.get("Feel", "")) + str(words.get("Look", ""))))
	_content.add_child(_never(str(words.get("Never", ""))))
	_content.add_child(_sound(str(words.get("Sound", ""))))
	_content.add_child(_technical(parts))
	_sync_foot(approved)
	_fit_width.call_deferred()


## Footer: Draft or "Approved · 12 Nov" on the leading edge; Approve look (disabled with what
## to do first while a required section is open) or, once approved, Make it match.
func _sync_foot(approved: bool) -> void:
	var blocking := _blocking()
	if approved:
		var day := LOOK.short_date(str(event.get("approved_on", "")))
		_state.text = "Approved" + (" · " + day if not day.is_empty() else "")
	elif not blocking.is_empty():
		_state.text = (
			"Choose colours first" if blocking == "palette" else "Decide the %s first" % blocking
		)
	else:
		_state.text = DRAFT
	_approve.visible = not approved
	_approve.disabled = not _on_approve.is_valid() or not blocking.is_empty()
	_approve.tooltip_text = APPROVE_TIP
	_match.visible = approved and dock != null and dock.has_method("run_command")
	_foot.show()


## The first required section still open ("palette"), or "" when Approve can go ahead.
func _blocking() -> String:
	for gap: Variant in event.get("gaps", []):
		if gap is Dictionary and bool(gap.get("required", false)):
			var section := str(gap.get("section", gap.get("heading", "")))
			return section.to_lower().replace("_", " ")
	return ""


## A draft in the first layout is upgraded once, without asking; an approved one is not.
func _upgrade_draft() -> void:
	if (
		int(event.get("version", 2)) == 1
		and not bool(event.get("approved", false))
		and dock != null
		and dock.has_method("upgrade_bible")
	):
		dock.call("upgrade_bible", str(event.get("sha256", "")))


func _show_waiting() -> void:
	event = {}
	_set_title(TITLE)
	_clear()
	_foot.hide()
	_waiting.get_node("Skeleton").hide()
	_waiting.get_node("NoAnswer").hide()
	_waiting.show()


func _skeleton_in() -> void:
	var skeleton := _waiting.get_node("Skeleton") as CanvasItem
	skeleton.show()
	UI_MOTION.fade_in(skeleton)


func _no_answer() -> void:
	_waiting.get_node("Skeleton").hide()
	var line := _waiting.get_node("NoAnswer") as CanvasItem
	line.show()
	UI_MOTION.fade_in(line)


# --- Building -----------------------------------------------------------------------------------


func _build_title() -> void:
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	_body.add_child(row)
	var back := UI.icon_button("back", "Back to chat")
	back.name = "Back"
	back.accessibility_name = "Close the look and return to the chat"
	var escape := InputEventKey.new()
	escape.keycode = KEY_ESCAPE
	back.shortcut = Shortcut.new()
	back.shortcut.events = [escape]
	back.shortcut_in_tooltip = false
	back.pressed.connect(_back)
	row.add_child(back)
	_title = UI.label(TITLE, "JoineryTitle")
	_title.name = "Title"
	_title.mouse_filter = Control.MOUSE_FILTER_PASS
	_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(_title)


## Back leaves a full-size reference for the look; from the look, it returns to the chat.
func _back() -> void:
	if not _expanded.is_empty() and not event.is_empty():
		_expanded = {}
		var scroll := _scroll.scroll_vertical
		_fill(event)
		_scroll.set_deferred("scroll_vertical", scroll)
		return
	closed.emit()


func _build_waiting() -> VBoxContainer:
	var group := VBoxContainer.new()
	group.name = "Waiting"
	group.theme_type_variation = "JoineryStack"
	var skeleton := LOOK.skeleton()
	skeleton.hide()
	group.add_child(skeleton)
	var line := VBoxContainer.new()
	line.name = "NoAnswer"
	line.theme_type_variation = "JoineryTight"
	line.hide()
	group.add_child(line)
	_waiting_text = UI.label(NO_ANSWER, "JoineryCaption", true)
	line.add_child(_waiting_text)
	_retry = UI.quiet_link("Try again")
	_retry.name = "Retry"
	_retry.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	STYLE.hug_left(_retry)
	_retry.pressed.connect(
		func() -> void:
			if dock != null and dock.has_signal("art_review_requested"):
				dock.emit_signal("art_review_requested")
	)
	line.add_child(_retry)
	group.hide()
	return group


## The bottom of the page, outside the scroll, under a hairline: the state on the leading edge,
## the one action on the trailing edge, both in the page's column.
func _build_foot() -> void:
	_foot = PanelContainer.new()
	_foot.name = "Foot"
	_foot.hide()
	add_child(_foot)
	STYLE.on_theme_overrides(
		_foot,
		func() -> void:
			var box := StyleBoxFlat.new()
			box.bg_color = _foot.get_theme_color("surface", "Joinery")
			box.border_color = _foot.get_theme_color("border_subtle", "Joinery")
			box.border_width_top = maxi(1, roundi(UI_THEME.unit(_foot)))
			box.content_margin_top = UI_THEME.space(_foot, 3)
			box.content_margin_bottom = UI_THEME.space(_foot, 3)
			_foot.add_theme_stylebox_override("panel", box)
	)
	var row := HBoxContainer.new()
	row.name = "Row"
	row.theme_type_variation = "JoineryRow"
	_foot.add_child(row)
	STYLE.centered_column(row)
	_state = UI.label(DRAFT, "JoineryCaption")
	_state.name = "State"
	_state.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_state.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(_state)
	_approve = UI.primary(APPROVE)
	_approve.name = "Approve"
	_approve.pressed.connect(_on_approve_pressed)
	UI.bind_height(_approve, "md")
	row.add_child(_approve)
	_match = UI.secondary("Make it match")
	_match.name = "Match"
	_match.tooltip_text = "Make the project match the look, as reviewed changes (/match)"
	STYLE.filled(_match)
	UI.bind_height(_match, "md")
	_match.pressed.connect(
		func() -> void:
			closed.emit()
			dock.call("run_command", "/match")
	)
	row.add_child(_match)


## A human click on Approve: the one way this page approves.
func _on_approve_pressed() -> void:
	if event.is_empty() or bool(event.get("approved", false)):
		return
	var approve := _on_approve
	_on_approve = Callable()
	UI_MOTION.pulse(_foot)
	closed.emit()
	if approve.is_valid():
		approve.call()


## Nothing decided yet: one heading and one way to start.
func _blankslate() -> Control:
	var start := UI.primary("Start with Journeyman")
	start.name = "Start"
	start.disabled = not _can_ask()
	start.pressed.connect(
		_ask.bind("Help me decide how my game should look: the feel, palette and sound.")
	)
	var own := UI.quiet_link("Write my own")
	own.name = "WriteOwn"
	own.disabled = not _can_ask()
	own.pressed.connect(_ask.bind("Here's how my game should look: "))
	var slate := UI.blankslate("image", "No look yet", start, own)
	slate.custom_minimum_size.y = UI_THEME.space(self, 6) * 6
	return slate


## The hero: the palette as colour, or dashed swatches to choose them with Journeyman.
func _palette(palette: Array) -> Control:
	if not LOOK.chosen(palette):
		return LOOK.empty_palette(
			_ask.bind("Help me choose the palette for my game.") if _can_ask() else Callable()
		)
	var strip := UI.palette_strip(palette)
	strip.name = "Palette"
	var swatches: Control = strip.get_node("Swatches")
	swatches.connect("swatch_pressed", _swatch_popover.bind(swatches))
	return strip


## A swatch's popover: its role and hex, and "Change with Journeyman".
func _swatch_popover(index: int, swatches: Control) -> void:
	var roles: Array = swatches.get("roles")
	if index < 0 or index >= roles.size():
		return
	var role: Dictionary = roles[index]
	var popup := PopupPanel.new()
	popup.name = "SwatchPopover"
	popup.theme = UI_THEME.build(self)
	popup.add_theme_stylebox_override("panel", StyleBoxEmpty.new())
	var card := PanelContainer.new()
	card.theme_type_variation = "JoineryHoverCard"
	popup.add_child(card)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	card.add_child(column)
	var name_line := UI.label(str(role.get("role", "")), "JoineryCardTitle")
	column.add_child(name_line)
	var hex := UI.label(str(role.get("hex", "")), "JoineryCaption")
	hex.add_theme_font_override("font", UI_THEME.mono_font(self))
	column.add_child(hex)
	if _can_ask():
		var change := UI.quiet_link("Change with Journeyman")
		change.name = "Change"
		UI.bind_height(change, "xs")
		STYLE.hug_left(change)
		change.pressed.connect(
			func() -> void:
				popup.hide()
				_ask("Change the %s colour of the look: " % str(role.get("role", "")).to_lower())
		)
		column.add_child(change)
	popup.popup_hide.connect(popup.queue_free)
	add_child(popup)
	var rect := swatches.get_global_rect()
	var wide := rect.size.x / roles.size()
	var at := Vector2i(
		roundi(rect.position.x + wide * index), roundi(rect.end.y + UI_THEME.space(self, 1))
	)
	popup.popup(Rect2i(at + Vector2i(get_window().position), Vector2i.ZERO))
	UI_MOTION.enter(card, swatches)


## The reference pictures, a dashed "Add reference" tile last; a tile opens full width.
func _board(refs: Array) -> Control:
	var grid := UI.reference_grid(refs)
	grid.name = "References"
	for index: int in grid.get_child_count():
		var tile := grid.get_child(index) as Control
		tile.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
		tile.mouse_filter = Control.MOUSE_FILTER_STOP
		tile.gui_input.connect(_tile_input.bind(refs[index], tile))
	if _can_ask():
		grid.add_child(LOOK.add_tile(_ask.bind("Add a reference to the look: ")))
	return grid


func _tile_input(input: InputEvent, ref: Dictionary, tile: Control) -> void:
	var click := input as InputEventMouseButton
	if click != null and click.pressed and click.button_index == MOUSE_BUTTON_LEFT:
		tile.accept_event()
		_expand(ref, tile)


## [param ref] full width inside the page, its take and what to leave under it; Back returns.
func _expand(ref: Dictionary, from: Control = null) -> void:
	_expanded = ref
	_clear()
	var view := VBoxContainer.new()
	view.name = "Expanded"
	view.theme_type_variation = "JoineryStack"
	_content.add_child(view)
	var frame := Panel.new()
	frame.name = "Frame"
	frame.clip_children = CanvasItem.CLIP_CHILDREN_AND_DRAW
	frame.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	view.add_child(frame)
	var texture := BLOCKS.picture(str(ref.get("image", "")), self)
	if texture != null:
		var picture := TextureRect.new()
		picture.name = "Picture"
		picture.texture = texture
		picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		picture.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		frame.add_child(picture)
	STYLE.on_theme_overrides(
		frame,
		func() -> void:
			var box := StyleBoxFlat.new()
			box.bg_color = frame.get_theme_color("surface_sunken", "Joinery")
			box.set_corner_radius_all(UI_THEME.radius(frame, "lg"))
			frame.add_theme_stylebox_override("panel", box)
	)
	frame.resized.connect(func() -> void: frame.custom_minimum_size.y = roundf(frame.size.x * 0.75))
	var words := VBoxContainer.new()
	words.theme_type_variation = "JoineryTight"
	view.add_child(words)
	words.add_child(STYLE.card_title(str(ref.get("title", "")), true))
	for pair: Array in [["Take", ref.get("take", "")], ["Not", ref.get("not", "")]]:
		if not str(pair[1]).is_empty():
			words.add_child(UI.label("%s: %s" % [pair[0], pair[1]], "JoineryCaption", true))
	_scroll.scroll_vertical = 0
	if from != null:
		UI_MOTION.morph(from, frame)
	else:
		UI_MOTION.enter(view)


## The feel: one sentence in body_lg, its tags as chips under it.
func _feel(parts: Array, words: String) -> Control:
	var group := _group("Feel", "Change the feel of the look: ")
	if words.strip_edges().is_empty():
		group.add_child(_slot("Feel", "Help me decide the feel and look of my game."))
		return group
	var found := LOOK.feel(parts)
	if not str(found[0]).is_empty():
		var line := UI.label(str(found[0]), "JoineryBodyLarge", true)
		line.name = "FeelLine"
		group.add_child(line)
	if not (found[1] as Array).is_empty():
		group.add_child(LOOK.chips(found[1]))
	if str(found[0]).is_empty() and (found[1] as Array).is_empty():
		group.add_child(_rich(DIALOG.to_bbcode(words)))
	return group


func _never(words: String) -> Control:
	var group := _group("Never", "Change what the look never does: ")
	var bans := LOOK.bans(words)
	if bans.is_empty():
		group.add_child(_slot("Never", "Help me decide what my game should never look like."))
	else:
		group.add_child(LOOK.chips(bans, true))
	return group


func _sound(words: String) -> Control:
	var group := _group("Sound", "Change the sound of the look: ")
	if words.strip_edges().is_empty():
		group.add_child(_slot("Sound", "Help me decide how my game should sound."))
	else:
		var body := _rich(DIALOG.to_bbcode(words))
		body.name = "SoundText"
		group.add_child(body)
	return group


func _group(title: String, change: String) -> VBoxContainer:
	var group := VBoxContainer.new()
	group.name = title
	group.theme_type_variation = "JoineryTight"
	group.add_child(LOOK.heading(title, _ask.bind(change) if _can_ask() else Callable()))
	return group


func _slot(section: String, decide: String) -> Control:
	return LOOK.open_slot(section, _ask.bind(decide) if _can_ask() else Callable())


func _technical(parts: Array) -> Control:
	var section := UI.section("Checked rules", true)
	section.name = "Technical"
	section.header.tooltip_text = "Joinery checks every asset against these before you review it."
	section.body.theme_type_variation = "JoineryStack"
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
		# Each rule its name over its words; "(checked)" is for writers.
		var item := VBoxContainer.new()
		item.theme_type_variation = "JoineryTight"
		item.add_child(UI.label(heading.replace("(checked)", "").strip_edges(), "JoineryCaption"))
		item.add_child(_rich(DIALOG.to_bbcode(body)))
		rules.add_child(item)
	# A bible with no technical rules has nothing to unfold.
	section.visible = rules.get_child_count() > 0
	return section


# --- Helpers -----------------------------------------------------------------------------------


## The swatches grow at 1000 px; the Change links always show in a narrow sidebar.
func _fit_width() -> void:
	if not is_inside_tree():
		return
	var logical := _body.size.x / UI_THEME.unit(self)
	for row: Node in _content.find_children("*Heading", "HBoxContainer", true, false):
		LOOK.set_narrow(row as Control, logical < NARROW)
	var palette := _content.get_node_or_null("Palette")
	if palette != null:
		var swatches := palette.get_node("Swatches") as Control
		var tall := "lg" if logical >= WIDE else "md"
		swatches.set("size_name", tall)
		swatches.custom_minimum_size.y = UI_THEME.swatch_h(swatches, tall)


func _set_title(text: String) -> void:
	_title.text = text
	accessibility_name = text


func _clear() -> void:
	for child: Node in _content.get_children():
		_content.remove_child(child)
		child.queue_free()


func _can_ask() -> bool:
	return dock != null and dock.has_method("fill_composer")


## Start a message with [param text] and go back to the chat to finish it.
func _ask(text: String) -> void:
	closed.emit()
	dock.call("fill_composer", text)


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
