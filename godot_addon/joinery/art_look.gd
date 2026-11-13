@tool
extends RefCounted
## The look's parts, read from the art review event and drawn for art_page.gd and the art gate
## (sidebar_art_gate.gd): the palette roles, the reference pictures, the feel as one sentence
## and tags, the hard bans as chips, a section's caption heading with its Change link, the empty
## slot of a section not decided yet, and the loading skeleton shaped like the page.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const BLOCKS := preload("ui_blocks.gd")
const DIALOG := preload("art_review_dialog.gd")
## Empty swatches in a palette not chosen yet.
const EMPTY_ROLES := 5
## At most this many tags under the feel.
const MAX_TAGS := 6
## A reference kind's stand-in glyph, when it has no picture.
const MONTHS := ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


## The palette roles of [param event] ({role, hex, use}); five empty slots when none is chosen.
static func roles(event: Dictionary) -> Array:
	var found: Array = []
	for item: Variant in event.get("palette", []):
		if item is Dictionary:
			found.append(item)
	return found if chosen(found) else empty_roles()


## True when at least one role in [param palette] has a colour.
static func chosen(palette: Array) -> bool:
	return palette.any(
		func(role: Variant) -> bool:
			return role is Dictionary and not str(role.get("hex", "")).is_empty()
	)


static func empty_roles() -> Array:
	var empty: Array = []
	for index: int in EMPTY_ROLES:
		empty.append({"role": "", "hex": "", "use": "Not chosen yet"})
	return empty


## The board's references as reference_grid items ({title, image, take, not, kind, tint}): a
## reference with no picture leans toward the look's colours in turn, so the board reads as one
## palette rather than as grey placeholders.
static func references(event: Dictionary) -> Array:
	var colours: Array[Color] = []
	for role: Dictionary in roles(event):
		if Color.html_is_valid(str(role.get("hex", ""))):
			colours.append(Color.html(str(role["hex"])))
	var refs: Array = []
	for item: Variant in event.get("references", []):
		if not item is Dictionary:
			continue
		var row := item as Dictionary
		var file := str(row.get("file", ""))
		var tint: Variant = (
			colours[refs.size() % colours.size()] if not colours.is_empty() else null
		)
		(
			refs
			. append(
				{
					"title": str(row.get("reference", "")),
					"image": ("res://" + file) if not file.is_empty() else "",
					"take": str(row.get("take", "")),
					"not": str(row.get("avoid", "")),
					"kind": str(row.get("kind", "")).to_lower(),
					"tint": tint,
				}
			)
		)
	return refs


## The feel as [sentence, tags]: the vision's first paragraph, then the pillars' bold names and
## the look's comma-separated phrases as tags (at most MAX_TAGS).
static func feel(parts: Array) -> Array:
	var sentence := ""
	var tags: Array[String] = []
	var bold := RegEx.create_from_string("\\*\\*(.+?)\\*\\*")
	for part: Array in parts:
		var heading := str(part[0]).to_lower()
		var body := DIALOG.clean(str(part[1]))
		if body.is_empty():
			continue
		if heading.begins_with("vision") and sentence.is_empty():
			sentence = _plain(body.split("\n\n")[0])
		elif heading.begins_with("pillars"):
			for found: RegExMatch in bold.search_all(body):
				_add_tag(tags, found.get_string(1))
		elif heading.begins_with("visual style") or heading.begins_with("shape language"):
			for line: String in body.split("\n"):
				var text := _plain(line)
				var colon := text.find(":")
				if colon >= 0:
					text = text.substr(colon + 1)
				for phrase: String in text.split(","):
					_add_tag(tags, phrase)
	return [sentence, tags.slice(0, MAX_TAGS)]


## The hard bans as short phrases ("Neon", "Photo-real faces").
static func bans(text: String) -> Array[String]:
	var found: Array[String] = []
	for line: String in DIALOG.clean(text).split("\n"):
		for phrase: String in _plain(line).split(","):
			_add_tag(found, phrase)
	return found


## [param iso] ("2026-11-12") as "12 Nov"; "" stays "".
static func short_date(iso: String) -> String:
	var parts := iso.split("-")
	if parts.size() < 3 or not parts[1].is_valid_int():
		return ""
	return "%d %s" % [int(parts[2]), MONTHS[clampi(int(parts[1]) - 1, 0, 11)]]


## A row of chips, wrapping, quiet ([param muted]: text_muted words, for what to avoid).
static func chips(words: Array, muted := false) -> HFlowContainer:
	var flow := HFlowContainer.new()
	flow.name = "Tags"
	STYLE.on_theme_overrides(
		flow,
		func() -> void:
			var gap := UI_THEME.space(flow, 1)
			flow.add_theme_constant_override("h_separation", gap)
			flow.add_theme_constant_override("v_separation", gap)
	)
	for word: Variant in words:
		var chip := UI.chip(str(word))
		if muted:
			var label := chip.label
			STYLE.on_theme_overrides(
				label,
				func() -> void:
					label.add_theme_color_override(
						"font_color", label.get_theme_color("text_muted", "Joinery")
					)
			)
		flow.add_child(chip)
	return flow


## A section's caption heading, with a trailing "Change" link shown on hover or focus (always
## shown in a narrow sidebar: set the row's "narrow" meta). Null [param change]: no link.
static func heading(title: String, change := Callable()) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.name = title + "Heading"
	row.theme_type_variation = "JoineryRow"
	var label := UI.label(title, "JoineryCaption")
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(label)
	if change.is_valid():
		var link := UI.quiet_link("Change")
		link.name = "Change"
		link.accessibility_name = "Change %s with Journeyman" % title.to_lower()
		link.tooltip_text = "Ask Journeyman to change this"
		UI.bind_height(link, "xs")
		STYLE.hug_right(link)
		link.pressed.connect(change)
		row.add_child(link)
		_reveal(row, link)
	return row


## Shown while [param row] is pointed at or [param link] has focus, or always when the row's
## "narrow" meta is set (a touch-sized sidebar has no hover).
static func _reveal(row: Control, link: Control) -> void:
	link.self_modulate.a = 0.0
	row.mouse_filter = Control.MOUSE_FILTER_PASS
	var update := func() -> void:
		if not is_instance_valid(link) or not link.is_inside_tree():
			return
		var shown := (
			bool(row.get_meta("narrow", false))
			or link.has_focus()
			or row.get_global_rect().has_point(row.get_global_mouse_position())
		)
		UI_MOTION.tween_to(link, link, "self_modulate:a", 1.0 if shown else 0.0, "fast")
	row.set_meta("reveal", update)
	for signal_name: String in ["mouse_entered", "mouse_exited"]:
		row.connect(signal_name, update)
		link.connect(signal_name, update)
	link.focus_entered.connect(update)
	link.focus_exited.connect(update)


## Mark [param row] (a [method heading]) narrow or not, and show its link to match.
static func set_narrow(row: Control, narrow: bool) -> void:
	row.set_meta("narrow", narrow)
	var link := row.get_node_or_null("Change") as Control
	if link != null:
		link.self_modulate.a = 1.0 if narrow else 0.0


## A section not decided yet: "Not decided yet" with "Decide with me ›" on its trailing edge.
static func open_slot(section: String, decide: Callable) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.name = section + "Open"
	row.theme_type_variation = "JoineryRow"
	var words := UI.label("Not decided yet", "JoineryFaint")
	words.name = "OpenLine"
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(words)
	if decide.is_valid():
		var link := UI.quiet_link("Decide with me ›")
		link.name = "Decide"
		link.accessibility_name = "Decide the %s with Journeyman" % section.to_lower()
		UI.bind_height(link, "xs")
		STYLE.hug_right(link)
		link.pressed.connect(decide)
		row.add_child(link)
	return row


## The palette not chosen yet: five dashed swatches with "Choose colours with Journeyman" in
## their middle.
static func empty_palette(choose: Callable) -> Container:
	var stack := MarginContainer.new()
	stack.name = "PaletteEmpty"
	stack.add_child(UI.palette_strip(empty_roles(), "md", false))
	if choose.is_valid():
		var middle := CenterContainer.new()
		middle.mouse_filter = Control.MOUSE_FILTER_IGNORE
		stack.add_child(middle)
		# On the page's ground, so the dashes stop short of the words.
		var ground := PanelContainer.new()
		ground.mouse_filter = Control.MOUSE_FILTER_IGNORE
		middle.add_child(ground)
		STYLE.on_theme_overrides(
			ground,
			func() -> void:
				var box := StyleBoxFlat.new()
				box.bg_color = ground.get_theme_color("surface", "Joinery")
				box.content_margin_left = UI_THEME.space(ground, 2)
				box.content_margin_right = UI_THEME.space(ground, 2)
				ground.add_theme_stylebox_override("panel", box)
		)
		var link := UI.quiet_link("Choose colours with Journeyman")
		link.name = "Choose"
		UI.bind_height(link, "xs")
		link.pressed.connect(choose)
		ground.add_child(link)
	return stack


## "+ Add reference" under the board: a small hairline pill, never the size of a real
## reference.
static func add_tile(add: Callable) -> Button:
	var tile := Button.new()
	tile.text = "+  Add reference"
	tile.name = "AddReference"
	tile.theme_type_variation = "JoineryActionChip"
	tile.accessibility_name = "Add reference"
	tile.tooltip_text = "Photos, sketches or Krita boards"
	tile.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	UI.bind_height(tile, "sm")
	tile.pressed.connect(add)
	return tile


## Bars in the page's shape while the rules load: five swatches, three tiles and two lines on
## surface_raised, breathing together.
static func skeleton() -> VBoxContainer:
	var block := VBoxContainer.new()
	block.name = "Skeleton"
	block.theme_type_variation = "JoineryStack"
	block.mouse_filter = Control.MOUSE_FILTER_IGNORE
	block.accessibility_name = "Loading the look"
	var strip := HBoxContainer.new()
	strip.theme_type_variation = "JoineryFlush"
	block.add_child(strip)
	for index: int in EMPTY_ROLES:
		strip.add_child(_bar("md", 0.0))
	var tiles := HBoxContainer.new()
	tiles.theme_type_variation = "JoineryRow"
	block.add_child(tiles)
	for index: int in 3:
		tiles.add_child(_bar("", 0.75))
	block.add_child(UI.skeleton(2))
	block.tree_entered.connect(func() -> void: UI_MOTION.breathe(block))
	return block


static func _bar(height: String, ratio: float) -> Panel:
	var bar := Panel.new()
	bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
	bar.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	STYLE.on_theme_overrides(
		bar,
		func() -> void:
			var box := StyleBoxFlat.new()
			box.bg_color = bar.get_theme_color("surface_raised", "Joinery")
			box.set_corner_radius_all(UI_THEME.radius(bar, "sm" if not height.is_empty() else "md"))
			bar.add_theme_stylebox_override("panel", box)
			if not height.is_empty():
				bar.custom_minimum_size.y = UI_THEME.swatch_h(bar, height)
	)
	if ratio > 0.0:
		bar.resized.connect(func() -> void: bar.custom_minimum_size.y = roundf(bar.size.x * ratio))
	return bar


static func _add_tag(tags: Array[String], phrase: String) -> void:
	var text := phrase.strip_edges().trim_suffix(".").strip_edges()
	if text.is_empty() or text.length() > 32:
		return
	text = text[0].to_upper() + text.substr(1)
	if not tags.has(text):
		tags.append(text)


## Markdown line as plain words: list markers and bold marks gone.
static func _plain(line: String) -> String:
	var text := line.strip_edges()
	for mark: String in ["- ", "* ", "+ "]:
		text = text.trim_prefix(mark)
	return text.replace("**", "").replace("`", "").strip_edges()
