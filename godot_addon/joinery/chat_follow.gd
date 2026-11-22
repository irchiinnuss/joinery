@tool
extends PanelContainer
## Keeps the conversation's end in view while new output arrives (a reply streaming in, the
## work trail's live line, a card that grew), but only while the user is at or near the
## bottom. Scrolled up to read, they stay where they are; a small, calm "New" pill floats over
## the bottom edge instead, and pressing it (or scrolling back down) follows again. Sending a
## message always follows ([method follow]).
##
## The pill is internal to the transcript (chat_history.gd) and top level, so it takes no
## layout height. It fades in and out (instant with reduced motion); it never bounces or
## stacks.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")

## True while the transcript keeps its end in view.
var following := true
var _scroll: ScrollContainer
var _jump: Button
var _leaving := false
## While older turns are put above the ones on screen: the distance from the end to keep, and
## the last frame to keep it in (chat_history.gd _prepend).
var _anchor := 0.0
var _anchor_until := -1


func _init() -> void:
	name = "NewBelow"
	top_level = true
	z_index = 1
	visible = false
	mouse_filter = Control.MOUSE_FILTER_STOP
	accessibility_name = "New messages below"
	STYLE.on_theme_overrides(
		self,
		func() -> void:
			var box := StyleBoxFlat.new()
			box.bg_color = get_theme_color("surface_raised", "Joinery")
			box.set_corner_radius_all(UI_THEME.radius(self, "full"))
			var pad := float(UI_THEME.space(self, 1))
			box.content_margin_left = pad
			box.content_margin_right = pad
			add_theme_stylebox_override("panel", box)
	)
	_jump = UI.quiet("New")
	_jump.name = "Jump"
	_jump.tooltip_text = "Scroll to the newest"
	_jump.accessibility_name = "Scroll to the newest"
	UI.bind_icon(_jump, "disclosure_open")
	UI.bind_height(_jump, "xs")
	_jump.pressed.connect(follow)
	add_child(_jump)


## Watch [param scroll]: its user's scrolling decides [member following], and its content
## growing keeps the end in view or offers the pill.
func watch(scroll: ScrollContainer) -> void:
	_scroll = scroll
	scroll.add_child(self, false, Node.INTERNAL_MODE_BACK)
	var bar := scroll.get_v_scroll_bar()
	bar.value_changed.connect(_on_scroll)
	bar.changed.connect(_on_grown)


## Back to the end, and keep following.
func follow() -> void:
	following = true
	_hide_pill()
	to_end()


## Scroll to the end once layout has settled (the newest call wins).
func to_end() -> void:
	if is_instance_valid(_scroll):
		_end.call_deferred()


## Older content is going in above: keep the view [param distance] from the end, so what the
## user reads stays put and no pill offers "New" for it.
func keep_from_end(distance: float) -> void:
	_anchor = distance
	_anchor_until = Engine.get_process_frames() + 2


## True while the pill offers newer output below.
func offering() -> bool:
	return visible and not _leaving


func _end() -> void:
	if is_instance_valid(_scroll):
		_scroll.scroll_vertical = int(_scroll.get_v_scroll_bar().max_value)


func _on_scroll(value: float) -> void:
	var bar := _scroll.get_v_scroll_bar()
	following = value >= bar.max_value - bar.page - UI_THEME.space(_scroll, 6)
	if following:
		_hide_pill()


## The content grew (or shrank): follow it, or say there is more below.
func _on_grown() -> void:
	if following:
		to_end()
		return
	var bar := _scroll.get_v_scroll_bar()
	if Engine.get_process_frames() <= _anchor_until:
		_scroll.scroll_vertical = int(bar.max_value - _anchor)
		return
	if bar.max_value - bar.page - _scroll.scroll_vertical > UI_THEME.space(_scroll, 6):
		_show_pill()


func _show_pill() -> void:
	if visible and not _leaving:
		return
	_leaving = false
	_place()
	UI_MOTION.fade_in(self)


func _hide_pill() -> void:
	if not visible or _leaving:
		return
	_leaving = true
	UI_MOTION.exit(self, null, func() -> void: _leaving = false)


func _process(_delta: float) -> void:
	if visible:
		_place()


## Centred over the transcript's bottom edge, clear of its scrollbar.
func _place() -> void:
	if not is_instance_valid(_scroll):
		return
	var wanted := get_combined_minimum_size()
	size = wanted
	var rect := _scroll.get_global_rect()
	var bar := _scroll.get_v_scroll_bar()
	var width := rect.size.x - bar.get_minimum_size().x
	var inset := float(UI_THEME.space(_scroll, 2))
	global_position = Vector2(
		rect.position.x + (width - wanted.x) / 2.0, rect.end.y - wanted.y - inset
	)
