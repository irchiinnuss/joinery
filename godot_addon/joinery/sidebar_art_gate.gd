@tool
extends PanelContainer
## The art gate's card in a request's card (sidebar_change_card.gd): asset work waits until the
## look is approved. A miniature of the look leads it (the palette as a 24 px strip and up to
## three reference thumbnails, five dashed swatches when nothing is chosen yet), then "Agree the
## look first", then "Review the look" (the card's one accent) and the quiet "Describe my own".
## The step above it reads "Waiting for the look"; once the look is approved the card leaves.

signal review_requested
signal describe_requested

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const BLOCKS := preload("ui_blocks.gd")
const LOOK := preload("art_look.gd")
const HEADING := "Agree the look first"
## Thumbnails in the miniature.
const THUMBS := 3

var _preview: HBoxContainer
var _heading: Label


func _init() -> void:
	name = "ArtGate"
	theme_type_variation = "JoineryChangeCard"
	accessibility_name = "The look needs agreeing first"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	STYLE.boxed(self, "JoineryChangeCard", 4)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryStack"
	add_child(column)
	_preview = HBoxContainer.new()
	_preview.name = "Preview"
	_preview.theme_type_variation = "JoineryRow"
	column.add_child(_preview)
	_heading = STYLE.card_title(HEADING, true)
	_heading.name = "Heading"
	column.add_child(_heading)
	var actions := HFlowContainer.new()
	actions.name = "Actions"
	STYLE.on_theme_overrides(
		actions,
		func() -> void:
			var gap := UI_THEME.space(actions, 3)
			actions.add_theme_constant_override("h_separation", gap)
			actions.add_theme_constant_override("v_separation", UI_THEME.space(actions, 1))
	)
	column.add_child(actions)
	var review := UI.primary("Review the look")
	review.name = "Review"
	review.accessibility_name = "Review the look"
	review.pressed.connect(
		func() -> void:
			UI_MOTION.press_feedback(review)
			review_requested.emit()
	)
	UI.bind_height(review, "md")
	actions.add_child(review)
	var own := UI.quiet_link("Describe my own")
	own.name = "Describe"
	own.accessibility_name = "Describe how your game should look"
	own.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	own.pressed.connect(func() -> void: describe_requested.emit())
	UI.bind_height(own, "md")
	actions.add_child(own)
	show_look({})


## Show the miniature of [param look] ({palette, references}, as the bridge sends them).
func show_look(look: Dictionary) -> void:
	for child: Node in _preview.get_children():
		_preview.remove_child(child)
		child.queue_free()
	var strip := UI.palette_strip(LOOK.roles(look), "sm", false)
	strip.name = "Palette"
	strip.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	strip.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	(strip.get_node("Swatches") as Control).focus_mode = Control.FOCUS_NONE
	_preview.add_child(strip)
	var shown := 0
	for ref: Dictionary in LOOK.references(look):
		if shown >= THUMBS:
			break
		var texture := BLOCKS.picture(str(ref.get("image", "")), self)
		if texture == null:
			continue
		_preview.add_child(_thumb(texture, str(ref.get("title", ""))))
		shown += 1


## The heading on show.
func heading() -> String:
	return _heading.text


func _thumb(texture: Texture2D, title: String) -> Control:
	var frame := Panel.new()
	frame.name = "Thumb"
	frame.tooltip_text = title
	frame.clip_children = CanvasItem.CLIP_CHILDREN_AND_DRAW
	frame.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	var picture := TextureRect.new()
	picture.texture = texture
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
	picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
	picture.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	frame.add_child(picture)
	STYLE.on_theme_overrides(
		frame,
		func() -> void:
			# 40 logical px.
			var side := float(UI_THEME.space(frame, 6) + UI_THEME.space(frame, 2))
			frame.custom_minimum_size = Vector2(side, side)
			var box := StyleBoxFlat.new()
			box.bg_color = frame.get_theme_color("surface_sunken", "Joinery")
			box.set_corner_radius_all(UI_THEME.radius(frame, "sm"))
			frame.add_theme_stylebox_override("panel", box)
	)
	return frame
