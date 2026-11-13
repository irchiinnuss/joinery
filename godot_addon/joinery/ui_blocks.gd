@tool
extends RefCounted
## Larger building blocks, reached through ui_components.gd: the loading skeleton, the blankslate
## (an empty or first-run view with one main action), the palette strip and the reference grid.
## Sizes come from the ui_theme tokens and follow every theme change.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
## A loading bar is this share of a caption line tall, so it reads as a line of text.
const _BAR_EM := 0.8
## The last of several skeleton lines is this much shorter, like the end of a paragraph.
const _LAST_LINE := 0.6
## The blankslate's picture: an ICON.tile glyph in a tile twice that size.
const _TILE_GLYPH := 1.75
## A reference image is 4:3.
const _TILE_RATIO := 0.75
## How far a typographic reference tile's ground leans toward its colour from the look.
const _TINT := 0.22
## Reference images are kept at most this wide (logical px × unit); the grid never shows more.
const _THUMB_W := 320
## Reference columns by room (logical px × unit): two below the first width, three below the
## second, four from there (a 340, 480 and 1000 px sidebar).
const _TILE_STEPS := [400, 600]


## [param lines] bars shaped like text, on the page's ground, breathing while they show. One
## line is a short value-sized bar (a setting still loading); more read as a paragraph whose last
## line is shorter. Named "Skeleton"; swap it for the content with ui_motion.crossfade.
static func skeleton(lines := 2) -> VBoxContainer:
	var block := VBoxContainer.new()
	block.name = "Skeleton"
	block.theme_type_variation = "JoineryTight"
	block.mouse_filter = Control.MOUSE_FILTER_IGNORE
	block.accessibility_name = "Loading"
	block.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	for index: int in maxi(lines, 1):
		var row := HBoxContainer.new()
		row.theme_type_variation = "JoineryFlush"
		row.mouse_filter = Control.MOUSE_FILTER_IGNORE
		block.add_child(row)
		var bar := Panel.new()
		bar.name = "Bar"
		bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
		bar.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row.add_child(bar)
		if lines > 1 and index == lines - 1:
			var rest := Control.new()
			rest.mouse_filter = Control.MOUSE_FILTER_IGNORE
			rest.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			rest.size_flags_stretch_ratio = (1.0 - _LAST_LINE) / _LAST_LINE
			row.add_child(rest)
		STYLE.on_theme_overrides(
			bar,
			func() -> void:
				var box := StyleBoxFlat.new()
				box.bg_color = bar.get_theme_color("border_subtle", "Joinery")
				box.set_corner_radius_all(UI_THEME.radius(bar, "xs"))
				bar.add_theme_stylebox_override("panel", box)
				var tall := roundi(UI_THEME.font_size(bar, "caption") * _BAR_EM)
				# A lone bar stands in for a value: 64 logical px (two SPACE.6 steps) wide.
				var wide := UI_THEME.space(bar, 6) * 2 if lines <= 1 else 0
				bar.custom_minimum_size = Vector2(wide, tall)
		)
	block.tree_entered.connect(func() -> void: UI_MOTION.breathe(block))
	return block


## While [param waiting], a one-line skeleton bar stands in [param control]'s place (a value
## or a switch not known yet: never "Checking…" text); after, the control fades back in and
## the bar leaves.
static func wait_for(control: Control, waiting: bool) -> void:
	var holder := control.get_parent()
	var bar: Control = holder.get_node_or_null("Skeleton") if holder != null else null
	if waiting:
		if bar == null and holder != null:
			bar = skeleton(1)
			holder.add_child(bar)
			holder.move_child(bar, control.get_index())
		control.hide()
		return
	if bar == null:
		return
	bar.queue_free()
	holder.remove_child(bar)
	control.show()
	UI_MOTION.fade_in(control)


## A round swatch of [param color], 12 logical px × unit (the accent picker's items).
static func swatch_dot(context: Node, color: Color) -> ImageTexture:
	var side := maxi(4, UI_THEME.space(context, 3))
	var image := Image.create(side, side, false, Image.FORMAT_RGBA8)
	var half := side / 2.0
	for y: int in side:
		for x: int in side:
			var distance := Vector2(x + 0.5 - half, y + 0.5 - half).length()
			image.set_pixel(x, y, Color(color, clampf(half - distance + 0.5, 0.0, 1.0)))
	return ImageTexture.create_from_image(image)


## A finding's severity as a glyph and a colour, so it reads without colour too: ▲ high
## (danger), ● medium (accent), ○ low (faint ring). Its tooltip names it.
static func severity_glyph(severity: String) -> Control:
	var glyph := SeverityGlyph.new()
	glyph.severity = severity
	glyph.tooltip_text = severity.capitalize()
	return glyph


## Draws [method severity_glyph]: a triangle, a dot or a ring, DOT × unit across.
class SeverityGlyph:
	extends Control
	var severity := "low"

	func _init() -> void:
		size_flags_vertical = Control.SIZE_SHRINK_CENTER
		mouse_filter = Control.MOUSE_FILTER_PASS

	func _notification(what: int) -> void:
		if what == NOTIFICATION_THEME_CHANGED or what == NOTIFICATION_ENTER_TREE:
			var side := float(UI_THEME.dot_px(self)) * 1.25
			custom_minimum_size = Vector2(side, side)
			queue_redraw()

	func _draw() -> void:
		var side := minf(size.x, size.y)
		var center := size / 2.0
		var radius := side / 2.0
		if severity in ["high", "critical"]:
			var ink := get_theme_color("danger", "Joinery")
			var points := PackedVector2Array(
				[
					center + Vector2(0.0, -radius),
					center + Vector2(radius, radius * 0.8),
					center + Vector2(-radius, radius * 0.8),
				]
			)
			draw_colored_polygon(points, ink)
		elif severity == "medium":
			draw_circle(
				center, radius * 0.8, get_theme_color("accent", "Joinery"), true, -1.0, true
			)
		else:
			var width := maxf(1.0, roundf(UI_THEME.unit(self)))
			var faint := get_theme_color("text_faint", "Joinery")
			draw_circle(center, radius * 0.8 - width / 2.0, faint, false, width, true)


## An empty or first-run view (Primer's Blankslate): a picture ([param icon] in a raised tile,
## "" for none), one [param heading], an optional one-line [param line], the one
## [param primary] action and an optional quiet [param secondary] link, centred in the room it
## has. The buttons are the caller's, so it wires them; they keep their own variations.
static func blankslate(
	icon: String, heading: String, primary: Button = null, secondary: Button = null, line := ""
) -> VBoxContainer:
	var block := VBoxContainer.new()
	block.name = "Blankslate"
	block.theme_type_variation = "JoineryStack"
	block.alignment = BoxContainer.ALIGNMENT_CENTER
	block.size_flags_vertical = Control.SIZE_EXPAND_FILL
	if not icon.is_empty():
		block.add_child(_tile(icon))
	var title := UI.label(heading, "JoineryTitle", true)
	title.name = "Heading"
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	block.add_child(title)
	if not line.is_empty():
		var hint := UI.label(line, "JoineryCaption", true)
		hint.name = "Line"
		hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		block.add_child(hint)
	var actions := VBoxContainer.new()
	actions.name = "Actions"
	actions.theme_type_variation = "JoineryTight"
	block.add_child(actions)
	for button: Button in [primary, secondary]:
		if button != null:
			button.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
			actions.add_child(button)
	actions.visible = actions.get_child_count() > 0
	return block


## A centred empty state for a preview pane: a faint [param icon_name] icon, a [param title],
## an optional [param line] saying what to do, and an optional [param action]. Fills the height.
static func empty_state(
	icon_name: String, title: String, line := "", action: Button = null
) -> VBoxContainer:
	var block := VBoxContainer.new()
	block.theme_type_variation = "JoineryTight"
	block.alignment = BoxContainer.ALIGNMENT_CENTER
	block.size_flags_vertical = Control.SIZE_EXPAND_FILL
	var icon := TextureRect.new()
	icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
	UI.bind_icon(icon, icon_name, "texture")
	icon.theme_changed.connect(
		func() -> void: icon.self_modulate = UI_THEME.color(icon, "text_faint")
	)
	block.add_child(icon)
	var heading := UI.label(title, "JoineryBodyLarge")
	heading.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	block.add_child(heading)
	if not line.is_empty():
		var hint := UI.label(line, "JoineryCaption", true)
		hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		block.add_child(hint)
	if action != null:
		action.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
		block.add_child(action)
	return block


## A banner with one sentence and one secondary action ("Lost connection. [Reconnect]",
## "Open Docker Desktop… [Check again]"). The text label is the "Text" child and the button
## the "Action" child; connect to the button's pressed signal. [param variation] is
## JoineryBanner for something that went wrong, JoineryCard for plain guidance.
static func notice(
	text: String, action_text: String, variation := "JoineryBanner"
) -> PanelContainer:
	var panel := PanelContainer.new()
	panel.theme_type_variation = variation
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	panel.add_child(line)
	var words := UI.label(text, "", true)
	words.name = "Text"
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(words)
	var button := UI.secondary(action_text)
	button.name = "Action"
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(button)
	return panel


## [param text] trimmed in the middle to fit [param width] in [param font] at [param size]:
## the start and the end (a file's extension) stay, joined by an ellipsis.
static func middle(text: String, font: Font, size: int, width: float) -> String:
	if font == null or font.get_string_size(text, 0, -1, size).x <= width:
		return text
	var keep := text.length() - 1
	while keep > 2:
		var tail := keep / 2
		var short := text.left(keep - tail) + "…" + text.right(tail)
		if font.get_string_size(short, 0, -1, size).x <= width:
			return short
		keep -= 1
	return text.left(1) + "…"


## The picture of a blankslate:[param icon] drawn ICON.tile large in a surface_raised tile
## twice that size.
static func _tile(icon: String) -> PanelContainer:
	var tile := PanelContainer.new()
	tile.name = "Picture"
	tile.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	tile.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var glyph := TextureRect.new()
	glyph.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	glyph.mouse_filter = Control.MOUSE_FILTER_IGNORE
	tile.add_child(glyph)
	STYLE.on_theme_overrides(
		tile,
		func() -> void:
			var side := UI_THEME.icon_px(tile, "tile") * 2
			tile.custom_minimum_size = Vector2(side, side)
			var box := StyleBoxFlat.new()
			box.bg_color = tile.get_theme_color("surface_raised", "Joinery")
			box.set_corner_radius_all(UI_THEME.radius(tile, "lg"))
			tile.add_theme_stylebox_override("panel", box)
			glyph.texture = _large_icon(tile, icon, tile.get_theme_color("text_muted", "Joinery"))
	)
	return tile


## A bundled icons/ui glyph drawn ICON.tile large (sharp, not a stretched 16 px texture).
static func _large_icon(context: Control, icon: String, tint: Color) -> Texture2D:
	var script: Resource = UI_THEME
	var path := script.resource_path.get_base_dir().path_join("icons/ui/%s.svg" % icon)
	if not FileAccess.file_exists(path):
		return UI_THEME.icon(context, icon, tint)
	var svg := FileAccess.get_file_as_string(path).replace("#fff", "#" + tint.to_html(false))
	var image := Image.new()
	if image.load_svg_from_string(svg, UI_THEME.unit(context) * _TILE_GLYPH) != OK:
		return UI_THEME.icon(context, icon, tint)
	return ImageTexture.create_from_image(image)


## The palette as colour (Milanote's swatch cards): one swatch per role in [param roles]
## ({role, hex, use}), side by side, SWATCH_H[[param size]] tall, with the role names under them
## in faint caption text while each swatch has room for its name ([param captions] false: never,
## a miniature). A role without a hex is an empty dashed slot. Hover or focus shows the role, its
## hex and its use as the tooltip.
static func palette_strip(roles: Array, size := "md", captions := true) -> VBoxContainer:
	var block := VBoxContainer.new()
	block.name = "PaletteStrip"
	block.theme_type_variation = "JoineryTight"
	var strip := PaletteSwatches.new()
	strip.name = "Swatches"
	strip.size_name = size
	strip.roles = _roles(roles)
	block.add_child(strip)
	var names := HBoxContainer.new()
	names.name = "Names"
	names.theme_type_variation = "JoineryFlush"
	names.mouse_filter = Control.MOUSE_FILTER_IGNORE
	block.add_child(names)
	for role: Dictionary in strip.roles:
		var name := UI.label(str(role["role"]), "JoineryFaint")
		name.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		name.size_flags_stretch_ratio = 1.0
		name.mouse_filter = Control.MOUSE_FILTER_IGNORE
		names.add_child(name)
	# Narrow (five swatches in a 340 px sidebar), the names move to the tooltips only.
	names.visible = captions
	strip.resized.connect(
		func() -> void:
			var count := maxi(strip.roles.size(), 1)
			var room := strip.size.x / count
			names.visible = captions and room >= UI_THEME.font_size(strip, "caption") * 5.0
	)
	return block


static func _roles(roles: Array) -> Array[Dictionary]:
	var result: Array[Dictionary] = []
	for item: Variant in roles:
		if not item is Dictionary:
			continue
		var entry := item as Dictionary
		var hex := str(entry.get("hex", entry.get("color", ""))).strip_edges()
		(
			result
			. append(
				{
					"role": str(entry.get("role", entry.get("name", ""))),
					"hex": hex if Color.html_is_valid(hex) else "",
					"use": str(entry.get("use", "")),
				}
			)
		)
	return result


## The swatches themselves, drawn as one control: outer corners RADIUS.lg, the joins between
## swatches RADIUS.sm, a SPACE.1 / 2 gap. [signal swatch_pressed] reports a click.
class PaletteSwatches:
	extends Control

	signal swatch_pressed(index: int)

	var size_name := "md"
	var roles: Array[Dictionary] = []

	func _init() -> void:
		focus_mode = Control.FOCUS_ALL
		mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
		size_flags_horizontal = Control.SIZE_EXPAND_FILL
		accessibility_name = "Palette"

	func _notification(what: int) -> void:
		if what == NOTIFICATION_THEME_CHANGED or what == NOTIFICATION_ENTER_TREE:
			custom_minimum_size.y = UI_THEME.swatch_h(self, size_name)
			queue_redraw()

	## The swatch under [param at] (local), or -1.
	func index_at(at: Vector2) -> int:
		if roles.is_empty() or size.x <= 0.0:
			return -1
		return clampi(int(at.x / (size.x / roles.size())), 0, roles.size() - 1)

	func _get_tooltip(at: Vector2) -> String:
		var index := index_at(at)
		if index < 0:
			return ""
		var role := roles[index]
		var hex := str(role["hex"])
		var lines := PackedStringArray(
			[str(role["role"]) + ("  " + hex if not hex.is_empty() else "")]
		)
		if not str(role["use"]).is_empty():
			lines.append(str(role["use"]))
		elif hex.is_empty():
			lines.append("Not chosen yet")
		return "\n".join(lines)

	func _gui_input(event: InputEvent) -> void:
		var click := event as InputEventMouseButton
		if click != null and click.pressed and click.button_index == MOUSE_BUTTON_LEFT:
			var index := index_at(click.position)
			if index >= 0:
				swatch_pressed.emit(index)
				accept_event()

	func _draw() -> void:
		var count := roles.size()
		if count == 0:
			return
		var gap := UI_THEME.space(self, 1) / 2.0
		# A miniature (SWATCH_H.sm, inside a card) keeps its corners small.
		var small := size_name == "sm"
		var outer := UI_THEME.radius(self, "sm" if small else "lg")
		var inner := UI_THEME.radius(self, "xs" if small else "sm")
		var wide := (size.x - gap * (count - 1)) / count
		var faint := get_theme_color("border_subtle", "Joinery")
		var hairline := maxf(UI_THEME.unit(self), 1.0)
		for index: int in count:
			var rect := Rect2(index * (wide + gap), 0.0, wide, size.y)
			var hex := str(roles[index]["hex"])
			if hex.is_empty():
				_dashed(rect.grow(-hairline / 2.0), faint, hairline)
				continue
			var box := StyleBoxFlat.new()
			box.bg_color = Color.html(hex)
			box.corner_radius_top_left = outer if index == 0 else inner
			box.corner_radius_bottom_left = outer if index == 0 else inner
			box.corner_radius_top_right = outer if index == count - 1 else inner
			box.corner_radius_bottom_right = outer if index == count - 1 else inner
			box.anti_aliasing = true
			draw_style_box(box, rect)
		if has_focus():
			var ring := StyleBoxFlat.new()
			ring.draw_center = false
			ring.set_border_width_all(roundi(hairline * 2.0))
			ring.border_color = get_theme_color("accent", "Joinery")
			ring.set_corner_radius_all(outer)
			draw_style_box(ring, Rect2(Vector2.ZERO, size))

	func _dashed(rect: Rect2, color: Color, width: float) -> void:
		var dash := UI_THEME.space(self, 1)
		var corners := [
			rect.position,
			Vector2(rect.end.x, rect.position.y),
			rect.end,
			Vector2(rect.position.x, rect.end.y),
		]
		for side: int in 4:
			draw_dashed_line(corners[side], corners[(side + 1) % 4], color, width, dash)


## Reference images in a grid: each tile shows the picture (a PNG, JPEG or WebP, or a Krita
## board's own preview) at 4:3 with RADIUS.md corners, its one-line "take" under it, and what
## it is not ("Not: …") in the tooltip. A reference without a picture (a game, film or place)
## is typographic: its title set large and what it is small ("Hob", "game") on a ground tinted
## with one of the look's colours; no icon stands in for a picture it does not have.
## [param columns] fixes the count; 0 picks two to four from the width. [param refs] are
## {title, image, take, not, kind, tint}; image is a file path, kind the word under the title
## ("game"), tint the colour its ground leans toward (none: the raised fill).
static func reference_grid(refs: Array, columns := 0) -> GridContainer:
	var grid := GridContainer.new()
	grid.name = "ReferenceGrid"
	grid.columns = maxi(columns, 1) if columns > 0 else 3
	STYLE.on_theme_overrides(
		grid,
		func() -> void:
			var gap := UI_THEME.space(grid, 2)
			grid.add_theme_constant_override("h_separation", gap)
			grid.add_theme_constant_override("v_separation", gap)
	)
	if columns <= 0:
		grid.resized.connect(
			func() -> void:
				var logical := grid.size.x / UI_THEME.unit(grid)
				var count := (
					2 if logical < _TILE_STEPS[0] else (3 if logical < _TILE_STEPS[1] else 4)
				)
				if grid.columns != count:
					grid.columns = count
		)
	for item: Variant in refs:
		if item is Dictionary:
			grid.add_child(_reference(item as Dictionary))
	return grid


static func _reference(ref: Dictionary) -> VBoxContainer:
	var tile := VBoxContainer.new()
	tile.name = "Reference"
	tile.theme_type_variation = "JoineryTight"
	tile.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var title := str(ref.get("title", ""))
	var lines := PackedStringArray([title])
	if not str(ref.get("not", "")).is_empty():
		lines.append("Not: " + str(ref["not"]))
	tile.tooltip_text = "\n".join(lines).strip_edges()
	var frame := Panel.new()
	frame.name = "Frame"
	frame.mouse_filter = Control.MOUSE_FILTER_PASS
	frame.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	frame.clip_children = CanvasItem.CLIP_CHILDREN_AND_DRAW
	tile.add_child(frame)
	var texture := _thumbnail(str(ref.get("image", "")), frame)
	if texture != null:
		var picture := TextureRect.new()
		picture.name = "Picture"
		picture.texture = texture
		picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
		picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
		picture.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		frame.add_child(picture)
	else:
		frame.add_child(_stand_in(title, str(ref.get("kind", ""))))
	var tint: Variant = ref.get("tint")
	STYLE.on_theme_overrides(
		frame,
		func() -> void:
			var box := StyleBoxFlat.new()
			# A picture sits on the sunken ground; a stand-in on the raised one, leaning a
			# little toward its colour from the look so the board reads as one palette.
			var ground := "surface_sunken" if texture != null else "surface_raised"
			box.bg_color = frame.get_theme_color(ground, "Joinery")
			if texture == null and tint is Color:
				box.bg_color = box.bg_color.lerp(tint as Color, _TINT)
			box.set_corner_radius_all(UI_THEME.radius(frame, "md"))
			box.anti_aliasing = true
			frame.add_theme_stylebox_override("panel", box)
	)
	frame.resized.connect(
		func() -> void: frame.custom_minimum_size.y = roundf(frame.size.x * _TILE_RATIO)
	)
	var take := str(ref.get("take", ""))
	var caption := UI.label(take if not take.is_empty() else title, "JoineryCaption")
	caption.name = "Take"
	caption.mouse_filter = Control.MOUSE_FILTER_IGNORE
	tile.add_child(caption)
	return tile


## A reference with no picture, set in type: the title at title size on the tile's lower
## edge, what it is ("game") small above it, both SPACE.3 in from the corner.
static func _stand_in(title: String, kind: String) -> MarginContainer:
	var stand_in := MarginContainer.new()
	stand_in.name = "StandIn"
	stand_in.mouse_filter = Control.MOUSE_FILTER_IGNORE
	stand_in.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	STYLE.on_theme_overrides(
		stand_in,
		func() -> void:
			var pad := UI_THEME.space(stand_in, 3)
			for side: String in ["left", "right", "top", "bottom"]:
				stand_in.add_theme_constant_override("margin_" + side, pad)
	)
	var words := VBoxContainer.new()
	words.theme_type_variation = "JoineryFlush"
	words.alignment = BoxContainer.ALIGNMENT_END
	words.mouse_filter = Control.MOUSE_FILTER_IGNORE
	stand_in.add_child(words)
	if not kind.is_empty():
		var what := UI.label(kind.to_lower(), "JoineryCaption")
		what.name = "Kind"
		what.mouse_filter = Control.MOUSE_FILTER_IGNORE
		# On a tinted ground the muted ink loses its contrast: the full text colour, smaller.
		STYLE.on_theme_overrides(
			what,
			func() -> void:
				what.add_theme_color_override("font_color", what.get_theme_color("text", "Joinery"))
		)
		words.add_child(what)
	var name := UI.label(title, "JoineryTitle", true)
	name.name = "Title"
	name.max_lines_visible = 2
	name.mouse_filter = Control.MOUSE_FILTER_IGNORE
	words.add_child(name)
	return stand_in


## A small picture of [param path] (an image, or a Krita board's own preview), or null.
static func picture(path: String, context: Control) -> Texture2D:
	return _thumbnail(path, context)


## A small texture for [param path]: the image itself, or a Krita board's own preview
## (mergedimage.png, else preview.png inside the .kra). Null when there is no picture.
static func _thumbnail(path: String, context: Control) -> Texture2D:
	if path.is_empty():
		return null
	var absolute := ProjectSettings.globalize_path(path)
	if not FileAccess.file_exists(absolute):
		return null
	var image := Image.new()
	if absolute.get_extension().to_lower() == "kra":
		var zip := ZIPReader.new()
		if zip.open(absolute) != OK:
			return null
		for inner: String in ["mergedimage.png", "preview.png"]:
			if zip.file_exists(inner) and image.load_png_from_buffer(zip.read_file(inner)) == OK:
				break
		zip.close()
	elif image.load(absolute) != OK:
		return null
	if image.is_empty():
		return null
	var wide := roundi(_THUMB_W * UI_THEME.unit(context))
	if image.get_width() > wide:
		image.resize(wide, maxi(1, roundi(image.get_height() * wide / float(image.get_width()))))
	return ImageTexture.create_from_image(image)
