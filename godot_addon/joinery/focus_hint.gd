@tool
extends PanelContainer
## The first time Focus mode hides the panels, a small popover grows from the ⤢ button (0.95 to
## 1 over MOTION.menu, from the trigger) saying "Panels hidden" with Undo, and leaves after
## [constant SECONDS]. After that the button's pressed state says it alone; nothing is written
## into the chat. Seen once per user (journeyman.cfg, [journeyman] focus_hint_seen).

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const SETTINGS := preload("journeyman_settings.gd")
const SECONDS := 4.0
const SEEN_KEY := "focus_hint_seen"

var _trigger: Control


## Show the popover under [param trigger] the first time only; Undo runs [param undo]. Returns
## false when it was already seen (or there is no trigger), so the caller stays quiet.
static func show_once(trigger: Control, undo: Callable, force := false) -> bool:
	if trigger == null or not trigger.is_inside_tree():
		return false
	var file := ConfigFile.new()
	file.load(SETTINGS.config_path())
	if bool(file.get_value("journeyman", SEEN_KEY, false)) and not force:
		return false
	file.set_value("journeyman", SEEN_KEY, true)
	DirAccess.make_dir_recursive_absolute(SETTINGS.config_path().get_base_dir())
	file.save(SETTINGS.config_path())
	var hint: PanelContainer = load("res://addons/joinery/focus_hint.gd").new()
	hint.call("_build", trigger, undo)
	return true


func _build(trigger: Control, undo: Callable) -> void:
	_trigger = trigger
	name = "JoineryFocusHint"
	theme_type_variation = "JoineryHoverCard"
	top_level = true
	theme = UI_THEME.build(trigger)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	add_child(row)
	var words := UI.label("Panels hidden", "JoineryCaption")
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(words)
	var again := UI.link("Undo")
	again.accessibility_name = "Show the panels again"
	UI.bind_height(again, "xs")
	again.pressed.connect(
		func() -> void:
			undo.call()
			_leave()
	)
	row.add_child(again)
	trigger.get_window().add_child(self)
	_place.call_deferred()
	get_tree().create_timer(SECONDS).timeout.connect(_leave)


## Under the trigger, its trailing edge on the trigger's, inside the window.
func _place() -> void:
	if not is_instance_valid(_trigger):
		queue_free()
		return
	reset_size()
	var rect := _trigger.get_global_rect()
	var gap := float(UI_THEME.space(self, 1))
	var at := Vector2(rect.end.x - size.x, rect.end.y + gap)
	var room := get_viewport_rect().size
	at.x = clampf(at.x, gap, maxf(gap, room.x - size.x - gap))
	global_position = at
	UI_MOTION.enter(self, _trigger)


func _leave() -> void:
	if is_queued_for_deletion() or not is_inside_tree():
		return
	UI_MOTION.exit(self, _trigger if is_instance_valid(_trigger) else null, Callable(), true)
