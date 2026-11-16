@tool
extends VBoxContainer
## A calm note on a ready change's card, before Accept: what the change made or changed, by
## name and with the tool that owns it ("Changed in SuperCollider: engine sound"), and under it
## two quiet ways on, "Preview · Ask to fix". Preview shows that asset in the Review panel's
## preview (a model turning, a sound to play, before and after); Ask to fix puts the asset on
## the message and focuses the message box, so Journeyman can be asked to fix it first.
##
## Never a popup, a sound or a stack of toasts: one line in the card, faded in once (still
## with reduced motion). Accept and Discard stay as they are.

signal preview_requested(path: String)
signal fix_requested(path: String)

const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const STYLE := preload("sidebar_style.gd")
const OWNERS := preload("review_owners.gd")

var _text: Label
var _links: HBoxContainer
var _preview: Button
var _fix: Button
var _joiner: Label
var _path := ""
var _shown := ""


func _init() -> void:
	name = "PreviewNote"
	theme_type_variation = "JoineryFlush"
	accessibility_name = "Look before you accept"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_text = UI.label("", "JoineryCaption", true)
	_text.name = "Changed"
	_text.mouse_filter = Control.MOUSE_FILTER_PASS
	add_child(_text)
	_links = HBoxContainer.new()
	_links.name = "Links"
	_links.theme_type_variation = "JoineryRowTight"
	add_child(_links)
	_preview = _action("Preview", "Look at it, or listen, before you accept")
	_preview.pressed.connect(func() -> void: preview_requested.emit(_path))
	# On the text's own edge, under "Changed".
	STYLE.hug_left(_preview)
	_joiner = UI.label("·", "JoineryCaption")
	_joiner.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_joiner.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_links.add_child(_joiner)
	_fix = _action("Ask to fix", "Ask Journeyman to fix it before you accept")
	_fix.pressed.connect(func() -> void: fix_requested.emit(_path))
	hide()


## Show [param items] (review_owners.gd's {path, name, kind, tool}); none hides the note.
## [param can_fix] offers Ask to fix.
func show_items(items: Array, can_fix: bool) -> void:
	var line := OWNERS.line(items)
	_path = str((items[0] as Dictionary).get("path", "")) if not items.is_empty() else ""
	_text.text = line
	var paths := PackedStringArray()
	for item: Variant in items:
		paths.append(str((item as Dictionary).get("path", "")))
	_text.tooltip_text = "\n".join(paths)
	_preview.accessibility_name = "Preview " + line.get_slice(": ", 1)
	_fix.visible = can_fix
	_joiner.visible = can_fix
	var was := visible
	visible = not line.is_empty()
	if visible and (not was or _shown != line):
		UI_MOTION.fade_in(self)
	_shown = line if visible else ""


## The note's words ("" when hidden).
func text() -> String:
	return _text.text if visible else ""


## A quiet link on the note's second line.
func _action(words: String, hint: String) -> Button:
	var button := UI.quiet_link(words)
	button.name = words.replace(" ", "")
	button.tooltip_text = hint
	button.accessibility_name = hint
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI.bind_height(button, "xs")
	_links.add_child(button)
	return button
