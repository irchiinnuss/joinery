extends SceneTree
## What the agent guessed and where it didn't do as asked, on a change card: one compact
## line (review_glance.gd's departures_line) that opens to each line, the departures first
## (sidebar_departures.gd).

const CARD := preload("res://addons/joinery/sidebar_change_card.gd")
const GLANCE := preload("res://addons/joinery/review_glance.gd")

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
	_check_line()
	await _check_departures()
	_host.queue_free()
	await process_frame
	print("departures: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_line() -> void:
	_check(GLANCE.departures_line([], []) == "", "Nothing to say: no line")
	_check(GLANCE.departures_line(["oak"], []) == "1 guess", "A guess alone")
	_check(GLANCE.departures_line(["a", "b", " "], []) == "2 guesses", "Blank lines don't count")
	_check(
		GLANCE.departures_line(["oak"], ["smaller"]) == "Differs from your request · 1 guess",
		"A departure leads: " + GLANCE.departures_line(["oak"], ["smaller"])
	)
	_check(
		GLANCE.departures_line([], ["a", "b"]) == "Differs from your request in 2 ways",
		"Several departures are counted"
	)


func _check_departures() -> void:
	var card: VBoxContainer = CARD.new("Make a door 3 m wide")
	_host.add_child(card)
	card.set_done(["Made the door in Blender"])
	card.set_departures(["Made it oak; no wood was named"], ["1.2 m wide: the wall is 2.5 m"])
	await _frames()
	var departures: Control = card._departures
	_check(departures.visible, "The card shows what was guessed and what isn't as asked")
	_check(
		departures.line_text() == "Differs from your request · 1 guess",
		"One compact line: " + departures.line_text()
	)
	_check(not departures.is_open(), "Closed at first: the review stays glanceable")
	_check(card._done_list.get_index() < departures.get_index(), "It sits under the done list")
	departures.set_open(true)
	await _frames()
	_check(departures.is_open(), "The line opens")
	_check(
		(
			departures.lines()
			== PackedStringArray(
				[
					"Not as asked: 1.2 m wide: the wall is 2.5 m",
					"Guessed: Made it oak; no wood was named",
				]
			)
		),
		"Each line, the departure first: " + str(departures.lines())
	)
	var first: Label = departures._body.get_child(0).get_child(1)
	_check(first.theme_type_variation == "JoineryCaption", "Quiet caption text")
	card.set_departures([], [" "])
	_check(not departures.visible, "Nothing to say hides it")
	card.queue_free()
	await process_frame
