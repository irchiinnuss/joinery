extends SceneTree
## "Wait for Go" in Settings → Agent: unknown until the project answers, Auto by default, and a
## choice saved through the real `joinery todo-go` command into this disposable project's
## joinery.toml.

const SETTING := preload("res://addons/joinery/todo_go_setting.gd")

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
	print("todo go setting: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_states() -> void:
	var setting: SETTING = SETTING.new()
	_check(setting.picker.disabled, "Unknown until the project answers")
	_check(setting.picker.item_count == 4, "Auto, two steps or more, always, never")
	_check(setting.picker.get_item_text(0) == "Auto", "Auto comes first")
	setting.apply_status({"todo_go": "never"})
	_check(not setting.picker.disabled and setting.picker.selected == 3, "Never, changeable")
	_check(setting.picker.tooltip_text == SETTING.HINTS[3], "The closed picker says what it does")
	setting.apply_status({"error": "joinery.toml can't be read"})
	_check(setting.reason.visible and setting.picker.selected == 3, "A failure says why")
	setting.apply_status({"todo_go": "auto"})
	_check(not setting.reason.visible and setting.picker.selected == 0, "Auto again")
	setting.free()


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
	var answered := func() -> bool: return not setting.picker.disabled
	_check(await _until(answered), "The real command answers")
	_check(setting.picker.selected == 0, "Auto by default")
	setting.picker.select(3)
	setting.picker.item_selected.emit(3)
	var saved := func() -> bool: return _toml().contains('todo_go = "never"')
	_check(await _until(saved), "Never saves todo_go in joinery.toml")
	_check(await _until(answered) and setting.picker.selected == 3, "And reads back")
	_check(_toml().contains("[tools]"), "The rest of joinery.toml stays")
	setting.queue_free()
	await process_frame
