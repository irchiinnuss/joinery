@tool
extends RefCounted
## Small styling helpers shared by the sidebar modules. Every value is read from the Joinery
## theme or the ui_theme tokens and re-applied on each theme change, so editor scale, theme
## and accent changes reach these controls live.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const COLUMN := preload("sidebar_column.gd")
## Meta key prefix: what a part showed before a page took its place ([method hold_parts]).
const HELD := "_joinery_held_"


## Give [param control] the [param variation] panel with its side padding set to SPACE
## [param step], so text inside a box starts on the same line as text outside boxes.
static func boxed(control: Control, variation: String, step := 2) -> void:
	on_theme_overrides(
		control,
		func() -> void:
			var source := control.get_theme_stylebox("panel", variation)
			if source == null:
				return
			var box := source.duplicate() as StyleBox
			var pad := float(UI_THEME.space(control, step))
			box.content_margin_left = pad
			box.content_margin_right = pad
			control.add_theme_stylebox_override("panel", box)
	)


## A card in the sidebar's card area (to-do, errors, playtest, a task, a check): the decision
## card's lifted surface with its SPACE.3 padding, so every card's words start on the same line
## as the change card's and the message box's.
static func task_card(control: Control) -> void:
	boxed(control, "JoineryChangeCard", 3)


## How far an icon button's glyph sits inside its square (CONTROL_H.md around an ICON.sm
## glyph): a row of them reaches this far past the gutter, so the glyphs end on it.
static func icon_inset(control: Control) -> int:
	return roundi((UI_THEME.control_h(control, "md") - UI_THEME.icon_px(control, "sm")) / 2.0)


## Move a flat button's text onto the content edge: its left padding becomes an outward
## expansion of the hover fill, the same treatment the theme gives JoineryPicker.
static func hug_left(button: Button) -> void:
	on_theme_overrides(
		button,
		func() -> void:
			var variation := button.theme_type_variation
			for state: String in [
				"normal", "hover", "pressed", "hover_pressed", "disabled", "focus"
			]:
				button.remove_theme_stylebox_override(state)
			for state: String in [
				"normal", "hover", "pressed", "hover_pressed", "disabled", "focus"
			]:
				var source := button.get_theme_stylebox(state, variation)
				if source == null:
					continue
				var box := source.duplicate() as StyleBox
				var pad := box.content_margin_left
				box.content_margin_left = 0.0
				if box is StyleBoxFlat:
					(box as StyleBoxFlat).expand_margin_left += pad
				button.add_theme_stylebox_override(state, box)
	)


## The same on the right: [param button]'s text ends on the content edge, its hover fill
## reaching past it, so a trailing action lines up with the links above and below it.
static func hug_right(button: Button) -> void:
	on_theme_overrides(
		button,
		func() -> void:
			var variation := button.theme_type_variation
			for state: String in [
				"normal", "hover", "pressed", "hover_pressed", "disabled", "focus"
			]:
				button.remove_theme_stylebox_override(state)
				var source := button.get_theme_stylebox(state, variation)
				if source == null:
					continue
				var box := source.duplicate() as StyleBox
				var pad := box.content_margin_right
				box.content_margin_right = 0.0
				if box is StyleBoxFlat:
					(box as StyleBoxFlat).expand_margin_right += pad
				button.add_theme_stylebox_override(state, box)
	)


## A transcript card's title, the one card header: body size, semibold, so it leads its rows by
## weight alone. Ellipsized on one line, or wrapped when [param wrap] is set.
static func card_title(text: String, wrap := false) -> Label:
	var title := Label.new()
	title.text = text
	title.theme_type_variation = "JoineryCardTitle"
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	if wrap:
		title.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	else:
		title.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	return title


## A row's own action ([param action], a row's Fix or Ignore) shows only while [param row] is
## pointed at or the action has keyboard focus, so a list leads with what it lists. It keeps
## its place (only its ink fades), so nothing moves when it appears. A [param collapse] button
## gives its room to the row's words while hidden (its text clipped away), still focusable.
static func reveal_on_hover(row: Control, action: Control, collapse := false) -> void:
	action.self_modulate.a = 0.0
	if collapse and action is Button:
		(action as Button).clip_text = true
	if row.mouse_filter == Control.MOUSE_FILTER_IGNORE:
		row.mouse_filter = Control.MOUSE_FILTER_PASS
	var update := func() -> void:
		if not is_instance_valid(action) or not action.is_inside_tree():
			return
		var shown := (
			action.has_focus() or row.get_global_rect().has_point(row.get_global_mouse_position())
		)
		if collapse and action is Button:
			(action as Button).clip_text = not shown
		UI_MOTION.tween_to(action, action, "self_modulate:a", 1.0 if shown else 0.0, "fast")
	row.mouse_entered.connect(update)
	row.mouse_exited.connect(update)
	action.mouse_entered.connect(update)
	action.mouse_exited.connect(update)
	action.focus_entered.connect(update)
	action.focus_exited.connect(update)


## A quiet button that reads as a button at rest: a surface_raised fill (a step stronger on
## hover and press) at RADIUS.md, with SPACE.2 padding on both sides. Enabled text is the full
## text colour and hover adds a border_strong hairline, so it reads as clickable. Disabled
## keeps the same shape with the fill at half strength and text_faint text: only the contrast
## drops.
static func filled(button: Button) -> void:
	on_theme_overrides(
		button,
		func() -> void:
			var raised := button.get_theme_color("surface_raised", "Joinery")
			var ink := button.get_theme_color("text", "Joinery")
			var fills := {
				"normal": raised,
				"hover": raised.lerp(ink, 0.07),
				"pressed": raised.lerp(ink, 0.12),
				"hover_pressed": raised.lerp(ink, 0.12),
				"disabled": Color(raised, raised.a * 0.5),
			}
			var pad := float(UI_THEME.space(button, 2))
			var corner := UI_THEME.radius(button, "md")
			var edge := button.get_theme_color("border_strong", "Joinery")
			var hairline := maxi(roundi(UI_THEME.unit(button)), 1)
			for key: String in [
				"font_color",
				"font_hover_color",
				"font_pressed_color",
				"font_focus_color",
				"font_hover_pressed_color",
				"icon_normal_color",
				"icon_hover_color",
				"icon_pressed_color",
				"icon_focus_color"
			]:
				button.add_theme_color_override(key, ink)
			var faint := button.get_theme_color("text_faint", "Joinery")
			button.add_theme_color_override("font_disabled_color", faint)
			button.add_theme_color_override("icon_disabled_color", faint)
			for state: String in fills:
				button.remove_theme_stylebox_override(state)
				var source := button.get_theme_stylebox(state, button.theme_type_variation)
				if not source is StyleBoxFlat:
					source = button.get_theme_stylebox("normal", button.theme_type_variation)
				if not source is StyleBoxFlat:
					continue
				var box := source.duplicate() as StyleBoxFlat
				box.bg_color = fills[state]
				box.content_margin_left = pad
				box.content_margin_right = pad
				box.expand_margin_left = 0.0
				box.set_corner_radius_all(corner)
				# Every state draws the same hairline (the fill colour at rest), so hover only
				# changes its colour and nothing moves.
				box.set_border_width_all(hairline)
				box.border_color = edge if state.begins_with("hover") else fills[state]
				button.add_theme_stylebox_override(state, box)
	)


## A filled button sitting on a decision card: where the card is darker than a raised fill
## (the dark theme) its resting fill becomes the dock's ground (surface), so it stays visible;
## on the light theme's near-white card the raised fill already stands out and is kept.
static func on_card(button: Button) -> void:
	on_theme_overrides(
		button,
		func() -> void:
			button.remove_theme_stylebox_override("normal")
			var source := button.get_theme_stylebox("normal", button.theme_type_variation)
			var card := button.get_theme_color("surface_card", "Joinery")
			if source is StyleBoxFlat and card.get_luminance() < 0.5:
				var box := source.duplicate() as StyleBoxFlat
				box.bg_color = button.get_theme_color("surface", "Joinery")
				button.add_theme_stylebox_override("normal", box)
	)


## A square TextureRect for a 16u icon or thumbnail, tinted with the [param role] colour.
static func glyph(role := "text_muted") -> TextureRect:
	var picture := TextureRect.new()
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
	picture.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	picture.set_meta("_joinery_role", role)
	on_theme(
		picture,
		func() -> void:
			var side := float(UI_THEME.space(picture, 4))
			picture.custom_minimum_size = Vector2(side, side)
			var tint := str(picture.get_meta("_joinery_role", ""))
			if not tint.is_empty():
				picture.self_modulate = picture.get_theme_color(tint, "Joinery")
	)
	return picture


## Set a glyph's icon and tint role ("" keeps the texture's own colours, for thumbnails).
static func set_glyph(picture: TextureRect, icon_name: String, role := "text_muted") -> void:
	picture.set_meta("_joinery_role", role)
	picture.set_meta("_joinery_glyph", icon_name)
	if picture.is_inside_tree():
		_apply_glyph(picture)
	if not picture.has_meta("_joinery_glyph_bound"):
		picture.set_meta("_joinery_glyph_bound", true)
		picture.theme_changed.connect(_apply_glyph.bind(picture))


## Fade [param scroller]'s content out at its bottom edge (and the top once scrolled) into the
## dock's ground, SPACE.5 tall, so text slides under a sticky footer instead of being cut.
static func scroll_fade(scroller: ScrollContainer) -> void:
	if not "scroll_hint_mode" in scroller:
		return
	on_theme_overrides(
		scroller,
		func() -> void:
			var gradient := Gradient.new()
			gradient.set_color(0, Color.WHITE)
			gradient.set_color(1, Color(1, 1, 1, 0))
			var texture := GradientTexture2D.new()
			texture.gradient = gradient
			texture.fill_from = Vector2.ZERO
			texture.fill_to = Vector2(0, 1)
			texture.width = 4
			texture.height = maxi(UI_THEME.space(scroller, 5), 1)
			scroller.add_theme_icon_override("scroll_hint_vertical", texture)
			scroller.add_theme_color_override(
				"scroll_hint_vertical_color", scroller.get_theme_color("surface", "Joinery")
			)
			scroller.set("scroll_hint_mode", 1)
	)


## Put [param scroller]'s always-reserved scrollbar in the sidebar's right gutter, SPACE.1 off
## the content: each time its column lays it out on the content edges, it grows by the room
## the bar takes (at most the gutter), so its content keeps the right edge every other row has.
static func scroll_in_gutter(scroller: ScrollContainer) -> void:
	scroller.set_meta("joinery_gutter", true)
	# The editor theme pads the bar on both sides (6u each); without it the bar is only as
	# wide as its track, and fits in the gutter.
	var bar := scroller.get_v_scroll_bar()
	bar.add_theme_constant_override("padding_left", 0)
	bar.add_theme_constant_override("padding_right", 0)
	on_theme_overrides(
		scroller,
		func() -> void:
			var gap := UI_THEME.space(scroller, 1)
			scroller.add_theme_constant_override("scrollbar_h_separation", gap)
	)
	# The room ScrollContainer keeps for the bar: its width plus the separation.
	var widen := func() -> void:
		var column := scroller.get_parent() as Control
		var reserve := float(bar.get_minimum_size().x)
		reserve += scroller.get_theme_constant("scrollbar_h_separation")
		scroller.size.x = column.size.x + minf(reserve, UI_THEME.gutter(scroller))
	var attach := func() -> void:
		var column := scroller.get_parent() as Container
		if column != null and not column.sort_children.is_connected(widen):
			column.sort_children.connect(widen)
	if scroller.is_inside_tree():
		attach.call()
	scroller.tree_entered.connect(attach)
	scroller.tree_exiting.connect(
		func() -> void:
			var column := scroller.get_parent() as Container
			if column != null and column.sort_children.is_connected(widen):
				column.sort_children.disconnect(widen)
	)


## A sidebar page (Settings, Chats, the art direction) on the chat's edges: its rows' text
## starts on the gutter where the transcript's text does (the JoineryInset rows inside
## [param body] lose their left inset; a row's hover fill reaches outward instead),
## [param page]'s scrollbar keeps its room in the gutter, and the page shares the chat's
## column: MAX_W[[param max_w]] wide, centred in a wider sidebar ([method centered_column]).
static func page_edges(page: ScrollContainer, body: Control, max_w := "column") -> void:
	var local := Theme.new()
	for inset: String in ["JoineryInset", "JoineryInsetTall"]:
		local.set_type_variation(inset, "MarginContainer")
		local.set_constant("margin_left", inset, 0)
	body.theme = local
	page.vertical_scroll_mode = ScrollContainer.SCROLL_MODE_RESERVE
	scroll_in_gutter(page)
	centered_column(page, max_w)


## Hold [param page]'s content in one column at most MAX_W[[param max_w]] wide ("column" 640,
## "group" 560), centred when the sidebar is wider; a narrower sidebar is filled. A
## ScrollContainer keeps scrolling from edge to edge (its content moves into the column, which
## gets the content's size flags); any other control is wrapped in place. Returns the column
## (sidebar_column.gd); calling it again returns the same one.
static func centered_column(page: Control, max_w := "column") -> Container:
	var target := page
	if page is ScrollContainer:
		target = null
		for child: Node in page.get_children():
			if child is Control:
				target = child as Control
				break
		if target == null:
			return null
		if target.get_script() == COLUMN:
			target.set("cap", max_w)
			return target as Container
	var holder := target.get_parent()
	if holder != null and holder.get_script() == COLUMN:
		holder.set("cap", max_w)
		return holder as Container
	var column: Container = COLUMN.new()
	column.name = "Column"
	column.set("cap", max_w)
	column.size_flags_horizontal = target.size_flags_horizontal | Control.SIZE_FILL
	column.size_flags_vertical = target.size_flags_vertical
	column.size_flags_stretch_ratio = target.size_flags_stretch_ratio
	column.mouse_filter = Control.MOUSE_FILTER_IGNORE
	if holder != null:
		var index := target.get_index()
		holder.remove_child(target)
		holder.add_child(column)
		holder.move_child(column, index)
	column.add_child(target)
	if not page is ScrollContainer:
		# A wrapped row that hides (an idle task list) takes its column with it, so its parent
		# adds no gap for it.
		column.visible = target.visible
		target.set_meta("_joinery_shown", target.visible)
		target.visibility_changed.connect(
			func() -> void:
				# Its own visible property only: a parent hiding (Settings open) is not a change.
				if target.visible != bool(target.get_meta("_joinery_shown", true)):
					target.set_meta("_joinery_shown", target.visible)
					show_part(column, target.visible)
		)
	return column


## Show [param page] in place of the conversation, or close it ([param open]): the sidebar
## content's other parts hide while it shows and come back as they were. [param key] names the
## page ("chats", "settings", "art"); pages can open over each other.
static func hold_parts(page: Control, open: bool, key: String) -> void:
	var content := page.get_parent()
	var meta := HELD + key
	if content != null:
		for child: Node in content.get_children():
			if child == page or not child is CanvasItem:
				continue
			var item := child as CanvasItem
			if open:
				if not item.has_meta(meta):
					item.set_meta(meta, item.visible)
				item.visible = false
			elif item.has_meta(meta):
				var shown: bool = item.get_meta(meta)
				item.remove_meta(meta)
				# With another page still open over it, that page brings it back.
				show_part(item, shown)
	page.visible = open


## Show or hide [param item], a part of the sidebar's content. While a page shows in its place
## ([method hold_parts]) it stays hidden and shows as asked when the page closes; a part that
## changes on its own (a task card arrives, the sidebar resizes) never shows over a page.
static func show_part(item: CanvasItem, on: bool) -> void:
	var held := false
	for meta: StringName in item.get_meta_list():
		if str(meta).begins_with(HELD):
			item.set_meta(meta, on)
			held = true
	if not held:
		item.visible = on


## In Joinery's layout the sidebar is alone in its dock slot, which hides its tabs. There the
## slot's own padding goes, so [param dock]'s gutter is measured from the edge you see and its
## header row sits level with the editor's tab row. A slot that shows tabs (Godot's classic
## layout) keeps its look, and the slot gets its padding back when the dock leaves.
static func own_slot(dock: Control) -> void:
	dock.resized.connect(func() -> void: _fit_slot(dock, true))
	dock.tree_exiting.connect(func() -> void: _fit_slot(dock, false))


static func _fit_slot(dock: Control, present: bool) -> void:
	var wrapper := dock.get_parent()
	var slot := wrapper.get_parent() as TabContainer if wrapper != null else null
	if slot == null:
		return
	var flush := present and not slot.tabs_visible
	if flush == slot.has_meta("_joinery_flush"):
		return
	if flush:
		slot.add_theme_stylebox_override("panel", StyleBoxEmpty.new())
		slot.set_meta("_joinery_flush", true)
	else:
		slot.remove_theme_stylebox_override("panel")
		slot.remove_meta("_joinery_flush")


## Run [param apply] now when [param control] is in the tree, and after every theme change.
static func on_theme(control: Control, apply: Callable) -> void:
	if control.is_inside_tree():
		apply.call()
	control.theme_changed.connect(apply)


## Like [method on_theme], for an [param apply] that adds theme overrides to [param control]
## itself: an override re-sends the theme-changed notification, so nested calls are skipped.
static func on_theme_overrides(control: Control, apply: Callable) -> void:
	var guarded := func() -> void:
		if control.has_meta("_joinery_restyling"):
			return
		control.set_meta("_joinery_restyling", true)
		apply.call()
		control.remove_meta("_joinery_restyling")
	on_theme(control, guarded)


## A setting row ([param row], its [param title] and [param control]) whose control moves under
## the title when both don't fit on one line, so the title is never cut short ("Older versions"
## beside "Last 50 changes" in a narrow sidebar). Stacked, the control keeps the trailing edge
## like every other setting's value. Decided from the row's width alone, so moving the control
## never changes the answer.
static func stack_when_narrow(row: Container, title: Label, control: Control) -> void:
	var box := control.get_parent() as Container
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryFlush"
	row.remove_child(box)
	row.add_child(column)
	column.add_child(box)
	var fit := func() -> void:
		if not column.is_inside_tree() or column.size.x <= 0.0:
			return
		var font := title.get_theme_font("font")
		var size := title.get_theme_font_size("font_size")
		var wide := font.get_string_size(title.text, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x
		var gap := float(box.get_theme_constant("separation"))
		var stacked := wide + gap + control.get_combined_minimum_size().x > column.size.x
		if stacked != (control.get_parent() == column):
			control.reparent(column if stacked else box, false)
			control.size_flags_horizontal = (
				Control.SIZE_SHRINK_END if stacked else Control.SIZE_FILL
			)
	column.resized.connect(fit, CONNECT_DEFERRED)
	control.minimum_size_changed.connect(fit, CONNECT_DEFERRED)
	title.theme_changed.connect(fit, CONNECT_DEFERRED)


static func _apply_glyph(picture: TextureRect) -> void:
	var icon_name := str(picture.get_meta("_joinery_glyph", ""))
	if not icon_name.is_empty():
		picture.texture = UI_THEME.icon(picture, icon_name)
	var role := str(picture.get_meta("_joinery_role", ""))
	picture.self_modulate = (
		picture.get_theme_color(role, "Joinery") if not role.is_empty() else Color.WHITE
	)
