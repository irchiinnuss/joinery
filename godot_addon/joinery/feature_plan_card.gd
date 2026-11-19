@tool
extends PanelContainer
## The plan for adding or removing a game feature (`joinery feature … --plan --json`) as a
## card in the chat: what it adds, which settings change, and notes. Nothing changes until
## Apply. States: planned, applying, applied, cancelled, failed (Apply again), reverted.
## Revert asks first, in place (revert_ask.gd); a reverted plan offers Undo revert.

signal apply_pressed
signal cancel_pressed
signal revert_pressed(transaction_id: String)
## Undo revert; [param overwrite] replaces files edited since the revert.
signal unrevert_pressed(transaction_id: String, overwrite: bool)

const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const REVERT_ASK := preload("revert_ask.gd")
## Files listed before "and N more".
const MAX_FILES := 6

var state := "planned"
var plan: Dictionary = {}
var transaction_id := ""
var title_label: Label
var _column: VBoxContainer
var _lines: Array[Label] = []
var _headings: Array[Label] = []
var _error: Label
var _actions: HBoxContainer
var _apply: Button
var _cancel: Button
var _outcome: HBoxContainer
var _outcome_icon: TextureRect
var _outcome_text: Label
var _revert: Button
var _undo_revert: Button
## The question before Revert, or before replacing edited files (revert_ask.gd).
var _ask: Control
var _working: Control


func build(data: Dictionary, title: String) -> void:
	plan = data.duplicate(true)
	STYLE.boxed(self, "JoineryChangeCard")
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = title
	_column = VBoxContainer.new()
	_column.theme_type_variation = "JoineryStack"
	add_child(_column)
	var head := VBoxContainer.new()
	head.theme_type_variation = "JoineryTight"
	_column.add_child(head)
	title_label = STYLE.card_title(title, true)
	head.add_child(title_label)
	_lines.append(_caption(head, "Nothing changes until you apply it."))
	var files := _files(plan.get("changes", []))
	if not files.is_empty():
		_group("Files", files)
	var settings := _settings(plan.get("config_edits", []))
	if not settings.is_empty():
		_group("Settings", settings)
	var notes: Array[String] = []
	for note: Variant in plan.get("notes", []):
		notes.append(str(note))
	if not notes.is_empty():
		_group("Notes", notes)
	_error = UI.label("", "JoineryError", true)
	_error.hide()
	_column.add_child(_error)
	var conflicts: Array = plan.get("conflicts", [])
	if not conflicts.is_empty():
		_error.text = " ".join(conflicts.map(func(c: Variant) -> String: return str(c)))
		_error.show()
	_actions = HBoxContainer.new()
	_actions.theme_type_variation = "JoineryRow"
	_actions.alignment = BoxContainer.ALIGNMENT_END
	_column.add_child(_actions)
	_working = INDICATOR.new()
	_working.hide()
	_actions.add_child(_working)
	_cancel = UI.secondary("Cancel")
	_cancel.accessibility_name = "Cancel this change"
	_cancel.pressed.connect(_on_cancel)
	_apply = UI.primary("Apply")
	_apply.accessibility_name = "Apply: " + title
	_apply.disabled = not conflicts.is_empty()
	_apply.pressed.connect(_on_apply)
	var ordered := [_apply, _cancel] if DisplayServer.get_swap_cancel_ok() else [_cancel, _apply]
	for button: Button in ordered:
		_actions.add_child(button)
	_outcome = HBoxContainer.new()
	_outcome.theme_type_variation = "JoineryRowTight"
	_outcome.hide()
	_column.add_child(_outcome)
	_outcome_icon = STYLE.glyph()
	_outcome.add_child(_outcome_icon)
	_outcome_text = UI.label("", "JoineryCaption", true)
	_outcome_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_outcome.add_child(_outcome_text)
	_revert = UI.quiet("Revert")
	_revert.tooltip_text = "Put back the files from before this change"
	_revert.hide()
	UI.bind_height(_revert, "xs")
	_revert.pressed.connect(_on_revert)
	_outcome.add_child(_revert)
	_undo_revert = UI.quiet("Undo revert")
	_undo_revert.name = "UndoRevert"
	_undo_revert.tooltip_text = "Put this change back as it was applied"
	_undo_revert.hide()
	UI.bind_height(_undo_revert, "xs")
	_undo_revert.pressed.connect(
		func() -> void:
			_undo_revert.hide()
			_error.hide()
			unrevert_pressed.emit(transaction_id, false)
	)
	_outcome.add_child(_undo_revert)


## Controls whose text follows the transcript text size, as [control, TYPE step] pairs.
func scalables() -> Array:
	var pairs: Array = [[title_label, "body"], [_outcome_text, "caption"]]
	for line: Label in _lines:
		pairs.append([line, "caption"])
	for heading: Label in _headings:
		pairs.append([heading, "body"])
	return pairs


## The visible texts of the card, top to bottom (for tests and captures).
func texts() -> PackedStringArray:
	var found := PackedStringArray()
	for label: Node in find_children("*", "Label", true, false):
		if (label as Label).is_visible_in_tree():
			found.append((label as Label).text)
	return found


## Apply is running: both buttons wait, with dots, until [method set_applied].
func set_applying() -> void:
	state = "applying"
	_apply.disabled = true
	_cancel.disabled = true
	_apply.text = "Applying…"
	_working.show()
	_working.set("running", true)
	_error.hide()


## Applied: the buttons give way to the outcome, with Revert when there is a transaction.
func set_applied(text: String, transaction := "") -> void:
	state = "applied"
	transaction_id = transaction
	_settle("success", "success", text)
	_revert.visible = not transaction.is_empty()
	UI_MOTION.pulse(_outcome)


## The apply failed: say why in place and offer Apply again.
func set_failed(text: String) -> void:
	state = "failed"
	_working.set("running", false)
	_working.hide()
	_apply.text = "Apply"
	_apply.disabled = false
	_cancel.disabled = false
	_error.text = text
	UI_MOTION.fade_in(_error)


## A revert of the applied change finished: "Reverted" with Undo revert, or why not.
func set_reverted(ok: bool, message := "") -> void:
	if ok:
		state = "reverted"
		_revert.hide()
		_settle("reload", "text_muted", "Reverted")
		_undo_revert.show()
		return
	_revert.show()
	_error.text = ("Couldn't revert. " + message if not message.is_empty() else "Couldn't revert.")
	UI_MOTION.fade_in(_error)


## Undo revert finished: "Restored" with Revert again, the question about files edited
## since the revert ([param conflict]), or why not.
func set_unreverted(ok: bool, message := "", conflict: Array = []) -> void:
	if ok:
		state = "applied"
		_undo_revert.hide()
		_settle("success", "success", "Restored")
		_revert.show()
		UI_MOTION.pulse(_outcome)
		return
	_undo_revert.show()
	if not conflict.is_empty():
		_open_ask(
			REVERT_ASK.for_conflict(message),
			func(replace: bool) -> void:
				if replace:
					_undo_revert.hide()
					unrevert_pressed.emit(transaction_id, true)
		)
		return
	_error.text = (
		"Couldn't undo the revert. " + message
		if not message.is_empty()
		else "Couldn't undo the revert."
	)
	UI_MOTION.fade_in(_error)


## The question open under the outcome, or null.
func asking() -> Control:
	return _ask if is_instance_valid(_ask) else null


## Revert asks first, in place; only its Revert runs it.
func _on_revert() -> void:
	if asking() != null:
		_ask.queue_free()
		_ask = null
		return
	_open_ask(
		REVERT_ASK.for_revert(title_label.text, (plan.get("changes", []) as Array).size()),
		func(ok: bool) -> void:
			if ok:
				_revert.hide()
				revert_pressed.emit(transaction_id)
			elif _revert.is_visible_in_tree():
				_revert.grab_focus()
	)


func _open_ask(ask: Control, on_answer: Callable) -> void:
	if is_instance_valid(_ask):
		_ask.queue_free()
	_ask = ask
	ask.connect("answered", on_answer)
	_column.add_child(ask)


func _on_apply() -> void:
	if state in ["planned", "failed"] and not _apply.disabled:
		set_applying()
		apply_pressed.emit()


func _on_cancel() -> void:
	if state in ["planned", "failed"]:
		state = "cancelled"
		_settle("close", "text_muted", "Cancelled. Nothing changed.")
		cancel_pressed.emit()


func _settle(icon_name: String, role: String, text: String) -> void:
	_working.set("running", false)
	_actions.hide()
	_error.hide()
	STYLE.set_glyph(_outcome_icon, icon_name, role)
	_outcome_text.text = text
	_outcome.show()


func _group(heading: String, lines: Array[String]) -> void:
	var group := VBoxContainer.new()
	group.theme_type_variation = "JoineryTight"
	_column.add_child(group)
	var title := UI.label(heading)
	group.add_child(title)
	_headings.append(title)
	for line: String in lines:
		_lines.append(_caption(group, line))


func _caption(parent: Control, text: String) -> Label:
	var label := UI.label(text, "JoineryCaption", true)
	parent.add_child(label)
	return label


## Changed paths without the .uid sidecars, at most MAX_FILES then "and N more".
static func _files(changes: Array) -> Array[String]:
	var paths: Array[String] = []
	for change: Variant in changes:
		var path := str(change.get("path", "")) if change is Dictionary else str(change)
		if not path.is_empty() and not path.ends_with(".uid"):
			paths.append(path)
	if paths.size() > MAX_FILES:
		var more := paths.size() - (MAX_FILES - 1)
		paths = paths.slice(0, MAX_FILES - 1)
		paths.append("and %d more" % more)
	return paths


## project.godot edits as plain lines: "Adds autoload Controller", "Changes input: pause,
## move left". Values stay out: they are engine text, not something to read here.
static func _settings(edits: Array) -> Array[String]:
	var groups := {}
	for edit: Variant in edits:
		if not edit is Dictionary:
			continue
		var verb := (
			"Adds"
			if edit.get("before") == null
			else "Removes" if edit.get("after") == null else "Changes"
		)
		var key := "%s %s" % [verb, str(edit.get("section", "")).replace("_", " ")]
		if not groups.has(key):
			groups[key] = []
		groups[key].append(str(edit.get("key", "")).replace("_", " "))
	var lines: Array[String] = []
	for key: String in groups:
		lines.append("%s: %s" % [key, ", ".join(groups[key])])
	return lines
