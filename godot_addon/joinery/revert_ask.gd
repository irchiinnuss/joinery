@tool
extends MarginContainer
## The question asked in place before a Revert runs, and before Undo revert replaces files edited
## since the revert. No dialog: it opens under the row or card that asked, says plainly what
## happens ("Revert “Hedge has small leaves…”? Its 7 files go back to how they were before it.
## You can undo this."), and offers the action and Cancel. Focus starts on Cancel, so Enter or
## Space right after a stray click never runs it; Escape answers Cancel too. Every place with a
## Revert button asks through this one (the chat's change cards, the feature plan card, the
## Review tab's decided rows), so the words and the order never differ.

## The user answered: [param ok] is true for the action, false for Cancel or Escape.
signal answered(ok: bool)

const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const STYLE := preload("sidebar_style.gd")
## A change's name is cut to about this many characters, at a word, in the question.
const NAME_CHARS := 28
const SELF_PATH := "res://addons/joinery/revert_ask.gd"

## The action's button (Revert, Undo, Replace them).
var action: Button
## Cancel: focused when the question opens.
var cancel: Button
## The question's words.
var text: Label


## [param summary] on one line, cut at a word to about [constant NAME_CHARS] with "…".
static func short_name(summary: String) -> String:
	var name := " ".join(summary.split("\n", false)).strip_edges()
	if name.length() <= NAME_CHARS:
		return name
	var cut := name.substr(0, NAME_CHARS)
	var space := cut.rfind(" ")
	if space * 2 > NAME_CHARS:
		cut = cut.substr(0, space)
	return cut.strip_edges().rstrip(",.;:") + "…"


## "Revert “Hedge has small leaves…”? Its 7 files go back to how they were before it. You can
## undo this." [param verb] is the button's word ("Revert", or "Undo" for a change kept
## without asking); [param files] is how many files the change has, 0 when not known.
static func revert_words(summary: String, files: int, verb := "Revert") -> String:
	var name := short_name(summary)
	var asked := "%s “%s”?" % [verb, name] if not name.is_empty() else "%s this change?" % verb
	var files_back := (
		"Its files go back"
		if files <= 0
		else ("Its file goes back" if files == 1 else "Its %d files go back" % files)
	)
	return "%s %s to how they were before it. You can undo this." % [asked, files_back]


## The question before a Revert of [param summary] with [param files] files.
static func for_revert(summary: String, files: int, verb := "Revert") -> Node:
	var ask: Node = load(SELF_PATH).new()
	ask.call("setup", revert_words(summary, files, verb), verb, "Cancel")
	return ask


## The question when Undo revert found files edited since the revert: [param message] is
## Joinery's (it names them). Keeping them is the safe answer and is focused.
static func for_conflict(message: String) -> Node:
	var ask: Node = load(SELF_PATH).new()
	ask.call("setup", message, "Replace them", "Keep my files")
	ask.set("name", "RevertConflict")
	return ask


func _init() -> void:
	name = "RevertAsk"
	theme_type_variation = "JoineryInset"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryRow"
	add_child(column)
	text = UI.label("", "JoineryCaption", true)
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	column.add_child(text)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	row.alignment = BoxContainer.ALIGNMENT_END
	column.add_child(row)
	cancel = UI.secondary("Cancel")
	cancel.name = "Cancel"
	STYLE.on_card(cancel)
	cancel.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	cancel.pressed.connect(_answer.bind(false))
	row.add_child(cancel)
	action = UI.primary("Revert")
	action.name = "Action"
	action.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	action.pressed.connect(_answer.bind(true))
	row.add_child(action)


## The words, the action's label and Cancel's label.
func setup(words: String, verb: String, cancel_label: String) -> void:
	text.text = words
	accessibility_name = words
	action.text = verb
	action.accessibility_name = verb
	cancel.text = cancel_label
	cancel.accessibility_name = cancel_label


func _ready() -> void:
	UI_MOTION.fade_in(self)
	# Weak: the question may be answered and freed before the next frame.
	var button := weakref(cancel)
	var focus := func() -> void:
		var target := button.get_ref() as Control
		if target != null and target.is_inside_tree() and target.is_visible_in_tree():
			target.grab_focus()
	focus.call_deferred()


func _input(event: InputEvent) -> void:
	if not is_visible_in_tree() or not event.is_action_pressed("ui_cancel"):
		return
	var focused := get_viewport().gui_get_focus_owner()
	if focused != null and is_ancestor_of(focused):
		get_viewport().set_input_as_handled()
		_answer(false)


func _answer(ok: bool) -> void:
	if is_queued_for_deletion():
		return
	answered.emit(ok)
	queue_free()
