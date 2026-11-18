extends SceneTree
## The "Playtester" switch in Journeyman settings: unknown until the project answers, on by
## default, and a flip saved through the real `joinery playtester` command into this disposable
## project's joinery.toml. Off, /playtest is no longer offered; on again, it is.

const SETTING := preload("res://addons/joinery/playtester_setting.gd")
const SLASH := preload("res://addons/joinery/slash_commands.gd")

var _failed := false
var _host: Control


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _until(condition: Callable, seconds := 30.0) -> bool:
	var deadline := Time.get_ticks_msec() + int(seconds * 1000.0)
	while Time.get_ticks_msec() < deadline:
		if bool(condition.call()):
			return true
		await process_frame
	return bool(condition.call())


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	_host = Control.new()
	root.add_child(_host)
	_check_states()
	await _check_real_cli()
	_host.queue_free()
	await process_frame
	print("playtester setting: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_states() -> void:
	var setting: SETTING = SETTING.new()
	_check(setting.row.switch_button.disabled, "Unknown until the project answers")
	_check(setting.row.title.text == "Playtester", "Its name")
	_check(setting.row.description.text == SETTING.CAPTION, "Its caption says when it plays")
	setting.apply_status({"playtester": true})
	_check(setting.row.is_on() and not setting.row.switch_button.disabled, "On, changeable")
	setting.apply_status({"playtester": false})
	_check(not setting.row.is_on(), "Off")
	setting.apply_status({"error": "joinery.toml can't be read"})
	_check(setting.row.reason.visible, "A failure says why")
	setting.free()
	_check(SETTING.says_off("[agents]\nplaytester = false # not now\n"), "Off in joinery.toml")
	_check(not SETTING.says_off("[agents]\nplaytester = true\n"), "On in joinery.toml")
	_check(not SETTING.says_off("[game]\nplaytester = false\n"), "Only [agents] counts")


func _toml() -> String:
	return FileAccess.get_file_as_string("res://joinery.toml")


func _check_real_cli() -> void:
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	if python.is_empty():
		push_error("JOINERY_TEST_PYTHON is not set")
		_failed = true
		return
	var setting: SETTING = SETTING.new()
	setting.cli_prefix = PackedStringArray(
		[python, "-c", "import sys; from joinery.cli import main; sys.exit(main())"]
	)
	_host.add_child(setting)
	var answered := func() -> bool: return not setting.row.switch_button.disabled
	_check(await _until(answered), "The real command answers")
	_check(setting.row.is_on(), "On by default")
	_check(SLASH.all_commands().has("/playtest"), "/playtest is offered while it is on")
	setting.row.switch_button.button_pressed = false
	var saved := func() -> bool: return _toml().contains("playtester = false")
	_check(await _until(saved), "Off saves playtester = false in joinery.toml")
	_check(await _until(answered), "And the switch is free again")
	_check(not SLASH.all_commands().has("/playtest"), "Off: /playtest is no longer offered")
	setting.row.switch_button.button_pressed = true
	var back := func() -> bool: return _toml().contains("playtester = true")
	_check(await _until(back), "On saves playtester = true")
	_check(await _until(answered), "Free again")
	_check(SLASH.all_commands().has("/playtest"), "On again: /playtest is back")
	_check(_toml().contains("[tools]"), "The rest of joinery.toml stays")
	setting.queue_free()
	await process_frame
