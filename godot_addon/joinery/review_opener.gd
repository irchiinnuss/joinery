@tool
extends Node
## Opens a change's review by itself as soon as it is ready, so there is no "press Review" step,
## without taking the keys from someone typing. While a text field with words in it has the
## focus and was edited in the last [member idle] seconds, the review waits and opens once the
## typing stops; when it opens, the field that had the focus keeps it. Each opening has a key
## (the main request's, or a task's transaction), so a newer request for the same key replaces
## the waiting one. A change Joinery keeps without asking never comes here (review_flow.gd).

## Seconds without an edit that count as having stopped typing.
var idle := 1.5
var _waiting: Dictionary = {}
var _field: Control
var _text := ""
var _quiet := 0.0


func _init() -> void:
	name = "ReviewOpener"
	set_process(false)


## Run [param open] now, or once the user stops typing. [param key] names what it opens.
func request(open: Callable, key := "main") -> void:
	_waiting.erase(key)
	if not typing():
		_open(open)
		return
	_waiting[key] = open
	set_process(true)


## True while an opening waits for the typing to stop.
func waiting() -> bool:
	return not _waiting.is_empty()


## Forget a waiting opening (its change was decided or dropped meanwhile).
func cancel(key := "main") -> void:
	_waiting.erase(key)


## True while words are being written in a focused text field.
func typing() -> bool:
	var field := _text_focus()
	var text := _text_of(field)
	if field == null or text.strip_edges().is_empty():
		return false
	if field != _field or text != _text:
		_field = field
		_text = text
		_quiet = 0.0
		return true
	return _quiet < idle


func _process(delta: float) -> void:
	_quiet += delta
	if typing():
		return
	set_process(false)
	var due := _waiting.values()
	_waiting.clear()
	for open: Callable in due:
		_open(open)


## Open, then give the focus back to the text field that had it.
func _open(open: Callable) -> void:
	var before := _text_focus()
	if open.is_valid():
		open.call()
	if before != null and is_instance_valid(before) and before.is_visible_in_tree():
		before.grab_focus.call_deferred()


func _text_focus() -> Control:
	var viewport := get_viewport() if is_inside_tree() else null
	var focus := viewport.gui_get_focus_owner() if viewport != null else null
	return focus if focus is TextEdit or focus is LineEdit else null


static func _text_of(field: Control) -> String:
	if field is TextEdit:
		return (field as TextEdit).text
	if field is LineEdit:
		return (field as LineEdit).text
	return ""
