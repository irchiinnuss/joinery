@tool
extends VBoxContainer
## The headline of a ready change's card, before Accept: what the change made or changed, by
## name and with the tool that owns it ("Engine sound in SuperCollider"), and two quiet
## buttons, "Preview" and "Ask to fix" ([member links]; the change card puts them on its row of
## quiet buttons beside "Try other ways"). Preview shows that asset in the Review panel's
## preview (a model turning, a sound to play, before and after); Ask to fix puts the asset on
## the message and focuses the message box, so Journeyman can be asked to fix it first.
##
## Never a popup, a sound or a stack of toasts: one line in body text at the top of the card,
## faded in once (still with reduced motion). Accept and Discard stay as they are.

signal preview_requested(path: String)
signal fix_requested(path: String)

const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const STYLE := preload("sidebar_style.gd")
const OWNERS := preload("review_owners.gd")

## Preview and Ask to fix, shown while the note is: a holder may move them to its own row.
var links: HBoxContainer
var _text: Label
var _preview: Button
var _fix: Button
var _path := ""
var _shown := ""


func _init() -> void:
	name = "PreviewNote"
	theme_type_variation = "JoineryFlush"
	accessibility_name = "Look before you accept"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_text = UI.label("", "", true)
	_text.name = "Changed"
	_text.mouse_filter = Control.MOUSE_FILTER_PASS
	add_child(_text)
	links = HBoxContainer.new()
	links.name = "Links"
	links.theme_type_variation = "JoineryRow"
	links.hide()
	add_child(links)
	_preview = _action("Preview", "Look at it, or listen, before you accept")
	_preview.pressed.connect(func() -> void: preview_requested.emit(_path))
	# Its words on the text's own edge; the hover fill reaches past it.
	STYLE.hug_left(_preview)
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
	_preview.accessibility_name = "Preview " + line
	_fix.visible = can_fix
	var was := visible
	visible = not line.is_empty()
	links.visible = visible
	if visible and (not was or _shown != line):
		UI_MOTION.fade_in(self)
	_shown = line if visible else ""


## The note's words ("" when hidden).
func text() -> String:
	return _text.text if visible else ""


## A quiet button, like every one on the change card.
func _action(words: String, hint: String) -> Button:
	var button := UI.quiet(words)
	button.name = words.replace(" ", "")
	button.tooltip_text = hint
	button.accessibility_name = hint
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI.bind_height(button, "xs")
	links.add_child(button)
	return button
