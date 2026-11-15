extends SceneTree
## The game core's play report (core/replays/play_report.gd), checked in a real game project
## (copied in as res://tests/play_report_check.gd): only a person's editor run is reported, and
## only once it has been played. This run itself is headless with a script, so it never counts.

const PlayReport := preload("res://core/replays/play_report.gd")
const Replays := preload("res://core/replays/replays.gd")

var _failed: bool = false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	var none: PackedStringArray = PackedStringArray()
	var not_game: Array = Replays.NOT_GAME_ARGS
	_check(
		not PlayReport.counts(OS.get_cmdline_user_args(), OS.get_cmdline_args(), not_game),
		"A headless run with a script is not a person's"
	)
	_check(PlayReport.person_args(none, none, not_game), "A plain editor run is a person's")
	var recording: PackedStringArray = PackedStringArray(["--joinery-record", "--joinery-pace"])
	_check(PlayReport.person_args(recording, none, not_game), "Record this play still counts")
	var named: PackedStringArray = PackedStringArray(["--joinery-record=walk"])
	_check(PlayReport.person_args(named, none, not_game), "A named recording counts too")
	for argument: String in [
		"--joinery-replay=a.replay", "--joinery-rerecord", "--playtest-out=x", "--seed=3"
	]:
		_check(
			not PlayReport.person_args(PackedStringArray([argument]), none, not_game),
			"%s is not a person's run" % argument
		)
	for argument: String in ["--script", "--headless"]:
		_check(
			not PlayReport.person_args(none, PackedStringArray([argument]), not_game),
			"%s is not a person's run" % argument
		)
	_check(PlayReport.scene_counts("res://core/main.tscn"), "The game's scene counts")
	_check(not PlayReport.scene_counts("res://addons/gut/GutRunner.tscn"), "GUT doesn't")
	_check(not PlayReport.scene_counts("res://tests/level_test.tscn"), "A test scene doesn't")
	var play: PlayReport = PlayReport.new()
	_check(not play.due(), "Nothing to report at once")
	var echo: InputEventKey = InputEventKey.new()
	echo.pressed = true
	echo.echo = true
	play.heard(echo)
	var motion: InputEventMouseMotion = InputEventMouseMotion.new()
	play.heard(motion)
	_check(play.presses == 0, "Echoes and mouse moves are not presses")
	var key: InputEventKey = InputEventKey.new()
	key.pressed = true
	play.heard(key)
	_check(play.presses == 1, "A key press is the person's")
	_check(not play.due(), "Not before it has been played long enough")
	play._start_ticks -= int(PlayReport.PLAY_SECONDS * 1000.0) + 100
	_check(play.due(), "Played long enough with a press: due")
	_check(not play.report("res://addons/gut/GutRunner.tscn"), "A test runner is never reported")
	_check(not play.due(), "And not asked again")
	var fresh: PlayReport = PlayReport.new()
	fresh.presses = 1
	fresh._start_ticks -= int(PlayReport.PLAY_SECONDS * 1000.0) + 100
	_check(fresh.report("res://core/main.tscn"), "A game run is reported once")
	_check(not fresh.report("res://core/main.tscn"), "Only once")
	_check(absf(fresh.started - Time.get_unix_time_from_system()) < 60.0, "With when it started")
	print("play report: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
