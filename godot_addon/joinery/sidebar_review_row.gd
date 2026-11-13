@tool
extends PanelContainer
## The change waiting for a decision, inside its chat card: the change large (its turntable or
## picture at 16:9 across the card, at most 360 px tall), one caption line naming it
## ("door.blend · +1 sound ›", the chevron opens the Review panel), and the decision row:
## "Try other ways" quiet on the leading edge, Discard and Accept (the view's one accent) on the
## trailing edge. In a narrow sidebar "Try other ways" takes its own line above the buttons. A
## bigger change (several assets, many lines, a reviewer's warning) offers "Review 2 changes"
## instead, which brings the panel into view. Both go through the panel (review_panel.gd), so
## the decision looks and behaves the same wherever it is made. ⌘↩ accepts and ⌘⌫ discards
## while the message box is empty or not focused. "Try other ways" asks for a few more versions
## of the same request side by side, this one kept as the first (ADR 0042).

## "Try other ways": compare this change with a few other versions.
signal other_ways_pressed

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const GLANCE := preload("review_glance.gd")
## The preview is 16:9 and at most this tall (logical px).
const PREVIEW_MAX_H := 360
const PREVIEW_RATIO := 9.0 / 16.0

## "Try other ways", while the change waits for a decision.
var ways: Button
## True while the card's change waits for a decision: the panel's later updates (another
## change's review) never bring a decided row back.
var waiting := false
var _review: Object
var _frame: PanelContainer
var _thumb: TextureRect
var _line: Label
var _details: Button
var _decision: HBoxContainer
## "Try other ways" on a line of its own, in a narrow sidebar.
var _ways_line: HBoxContainer
var _actions: HBoxContainer
var _discard: Button
var _accept: Button
var _open: Button


func _init() -> void:
	theme_type_variation = "JoineryChangeCard"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Change waiting for your decision"
	STYLE.boxed(self, "JoineryChangeCard", 3)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryRow"
	add_child(column)
	_frame = PanelContainer.new()
	_frame.name = "Preview"
	_frame.clip_children = CanvasItem.CLIP_CHILDREN_AND_DRAW
	_frame.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	column.add_child(_frame)
	_thumb = TextureRect.new()
	_thumb.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_thumb.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_thumb.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_frame.add_child(_thumb)
	STYLE.on_theme_overrides(
		_frame,
		func() -> void:
			var box := StyleBoxFlat.new()
			# The picture's own ground fills the 16:9 frame around it, so it reads as one image.
			var tone := GLANCE.ground(_thumb.texture)
			box.bg_color = (
				tone if tone.a > 0.0 else _frame.get_theme_color("surface_sunken", "Joinery")
			)
			# Nested in the card: its radius less the card's padding.
			var outer := float(UI_THEME.radius(_frame, "lg"))
			var inner := UI_THEME.nested(outer, float(UI_THEME.space(_frame, 3)))
			box.set_corner_radius_all(roundi(maxf(inner, float(UI_THEME.radius(_frame, "xs")))))
			box.anti_aliasing = true
			_frame.add_theme_stylebox_override("panel", box)
	)
	_frame.resized.connect(_fit_preview)
	# The caption: what changed, by name, with a chevron into the panel's details.
	var caption := HBoxContainer.new()
	caption.theme_type_variation = "JoineryRowTight"
	column.add_child(caption)
	_line = UI.label("", "JoineryCaption")
	_line.name = "Caption"
	_line.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_line.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_line.mouse_filter = Control.MOUSE_FILTER_PASS
	caption.add_child(_line)
	_details = UI.quiet_link("›")
	_details.name = "Details"
	_details.tooltip_text = "Look closer in the Review panel"
	_details.accessibility_name = "Open the review"
	UI.bind_height(_details, "xs")
	STYLE.hug_right(_details)
	_details.pressed.connect(_show_panel)
	STYLE.on_theme(
		_details,
		func() -> void: _details.custom_minimum_size.x = UI_THEME.control_h(_details, "xs")
	)
	caption.add_child(_details)
	_ways_line = HBoxContainer.new()
	_ways_line.theme_type_variation = "JoineryRow"
	_ways_line.hide()
	column.add_child(_ways_line)
	_decision = HBoxContainer.new()
	_decision.name = "Decision"
	_decision.theme_type_variation = "JoineryRow"
	# The buttons keep the trailing edge when "Try other ways" moves above them.
	_decision.alignment = BoxContainer.ALIGNMENT_END
	column.add_child(_decision)
	ways = UI.quiet_link("Try other ways")
	ways.accessibility_name = "Try other ways of this change"
	ways.tooltip_text = "Make a few other versions side by side and keep the one you like"
	ways.pressed.connect(func() -> void: other_ways_pressed.emit())
	ways.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	# It takes the room the buttons leave, clipped rather than widen the sidebar; a layout too
	# narrow for its words moves it to its own line ([method _fit_width]).
	ways.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	ways.alignment = HORIZONTAL_ALIGNMENT_LEFT
	ways.clip_text = true
	STYLE.hug_left(ways)
	# Full text colour: a real option beside the decision, not a faded one.
	STYLE.on_theme_overrides(
		ways,
		func() -> void:
			var ink := ways.get_theme_color("text", "Joinery")
			for key: String in ["font_color", "font_focus_color"]:
				ways.add_theme_color_override(key, ink)
	)
	_decision.add_child(ways)
	_actions = HBoxContainer.new()
	_actions.name = "Actions"
	STYLE.on_theme_overrides(
		_actions,
		func() -> void:
			_actions.add_theme_constant_override("separation", UI_THEME.space(_actions, 2))
	)
	_decision.add_child(_actions)
	_discard = UI.secondary("Discard")
	_discard.name = "Discard"
	_discard.accessibility_name = "Discard this change"
	_discard.tooltip_text = "Throw this change away. Your files stay as they are. (⌘⌫)"
	_discard.pressed.connect(_decide.bind(false))
	STYLE.on_card(_discard)
	_accept = UI.primary("Accept")
	_accept.name = "Accept"
	_accept.accessibility_name = "Accept this change"
	_accept.tooltip_text = "Keep this change (⌘↩)"
	_accept.pressed.connect(_decide.bind(true))
	# A bigger change's one action is still the card's call to action: filled like Accept.
	_open = UI.primary("Review")
	_open.name = "Review"
	_open.accessibility_name = "Open the review"
	_open.tooltip_text = "A bigger change: look at it in the Review panel"
	_open.pressed.connect(_show_panel)
	var ordered := (
		[_accept, _discard] if DisplayServer.get_swap_cancel_ok() else [_discard, _accept]
	)
	for button: Button in ordered + [_open]:
		UI.bind_height(button, "md")
		_actions.add_child(button)
	resized.connect(_fit_width)
	hide()


## Follow [param review] (review_panel.gd), or keep the one followed when null, and show
## [param media]'s first picture until the panel has its own.
func show_for(media: Array, review: Object = null) -> void:
	if review != null and review != _review:
		if _review != null and _review.has_signal("glance_changed"):
			_review.disconnect("glance_changed", refresh)
		_review = review
		if _review.has_signal("glance_changed"):
			_review.connect("glance_changed", refresh)
	refresh(media)


## Read the panel again: its line, picture, and whether the change is small.
func refresh(media: Array = []) -> void:
	var glance: Dictionary = {}
	if _review != null and _review.has_method("glance"):
		glance = _review.call("glance")
	var picture: Texture2D = glance.get("thumbnail") as Texture2D
	if picture == null:
		picture = first_picture(media)
	if _thumb.texture != picture:
		_thumb.texture = picture
		_frame.theme_changed.emit()
	_frame.visible = picture != null
	_line.text = str(glance.get("line", ""))
	_line.tooltip_text = str(glance.get("title", _line.text))
	var decidable := not glance.is_empty()
	_line.get_parent().visible = not _line.text.is_empty() or decidable
	_details.visible = decidable
	var simple := bool(glance.get("simple", false))
	var count := int(glance.get("count", 0))
	_decision.visible = decidable
	_discard.visible = simple
	_accept.visible = simple
	_open.visible = decidable and not simple
	_open.text = "Review %d changes" % count if count > 1 else "Review"
	ways.visible = decidable
	var was := visible
	visible = waiting and (_frame.visible or decidable)
	_fit_width.call_deferred()
	if visible and not was:
		UI_MOTION.reveal(self)


## The line of facts on show ("" without one).
func line_text() -> String:
	return _line.text


## The thumbnail on show (null without one).
func thumbnail() -> Texture2D:
	return _thumb.texture if _frame.visible else null


## True when Accept and Discard are offered here.
func decidable() -> bool:
	return visible and _accept.visible


## The first "after" image of [param media] whose file exists, as a texture.
static func first_picture(media: Array) -> Texture2D:
	for item: Variant in media:
		if not item is Dictionary:
			continue
		var entry := item as Dictionary
		# A render still running has no file yet; review_media brings it.
		if (
			entry.get("media") == "image"
			and entry.has("after")
			and FileAccess.file_exists(str(entry["after"]))
		):
			var image := Image.load_from_file(str(entry["after"]))
			if image != null and not image.is_empty():
				return ImageTexture.create_from_image(image)
	return null


## ⌘↩ accepts and ⌘⌫ discards a change decided here, unless something is being typed.
func _input(event: InputEvent) -> void:
	var key := event as InputEventKey
	if key == null or not key.pressed or key.echo or not key.is_command_or_control_pressed():
		return
	if not (is_visible_in_tree() and _accept.visible and waiting):
		return
	var accept := key.keycode in [KEY_ENTER, KEY_KP_ENTER]
	if not accept and key.keycode != KEY_BACKSPACE:
		return
	if _typing():
		return
	get_viewport().set_input_as_handled()
	if accept:
		UI_MOTION.press_feedback(_accept)
	_decide(accept)


## True while the focus is in a text field that holds text (the message box being written).
func _typing() -> bool:
	var focus := get_viewport().gui_get_focus_owner() if is_inside_tree() else null
	if focus is TextEdit:
		return not (focus as TextEdit).text.strip_edges().is_empty()
	if focus is LineEdit:
		return not (focus as LineEdit).text.strip_edges().is_empty()
	return false


func _decide(accepted: bool) -> void:
	if _review != null and _review.has_method("decide"):
		if accepted:
			UI_MOTION.pulse(self)
		_review.call("decide", accepted)


func _show_panel() -> void:
	if _review != null and _review.has_method("request_show"):
		_review.call("request_show")


## 16:9 across the card, at most PREVIEW_MAX_H tall.
func _fit_preview() -> void:
	var tall := minf(
		roundf(_frame.size.x * PREVIEW_RATIO), float(PREVIEW_MAX_H) * UI_THEME.unit(_frame)
	)
	if absf(_frame.custom_minimum_size.y - tall) >= 1.0:
		_frame.custom_minimum_size.y = tall


## "Try other ways" beside the buttons when they fit on one line, above them when not.
func _fit_width() -> void:
	if not is_inside_tree() or not ways.visible:
		_ways_line.hide()
		return
	var room := _decision.size.x
	var gap := float(UI_THEME.space(self, 3))
	var font := ways.get_theme_font("font")
	var natural := (
		font.get_string_size(ways.text, 0, -1, ways.get_theme_font_size("font_size")).x
		+ ways.get_theme_stylebox("normal").get_minimum_size().x
	)
	var need := natural + gap + _actions.get_combined_minimum_size().x
	var narrow := room > 0.0 and need > room
	if narrow and ways.get_parent() == _decision:
		ways.reparent(_ways_line, false)
	elif not narrow and ways.get_parent() == _ways_line:
		ways.reparent(_decision, false)
		_decision.move_child(ways, 0)
	_ways_line.visible = ways.get_parent() == _ways_line
