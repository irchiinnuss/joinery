@tool
extends VBoxContainer
## The conversation before its first message (chat_history.gd shows it while the chat is empty).
##
## With an agent it starts from what is selected or open, not from a generic question: the
## thing's icon or thumbnail, its name and what it is ("Door", "MeshInstance3D in visual.tscn"),
## then three requests that fit it as round chips (sidebar_start.gd: a mesh offers "Make it
## taller", "Change its material", "Add a sound to it"; nothing selected offers scene requests).
## A click writes the request in the message box. The chips rest muted while the connection is
## lost. The block sits at the foot of the transcript, right above the message box it fills.
##
## Without an agent (the "none" state) the agents Joinery can use are the choices, inline: one
## row per agent with its mark, grouped on one card; a click opens the Connect screen with that
## agent's sign-in already started. "Use an API key" opens the same screen. While it connects
## the rows give way to "Connecting…" with the activity dots; once connected the start view
## comes back with a pulse.

signal example_chosen(text: String)
signal connect_requested

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const START := preload("sidebar_start.gd")
const AGENT_ICON := preload("agent_icon.gd")
const CHIP := preload("target_chip.gd")
## The agents offered without one connected, in order, with their names.
const AGENTS := {
	"claude": "Claude Code",
	"codex": "Codex",
	"gemini": "Gemini",
	"opencode": "opencode",
}
const HINT := "Select something, then say what to change."
## The agent row last pressed, read once by the editor plugin as the agent to sign in to.
const CHOSEN_META := "joinery_chosen_agent"

## The request chips, in order.
var examples: Array[Button] = []
## The start view (what is selected, and its requests) and the agent choices without an agent.
var start: VBoxContainer
var blank: VBoxContainer
## One row per agent on the choices card.
var agent_rows: Array[Button] = []
var _top: Control
var _tile: PanelContainer
var _picture: TextureRect
var _name: Label
var _line: Label
var _chips: HFlowContainer
var _choices: PanelContainer
var _api: Button
var _connecting: HBoxContainer
var _indicator: Control
var _scalables: Array = []
var _state := "connected"
var _height := 0.0
var _context := {}


func _init() -> void:
	name = "Empty"
	theme_type_variation = "JoineryFlush"
	_top = Control.new()
	_top.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_top)
	start = VBoxContainer.new()
	start.name = "Start"
	start.theme_type_variation = "JoineryStack"
	add_child(start)
	var head := HBoxContainer.new()
	head.name = "Context"
	head.theme_type_variation = "JoineryRow"
	start.add_child(head)
	_tile = PanelContainer.new()
	_tile.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(_tile)
	_picture = STYLE.glyph("text_muted")
	_tile.add_child(_picture)
	STYLE.on_theme_overrides(_tile, _style_tile)
	var words := VBoxContainer.new()
	words.theme_type_variation = "JoineryFlush"
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(words)
	_name = STYLE.card_title("", true)
	_name.name = "Name"
	words.add_child(_name)
	_scalables.append([_name, "body"])
	_line = UI.label(HINT, "JoineryCaption")
	_line.name = "Line"
	words.add_child(_line)
	_scalables.append([_line, "caption"])
	_chips = HFlowContainer.new()
	_chips.name = "Examples"
	STYLE.on_theme_overrides(
		_chips,
		func() -> void:
			var gap := UI_THEME.space(_chips, 2)
			_chips.add_theme_constant_override("h_separation", gap)
			_chips.add_theme_constant_override("v_separation", gap)
	)
	start.add_child(_chips)
	_build_blank()
	set_context([], "")


func _build_blank() -> void:
	blank = VBoxContainer.new()
	blank.name = "Agents"
	blank.theme_type_variation = "JoineryRow"
	blank.hide()
	add_child(blank)
	var title := STYLE.card_title("Connect an agent")
	title.name = "Heading"
	blank.add_child(title)
	_scalables.append([title, "body"])
	var line := UI.label("Journeyman works through the one you use.", "JoineryCaption")
	blank.add_child(line)
	_scalables.append([line, "caption"])
	_choices = PanelContainer.new()
	_choices.name = "Choices"
	STYLE.boxed(_choices, "JoineryChangeCard", 1)
	STYLE.on_theme_overrides(
		_choices,
		func() -> void:
			var box := _choices.get_theme_stylebox("panel").duplicate() as StyleBox
			box.content_margin_top = UI_THEME.space(_choices, 1)
			box.content_margin_bottom = UI_THEME.space(_choices, 1)
			_choices.add_theme_stylebox_override("panel", box)
	)
	blank.add_child(_choices)
	var rows := VBoxContainer.new()
	rows.theme_type_variation = "JoineryFlush"
	_choices.add_child(rows)
	for id: String in AGENTS:
		if not agent_rows.is_empty():
			rows.add_child(HSeparator.new())
		var row := _agent_row(id, str(AGENTS[id]))
		rows.add_child(row)
		agent_rows.append(row)
	_api = UI.quiet_link("Use an API key")
	_api.tooltip_text = "Connect with a key from Anthropic, OpenAI, Google or another provider"
	_api.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_api.pressed.connect(_choose.bind(""))
	blank.add_child(_api)
	_connecting = HBoxContainer.new()
	_connecting.name = "Connecting"
	_connecting.theme_type_variation = "JoineryRow"
	_connecting.hide()
	_indicator = INDICATOR.new()
	_connecting.add_child(_indicator)
	_connecting.add_child(UI.label("Connecting…", "JoineryCaption"))
	blank.add_child(_connecting)


## One agent's row: its mark, its name, a chevron at the trailing edge; the row is the action.
func _agent_row(id: String, label: String) -> Button:
	var row := Button.new()
	row.name = id
	row.theme_type_variation = "JoineryRowButton"
	row.accessibility_name = "Connect " + label
	row.tooltip_text = "Sign in with " + label
	row.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	UI.bind_height(row, "lg", false)
	UI_MOTION.press_feedback(row)
	var inside := HBoxContainer.new()
	inside.theme_type_variation = "JoineryRow"
	inside.mouse_filter = Control.MOUSE_FILTER_IGNORE
	inside.set_anchors_preset(Control.PRESET_FULL_RECT)
	row.add_child(inside)
	STYLE.on_theme(
		row,
		func() -> void:
			var pad := UI_THEME.space(row, 2)
			inside.offset_left = pad
			inside.offset_right = -pad
	)
	inside.add_child(AGENT_ICON.tile(id, label))
	var words := UI.label(label)
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	words.mouse_filter = Control.MOUSE_FILTER_IGNORE
	inside.add_child(words)
	_scalables.append([words, "body"])
	var more := STYLE.glyph("text_muted")
	STYLE.set_glyph(more, "disclosure_closed")
	inside.add_child(more)
	row.pressed.connect(_choose.bind(id))
	return row


func _choose(id: String) -> void:
	if id.is_empty():
		if Engine.has_meta(CHOSEN_META):
			Engine.remove_meta(CHOSEN_META)
	else:
		Engine.set_meta(CHOSEN_META, id)
	connect_requested.emit()


func _style_tile() -> void:
	var side := UI_THEME.control_h(_tile, "md")
	_tile.custom_minimum_size = Vector2(side, side)
	var box := StyleBoxFlat.new()
	box.bg_color = _tile.get_theme_color("surface_raised", "Joinery")
	box.set_corner_radius_all(UI_THEME.radius(_tile, "md"))
	box.anti_aliasing = true
	var pad := float(UI_THEME.space(_tile, 2))
	box.set_content_margin_all(pad)
	_tile.add_theme_stylebox_override("panel", box)


## Controls whose text follows the text size setting, with their TYPE step.
func scalables() -> Array:
	return _scalables


## Start from [param targets] (the message targets, first one leads) in the scene
## [param scene] ("" when none is open): the name, what it is, and three requests for it.
func set_context(targets: Array, scene := "") -> void:
	var context := START.describe(targets, scene)
	if context == _context:
		return
	_context = context
	var named := not str(context["name"]).is_empty()
	_name.text = str(context["name"])
	_name.visible = named
	_line.text = str(context["line"])
	_line.tooltip_text = _line.text
	_tile.visible = named
	STYLE.set_glyph(_picture, str(context["icon"]) if named else "scene")
	if named and not str(context["resource"]).is_empty():
		CHIP._request_thumbnail(_tile, _picture, str(context["resource"]))
	for chip: Button in examples:
		_chips.remove_child(chip)
		chip.queue_free()
	examples.clear()
	for text: String in context["actions"]:
		var chip := Button.new()
		chip.text = text
		chip.theme_type_variation = "JoineryActionChip"
		chip.tooltip_text = "Write this in the message box"
		UI.bind_height(chip, "sm", false)
		UI_MOTION.press_feedback(chip)
		chip.pressed.connect(func() -> void: example_chosen.emit(text))
		_chips.add_child(chip)
		examples.append(chip)
	_apply_connection()
	if start.visible and is_visible_in_tree():
		UI_MOTION.stagger(examples)
	fit.call_deferred(_height)


## Follow the connection: "none" shows the agent choices, "connecting" after it keeps them
## with "Connecting…", anything else the start view (requests act only connected).
func set_connection(state: String) -> void:
	var was_blank := blank.visible
	var blank_now := state == "none" or (state == "connecting" and was_blank)
	_state = state
	_apply_connection()
	var linking := state == "connecting" and blank_now
	_connecting.visible = linking
	_indicator.set("running", linking)
	_choices.visible = not linking
	_api.visible = not linking
	if blank_now == was_blank:
		return
	blank.visible = blank_now
	start.visible = not blank_now
	if blank_now:
		UI_MOTION.enter(blank)
		UI_MOTION.stagger(agent_rows)
	else:
		UI_MOTION.enter(start)
		UI_MOTION.stagger(examples)
		if was_blank and state == "connected":
			UI_MOTION.pulse(start)
	fit.call_deferred(_height)


func _apply_connection() -> void:
	var on := _state == "connected"
	for chip: Button in examples:
		chip.disabled = not on
		chip.focus_mode = Control.FOCUS_ALL if on else Control.FOCUS_NONE
		chip.tooltip_text = "Write this in the message box" if on else "Connect to use this"


## Rest the block at the foot of a transcript [param height] px tall, over the message box.
func fit(height: float) -> void:
	_height = height
	var shown: Control = blank if blank.visible else start
	var room := height - shown.get_combined_minimum_size().y - UI_THEME.space(self, 5)
	_top.custom_minimum_size.y = roundf(maxf(0.0, room))
