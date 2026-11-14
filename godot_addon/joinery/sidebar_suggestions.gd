@tool
extends RefCounted
## One-line suggestions from the game brief ({text, command}), offered in the transcript one at
## a time and never acted on: choosing one runs its command, which only plans the change.

## The sidebar (journeyman_dock.gd) whose transcript shows them.
var dock: Control
var _offers: Array[Dictionary] = []
var _row: Control
var _offered: Array[String] = []


func _init(sidebar: Control) -> void:
	dock = sidebar


## New suggestions from the chat host (chat_bridge.gd's suggestions); each text once.
func take(items: Array) -> void:
	for item: Variant in items:
		if not item is Dictionary:
			continue
		var text := str(item.get("text", ""))
		if text.is_empty() or _offered.has(text):
			continue
		_offered.append(text)
		_offers.append(item)
	if not is_instance_valid(_row):
		_next()


func _next() -> void:
	var transcript: Object = dock.get("_transcript")
	if is_instance_valid(_row):
		transcript.call("remove_offer", _row)
	_row = null
	if _offers.is_empty():
		return
	var item: Dictionary = _offers.pop_front()
	var actions: Array = []
	var command := str(item.get("command", ""))
	if not command.is_empty():
		actions.append([command, _take.bind(command)])
	actions.append(["Not now", _next])
	_row = transcript.call("add_offer", str(item.get("text", "")), actions)


## The user chose an offer: plan it (a plan changes nothing), and show the next one.
func _take(command: String) -> void:
	_next()
	dock.call("run_command", command)
