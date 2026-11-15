@tool
extends HBoxContainer
## "Not played since this change": the quiet line under an accepted change's decision line while
## nobody has run the game since it was accepted (joinery/played.py, played_watch.gd). It sits
## right under the decision line wherever the transcript put that line.

const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const TEXT := "Not played since this change"


func _init() -> void:
	theme_type_variation = "JoineryRowTight"
	accessibility_name = TEXT
	tooltip_text = "Nobody has run the game since this change was accepted"
	mouse_filter = Control.MOUSE_FILTER_PASS
	var glyph := STYLE.glyph("text_muted")
	STYLE.set_glyph(glyph, "play", "text_muted")
	add_child(glyph)
	var words := UI.label(TEXT, "JoineryCaption", true)
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(words)
	hide()


## Show it under [param outcome] (in [param fallback] while the decision line has no place yet)
## when [param waiting], else let it go. Returns whether it changed.
func sync(waiting: bool, outcome: Control, fallback: Control) -> bool:
	if waiting == visible:
		return false
	if not waiting:
		UI_MOTION.exit(self)
		return true
	var parent := outcome.get_parent() if outcome.get_parent() != null else fallback
	if get_parent() != parent:
		if get_parent() != null:
			get_parent().remove_child(self)
		parent.add_child(self)
	if outcome.get_parent() == parent:
		parent.move_child(self, outcome.get_index() + 1)
	show()
	UI_MOTION.fade_in(self)
	return true
