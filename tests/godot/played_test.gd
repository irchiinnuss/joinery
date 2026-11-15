extends SceneTree
## "Not yet played by you" in the editor (joinery/played.py).
##
## An accepted change nobody has played since says "Not played since this change"
## under its decision line; the sidebar header counts them with a link that runs the game; the
## Timeline marks their entries. played_watch.gd hears the game core's debugger message, records
## the play through the real `joinery played` command in this disposable project, and tells
## everything in the "joinery_unplayed" group.

const CARD := preload("res://addons/joinery/sidebar_change_card.gd")
const HEADER := preload("res://addons/joinery/sidebar_header.gd")
const WATCH := preload("res://addons/joinery/played_watch.gd")
const TIMELINE := preload("res://addons/joinery/timeline_tab.gd")
const ENTRY := preload("res://addons/joinery/timeline_entry.gd")
const ID := "0123456789abcdef0123456789abcdef"

var _failed := false
var _host: VBoxContainer


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _frames(count := 2) -> void:
	for _i: int in count:
		await process_frame


func _until(condition: Callable, seconds := 30.0) -> bool:
	var deadline := Time.get_ticks_msec() + int(seconds * 1000.0)
	while Time.get_ticks_msec() < deadline:
		if bool(condition.call()):
			return true
		await process_frame
	return bool(condition.call())


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	_host = VBoxContainer.new()
	_host.size = Vector2(360, 900)
	root.add_child(_host)
	await _check_unplayed_card()
	await _check_header()
	await _check_timeline()
	await _check_real_cli()
	_host.queue_free()
	await process_frame
	print("played: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_unplayed_card() -> void:
	var card: VBoxContainer = CARD.new("Make the door wider")
	_host.add_child(card)
	card.transaction_id = ID
	card.set_ready([])
	# The decision's state comes before the card's own outcome (played_watch.gd first).
	call_group(&"joinery_unplayed", &"show_unplayed", {"ids": [ID]})
	_check(not card.unplayed_shown(), "A change still in review is not marked")
	card.set_outcome("success", "Change accepted")
	await _frames()
	_check(card.unplayed_shown(), "Accepted and not played: the card says so")
	var line: Control = card._unplayed
	var outcome: Control = card._outcome
	_check(line.get_parent() == outcome.get_parent(), "Beside the decision line")
	_check(line.get_index() == outcome.get_index() + 1, "Right under it")
	_check(
		(line.get_child(1) as Label).text == "Not played since this change",
		"In plain words: " + (line.get_child(1) as Label).text
	)
	call_group(&"joinery_unplayed", &"show_unplayed", {"ids": []})
	await _frames()
	_check(not card.unplayed_shown(), "Played since: the line goes")
	card.show_unplayed({"ids": [ID]})
	card.revert_finished(true)
	await _frames()
	_check(not card.unplayed_shown(), "A reverted change has nothing left to play")
	card.queue_free()
	await process_frame


func _check_header() -> void:
	var header: VBoxContainer = HEADER.new()
	header.play_on_press = false
	_host.add_child(header)
	await _frames()
	_check(header.unplayed_text() == "", "Nothing waiting: the header stays quiet")
	header.show_unplayed({"count": 3, "text": "3 changes not played yet"})
	await _frames()
	_check(header.unplayed_text() == "3 changes not played yet", "The header counts them")
	_check(header.unplayed.icon != null, "With the play glyph")
	var plays: Array[int] = [0]
	header.play_pressed.connect(func() -> void: plays[0] += 1)
	header.unplayed.pressed.emit()
	_check(plays[0] == 1, "The count clicks through to Play")
	header.show_unplayed({"count": 0, "text": ""})
	await _frames()
	_check(header.unplayed_text() == "", "All played: the link goes")
	header.queue_free()
	await process_frame


func _check_timeline() -> void:
	var tab: Control = TIMELINE.new()
	tab.set("cli_enabled", false)
	_host.add_child(tab)
	var now := Time.get_unix_time_from_system()
	(
		tab
		. apply_listing(
			{
				"state": "ready",
				"auto_commit": true,
				"entries":
				[
					{
						"commit": "a1",
						"short": "a1",
						"subject": "Wider door",
						"time": now,
						"files": 1
					},
					{
						"commit": "b2",
						"short": "b2",
						"subject": "Notes",
						"time": now - 60,
						"files": 1
					},
				],
				"more": false,
			}
		)
	)
	await _frames()
	_check(_marked(tab).is_empty(), "No entry is marked before the state arrives")
	tab.show_unplayed({"commits": ["a1"]})
	await _frames()
	_check(_marked(tab) == ["Wider door"], "The unplayed entry says so: " + str(_marked(tab)))
	var entry: Control = ENTRY.new()
	_host.add_child(entry)
	entry.set_loading({"subject": "Wider door", "unplayed": true, "time": now, "files": 1})
	_check(entry._unplayed.visible, "Its page says it was not played since")
	entry.set_loading({"subject": "Notes", "time": now, "files": 1})
	_check(not entry._unplayed.visible, "Another entry's page doesn't")
	entry.queue_free()
	tab.queue_free()
	await process_frame


func _marked(tab: Control) -> Array:
	var found: Array = []
	for row: Control in tab.call("entry_rows"):
		if row.find_child("Unplayed", false, false) != null:
			found.append((row.get_node("Open") as Button).text)
	return found


## The watch hears a person's editor run, records it with the real CLI, and tells the group.
func _check_real_cli() -> void:
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	if python.is_empty():
		push_error("JOINERY_TEST_PYTHON is not set")
		_failed = true
		return
	var project := ProjectSettings.globalize_path("res://").rstrip("/")
	DirAccess.make_dir_recursive_absolute(project.path_join(".joinery"))
	var stamped := Time.get_unix_time_from_system() - 120.0
	var state := {
		"version": 1,
		"unplayed": [{"id": ID, "summary": "Wider door", "accepted": stamped}],
		"last_played": 0.0,
	}
	var file := FileAccess.open(project.path_join(".joinery/played.json"), FileAccess.WRITE)
	file.store_string(JSON.stringify(state))
	file.close()
	var header: VBoxContainer = HEADER.new()
	header.play_on_press = false
	_host.add_child(header)
	var watch: Node = WATCH.new()
	watch.cli_prefix = PackedStringArray(
		[python, "-c", "import sys; from joinery.cli import main; sys.exit(main())"]
	)
	_host.add_child(watch)
	var counted := func() -> bool: return header.unplayed_text() == "1 change not played yet"
	_check(await _until(counted), "The real status reaches the header: " + header.unplayed_text())
	# A run too short to count changes nothing; a long one clears the change.
	watch.record(stamped + 5.0, 3.0)
	watch.record(stamped + 5.0, 30.0)
	var cleared := func() -> bool: return header.unplayed_text() == ""
	_check(await _until(cleared), "A long enough run clears it")
	_check(int(watch.state.get("count", -1)) == 0, "The watch keeps the new state")
	watch.on_decision({"unplayed": {"count": 2, "text": "2 changes not played yet", "ids": []}})
	_check(header.unplayed_text() == "2 changes not played yet", "A decision says the state")
	watch.queue_free()
	header.queue_free()
	await process_frame
