@tool
extends VBoxContainer
## The conversation before its first message (chat_history.gd shows it while the chat is empty).
##
## With an agent: "What should we change?", one caption line, and three example requests as
## full-width plain rows with a chevron, which stagger in. A click writes the example in the
## message box. The rows rest muted while the connection is lost.
##
## Without an agent (the "none" state) the whole block is a blankslate instead: an agent tile,
## "Connect an agent", Connect (the view's one accent action) and the quiet "Which one?", both
## opening the Connect screen. While it connects, the buttons give way to "Connecting…" with the
## activity dots; once connected the start view comes back with a pulse.
##
## The block sits in the upper third of the transcript ([method fit]).

signal example_chosen(text: String)
signal connect_requested

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const EXAMPLES := [
	"Make the selected model taller", "Add a footstep sound", "Repaint this texture warmer"
]
const HINT := "Select something, then say what to change."
## The block starts this far down the transcript: the upper third, never mid-screen.
const TOP_SHARE := 0.12

## The example rows, in order.
var examples: Array[Button] = []
## The start view (title, caption, examples) and the blankslate shown without an agent.
var start: VBoxContainer
var blank: VBoxContainer
var _top: Control
var _connect: Button
var _which: Button
var _connecting: HBoxContainer
var _indicator: Control
var _scalables: Array = []
var _state := "connected"


func _init() -> void:
	name = "Empty"
	theme_type_variation = "JoineryFlush"
	_top = Control.new()
	_top.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_top)
	start = VBoxContainer.new()
	start.name = "Start"
	add_child(start)
	STYLE.on_theme_overrides(
		start,
		func() -> void: start.add_theme_constant_override("separation", UI_THEME.space(start, 4))
	)
	var words := VBoxContainer.new()
	words.theme_type_variation = "JoineryTight"
	start.add_child(words)
	var title := UI.label("What should we change?", "JoineryTitle", true)
	words.add_child(title)
	_scalables.append([title, "title"])
	var hint := UI.label(HINT, "JoineryCaption", true)
	words.add_child(hint)
	_scalables.append([hint, "caption"])
	var rows := VBoxContainer.new()
	rows.name = "Examples"
	rows.theme_type_variation = "JoineryFlush"
	start.add_child(rows)
	for example: String in EXAMPLES:
		var row := UI.row_button(example)
		row.tooltip_text = "Write this in the message box"
		row.pressed.connect(func() -> void: example_chosen.emit(example))
		STYLE.hug_left(row)
		rows.add_child(row)
		_scalables.append([row, "body"])
		examples.append(row)
	_build_blank()


func _build_blank() -> void:
	_connect = UI.primary("Connect")
	_connect.accessibility_name = "Connect an agent"
	_connect.pressed.connect(func() -> void: connect_requested.emit())
	_which = UI.quiet_link("Which one?")
	_which.tooltip_text = "See the agents Joinery can use"
	_which.pressed.connect(func() -> void: connect_requested.emit())
	blank = UI.blankslate("chats", "Connect an agent", _connect, _which)
	blank.size_flags_vertical = Control.SIZE_FILL
	blank.hide()
	add_child(blank)
	_connecting = HBoxContainer.new()
	_connecting.name = "Connecting"
	_connecting.theme_type_variation = "JoineryRow"
	_connecting.alignment = BoxContainer.ALIGNMENT_CENTER
	_connecting.hide()
	_indicator = INDICATOR.new()
	_connecting.add_child(_indicator)
	_connecting.add_child(UI.label("Connecting…", "JoineryCaption"))
	blank.add_child(_connecting)


## Controls whose text follows the text size setting, with their TYPE step.
func scalables() -> Array:
	return _scalables


## Follow the connection: "none" shows the blankslate, "connecting" after it keeps the
## blankslate with "Connecting…", anything else the start view (examples live only connected).
func set_connection(state: String) -> void:
	var was_blank := blank.visible
	var blank_now := state == "none" or (state == "connecting" and was_blank)
	_state = state
	for row: Button in examples:
		var on := state == "connected"
		row.disabled = not on
		row.focus_mode = Control.FOCUS_ALL if on else Control.FOCUS_NONE
		row.tooltip_text = "Write this in the message box" if on else "Connect to use this"
	var linking := state == "connecting" and blank_now
	_connecting.visible = linking
	_indicator.set("running", linking)
	_connect.get_parent().visible = not linking
	if blank_now == was_blank:
		return
	blank.visible = blank_now
	start.visible = not blank_now
	if blank_now:
		UI_MOTION.enter(blank)
	else:
		UI_MOTION.enter(start)
		UI_MOTION.stagger(examples)
		if was_blank and state == "connected":
			UI_MOTION.pulse(start)


## Place the block in the upper third of a transcript [param height] px tall.
func fit(height: float) -> void:
	_top.custom_minimum_size.y = roundf(maxf(0.0, height) * TOP_SHARE)
