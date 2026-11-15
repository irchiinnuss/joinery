extends RefCounted
## Tells the editor that a person has played this run, so Joinery can clear "not yet played by
## you" from the changes accepted before it (joinery/played.py in the Joinery repository).
##
## Only a person's run counts: one started from the editor (its debugger attached), with a
## window, and with none of the arguments a replay, the playtest bot, a test or Joinery's own
## checks pass. "Record this play" still counts: the person plays it. Once the run has lasted
## PLAY_SECONDS with at least one press from the person, one "joinery:played" message goes to
## the editor with when the run started and how long it has lasted. Nothing is written to the
## project and an exported game never sends anything.

const MESSAGE := "joinery:played"
## Real seconds a run must last, with the person's input, to count as played.
const PLAY_SECONDS := 10.0
## User arguments that a person's own run may carry ("Record this play").
const PERSON_ARGS: Array[String] = ["--joinery-record", "--joinery-pace"]
## Engine arguments that mean nobody is playing: a script, a test runner, no window.
const NOT_A_PERSON: Array[String] = ["--script", "-s", "--headless", "--test", "--doctool"]

var started: float = 0.0
var presses: int = 0
var sent: bool = false
var _start_ticks: int = 0


func _init() -> void:
	started = Time.get_unix_time_from_system()
	_start_ticks = Time.get_ticks_msec()


## Whether this run may count as a person's: from the editor, with a window, and with no
## argument from a replay, the bot, a test or Joinery ([param not_game] are the prefixes the
## Replays autoload treats as not the game's own).
static func counts(user_args: PackedStringArray, args: PackedStringArray, not_game: Array) -> bool:
	if not OS.has_feature("editor_runtime") or not EngineDebugger.is_active():
		return false
	if DisplayServer.get_name() == "headless":
		return false
	return person_args(user_args, args, not_game)


## The argument part of [method counts], on its own so it can be checked anywhere.
static func person_args(
	user_args: PackedStringArray, args: PackedStringArray, not_game: Array
) -> bool:
	for argument: String in args:
		if argument in NOT_A_PERSON:
			return false
	for argument: String in user_args:
		if _is_person_arg(argument):
			continue
		for prefix: String in not_game:
			if argument.begins_with(prefix):
				return false
	return true


static func _is_person_arg(argument: String) -> bool:
	for allowed: String in PERSON_ARGS:
		if argument == allowed or argument.begins_with(allowed + "="):
			return true
	return false


## A window event: a press from the person (a key, a button, a touch) counts.
func heard(event: InputEvent) -> void:
	if event.is_echo():
		return
	if (
		(event is InputEventKey or event is InputEventMouseButton)
		or event is InputEventJoypadButton
		or event is InputEventScreenTouch
	):
		if event.is_pressed():
			presses += 1


## Seconds since the run started.
func seconds() -> float:
	return (Time.get_ticks_msec() - _start_ticks) / 1000.0


## Whether the run has now been played long enough to report, once.
func due() -> bool:
	return not sent and presses > 0 and seconds() >= PLAY_SECONDS


## Whether a run that started on [param scene] is the game: a test runner's scene (GUT's under
## addons/, a test under tests/) is not.
static func scene_counts(scene: String) -> bool:
	return not (scene.begins_with("res://addons/") or scene.begins_with("res://tests/"))


## Send the report to the editor when it is due and the run started on [param scene] (a
## game scene, see [method scene_counts]). Returns whether it was sent.
func report(scene: String) -> bool:
	if not due():
		return false
	if not scene_counts(scene):
		sent = true
		return false
	sent = true
	if EngineDebugger.is_active():
		EngineDebugger.send_message(MESSAGE, [started, seconds()])
	return true
