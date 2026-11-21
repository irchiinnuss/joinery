extends SceneTree
## Settings → Remote (ADR 0093): off says devices stay paired; pairing shows the QR code and the
## code from the real `joinery remote pair`, asks "Pair …?" in place with Cancel focused when a
## phone uses the code, and the device it allows is listed with Revoke, which unpairs it. Runs
## the real CLI against a disposable per-user Remote folder; no server is started.

const SETTING := preload("res://addons/joinery/remote_setting.gd")

var _failed := false
var _python := ""
var _home := ""


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
	_check_states()
	_python = OS.get_environment("JOINERY_TEST_PYTHON")
	if _python.is_empty():
		_check(false, "JOINERY_TEST_PYTHON is not set")
	else:
		_home = OS.get_temp_dir().path_join("joinery-remote-test-%d" % OS.get_process_id())
		OS.set_environment("JOINERY_REMOTE_HOME", _home)
		await _check_real_cli()
		OS.move_to_trash(_home)
	print("remote setting: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_states() -> void:
	var setting: SETTING = SETTING.new()
	setting.cli_enabled = false
	setting.apply_status({"enabled": false, "tailscale": {"running": true}})
	_check(not setting.switch_row.is_on(), "Off")
	_check(setting.where.text.begins_with("Off."), "Off says devices stay paired")
	_check(setting.pair_button.disabled, "Pairing waits for Remote to be on")
	_check(not setting.helper_row.visible, "Off, the helper switch waits too")
	setting.apply_status(
		{"enabled": true, "tailscale": {"running": false, "message": "Tailscale isn't installed."}}
	)
	_check(setting.where.text == "Tailscale isn't installed.", "Says what Tailscale needs")
	(
		setting
		. apply_status(
			{
				"enabled": true,
				"url": "https://mac.tail1.ts.net",
				"tailscale": {"running": true},
				"devices": [{"id": "d_1", "name": "Ana's iPhone", "scopes": ["watch"]}],
				"helper": true,
			}
		)
	)
	_check(setting.where.text.ends_with("https://mac.tail1.ts.net"), "Says where a phone opens it")
	_check(not setting.pair_button.disabled, "Pair a device, once on")
	_check(setting.helper_row.visible and setting.awake_row.visible, "On: the helper and awake")
	_check(setting.devices.get_child_count() == 1, "The paired device is listed")
	_check(setting.helper_row.is_on() and not setting.awake_row.is_on(), "Each switch as told")
	setting.free()


func _python_line(code: String) -> String:
	var out: Array = []
	OS.execute(_python, PackedStringArray(["-c", code]), out, true)
	return str(out[0]).strip_edges() if not out.is_empty() else ""


func _check_real_cli() -> void:
	var host := Control.new()
	root.add_child(host)
	var setting: SETTING = SETTING.new()
	setting.cli_prefix = PackedStringArray(
		[_python, "-c", "import sys; from joinery.cli import main; sys.exit(main())"]
	)
	_python_line("from joinery.remote.store import Store; Store().set_config(enabled=True)")
	host.add_child(setting)
	_check(await _until(func() -> bool: return setting.switch_row.is_on()), "The CLI says it is on")
	setting.pair_button.pressed.emit()
	_check(await _until(func() -> bool: return setting.qr.texture != null), "The QR code shows")
	var code := setting.code.text
	_check(code.length() == 11 and code[5] == "-", "The code shows, grouped (%s)" % code)
	# A phone uses the code (as the server does on POST /api/pair).
	_python_line(
		(
			"from joinery.remote.store import Store, digest; "
			+ "Store().redeem('%s', 'Test Phone', 'iOS', digest('t'), digest('s'))" % code
		)
	)
	_check(
		await _until(func() -> bool: return setting.find_child("RevertAsk", true, false) != null),
		"The machine is asked before anything is paired"
	)
	var ask: Node = setting.find_child("RevertAsk", true, false)
	if ask != null:
		var words := str((ask.get("text") as Label).text)
		_check(words.begins_with('Pair "Test Phone" (iOS)?'), "It names the device: " + words)
		await process_frame
		await process_frame
		_check(ask.get("cancel").has_focus(), "Cancel has the focus")
		(ask.get("action") as Button).pressed.emit()
	_check(
		await _until(func() -> bool: return setting.devices.get_child_count() == 1),
		"The paired device is listed"
	)
	var revoke: Button = setting.devices.find_child("Revoke", true, false)
	if revoke != null:
		revoke.pressed.emit()
	_check(
		await _until(func() -> bool: return setting.devices.get_child_count() == 0),
		"Revoke unpairs it"
	)
	host.queue_free()
	await process_frame
