extends SceneTree
## Desktop notifications: only while the editor is in the background, one line per request
## that ended (review, Go, a question or stop, finished, another chat finished), spaced out per
## kind, silent when turned off, and started as a program with arguments: no shell, and the
## text never inside a script's source.

const NOTIFY := preload("res://addons/joinery/desktop_notify.gd")
const SETTING := preload("res://addons/joinery/notify_setting.gd")
const SHELLS := ["sh", "bash", "zsh", "dash", "fish", "cmd", "cmd.exe"]


class FakeBridge:
	extends Node
	signal sent
	signal file_preview(event: Dictionary)
	signal busy_changed(busy: bool)
	signal message_delta(text: String, new_message: bool)
	signal message_received(text: String)
	signal notice(text: String, kind: String)
	signal turn_state(event: Dictionary)
	signal file_decision(event: Dictionary)
	signal todo_event(event: Dictionary)
	signal completed(ok: bool)


class FakeDock:
	extends Control
	signal cancel_requested
	var turn: Control
	var _transcript: Control = Control.new()

	func _init() -> void:
		turn = _transcript

	func _exit_tree() -> void:
		_transcript.free()

	func _turn() -> Control:
		return turn


var _failed := false
var _calls: Array = []
var _attention := 0
var _focused := false
var _now := 100_000
var _dock: FakeDock
var _bridge: FakeBridge
var _notify: Node


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	_dock = FakeDock.new()
	root.add_child(_dock)
	_notify = NOTIFY.attach(_dock)
	_notify.set("runner", _record)
	_notify.set("focused", func() -> bool: return _focused)
	_notify.set("attention", func() -> void: _attention += 1)
	_notify.set("clock", func() -> int: return _now)
	_notify.set("platform", "macOS")
	_notify.set("project_name", "Yard Day")
	_bridge = FakeBridge.new()
	_dock.add_child(_bridge)
	_check_focus()
	_check_lines()
	_check_spacing()
	_check_settings()
	_check_mac()
	_check_linux()
	_check_windows()
	_check_clean()
	_check_setting_rows()
	_dock.queue_free()
	await process_frame
	print("desktop notify: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _record(program: String, args: PackedStringArray, env: Dictionary) -> void:
	_calls.append([program, args, env])


## One request: [param events] as [signal, args…] in order, then done with [param ok].
func _turn(events: Array, ok := true) -> void:
	_now += 10_000
	_bridge.busy_changed.emit(true)
	for event: Array in events:
		_bridge.emit_signal.callv(event)
	_bridge.completed.emit(ok)
	_bridge.busy_changed.emit(false)


func _last_line() -> String:
	return str((_calls[-1][1] as PackedStringArray)[-1]) if not _calls.is_empty() else ""


func _said(events: Array, ok: bool) -> String:
	var before := _calls.size()
	_turn(events, ok)
	return _last_line() if _calls.size() == before + 1 else "<%d calls>" % (_calls.size() - before)


func _check_focus() -> void:
	_focused = true
	_turn([["message_received", "Made the door wider."]])
	_check(_calls.is_empty() and _attention == 0, "Nothing while the editor has focus")
	_focused = false
	_turn([["message_received", "Made the door wider."]])
	_check(
		_calls.size() == 1 and _attention == 1, "Bounce and notify while it is in the background"
	)
	_check(_last_line() == "Finished: Made the door wider.", "Finished says the reply's first line")
	_check(_calls[-1][1][-2] == "Journeyman — Yard Day", "The title names the project")


func _check_lines() -> void:
	var review := [
		["file_preview", {"summary": "Mower engine sound", "transaction_id": "t1"}],
		["turn_state", {"state": "in_review"}],
		["message_received", "The mower now sounds like a two-stroke."],
	]
	_check(_said(review, true) == "Change ready for review: Mower engine sound", "Review")
	var auto := [
		["turn_state", {"state": "in_review", "auto_accept": true}],
		["message_received", "Kept it."],
	]
	_check(_said(auto, true) == "Finished: Kept it.", "Kept without asking is only finished")
	var steps := []
	steps.resize(8)
	var todo := [["todo_event", {"kind": "todo", "state": "waiting", "steps": steps}]]
	_check(_said(todo, true) == "Waiting for your Go on the to-do (8 steps)", "Go")
	var question := [["message_delta", "I can do that.\n", true]]
	question.append(["message_delta", "Which colour should the door be?", false])
	_check(
		_said(question, true) == "Needs your answer: Which colour should the door be?",
		"A reply ending in a question asks"
	)
	var needs := [
		["turn_state", {"state": "idle", "reason": "needs_user"}],
		["message_received", "The art bible has no palette yet."],
	]
	_check(
		_said(needs, true) == "Needs your input: The art bible has no palette yet.", "needs_user"
	)
	var stop := [["message_received", "Stopped by project policy: generative service"]]
	_check(
		_said(stop, false) == "Stopped by project policy: generative service",
		"A Rule Zero stop says so"
	)
	var error := [["notice", "Lost connection to Claude Code.", "error"]]
	_check(_said(error, false) == "Stopped: Lost connection to Claude Code.", "An error stops")
	var before := _calls.size()
	_bridge.busy_changed.emit(true)
	_dock.cancel_requested.emit()
	_bridge.completed.emit(false)
	_check(_calls.size() == before, "A request the user stopped says nothing")
	_dock.turn = Control.new()
	var away := [["message_received", "Added the fence."]]
	_check(
		_said(away, true) == "Finished in another chat: Added the fence.",
		"A chat out of sight says it finished in another chat"
	)
	_dock.turn.free()
	_dock.turn = _dock._transcript
	var many := [["message_delta", "**Done.** Wider door, oak frame.", true]]
	_check(_said(many, true) == "Finished: Done. Wider door, oak frame.", "Without Markdown marks")


func _check_spacing() -> void:
	_now += 60_000
	var before := _calls.size()
	_notify.call("notify", "finished", "First")
	_now += 1000
	_notify.call("notify", "finished", "Second")
	_now += 1000
	_notify.call("notify", "finished", "Third")
	_notify.call("notify", "review", "Change ready for review")
	_check(_calls.size() == before + 2, "One per kind in five seconds; other kinds go out")
	_notify.call("_process", 0.0)
	_check(_calls.size() == before + 2, "The held one waits its turn")
	_now += 3000
	_notify.call("_process", 0.0)
	_check(_calls.size() == before + 3 and _last_line() == "Third", "The newest one replaces it")
	_now += 10_000
	_notify.call("notify", "finished", "Held")
	_notify.call("notify", "finished", "Held again")
	_focused = true
	_now += 10_000
	_notify.call("_process", 0.0)
	_check(_calls.size() == before + 4, "Back at the editor, the held one is dropped")
	_focused = false


func _check_settings() -> void:
	_now += 60_000
	var before := _calls.size()
	var bounced := _attention
	NOTIFY.set_pref(NOTIFY.ENABLED, false)
	_turn([["message_received", "Done."]])
	_turn([["turn_state", {"state": "in_review"}]])
	_check(_calls.size() == before and _attention == bounced, "Turned off: nothing at all")
	NOTIFY.set_pref(NOTIFY.ENABLED, true)
	NOTIFY.set_pref(NOTIFY.INPUT_ONLY, true)
	_turn([["message_received", "Done."]])
	_check(_calls.size() == before, "Only when needed: finished says nothing")
	_turn([["turn_state", {"state": "in_review"}]])
	_check(_calls.size() == before + 1, "Only when needed: a review still does")
	_check(_last_line() == "Change ready for review", "A review with no summary")
	NOTIFY.set_pref(NOTIFY.INPUT_ONLY, false)
	_check(NOTIFY.pref(NOTIFY.ENABLED) and not NOTIFY.pref(NOTIFY.INPUT_ONLY), "Defaults back")


func _no_shell(call: Array, text: String) -> void:
	var program := str(call[0]).get_file().to_lower()
	_check(program not in SHELLS, "No shell runs it: " + program)
	var args: PackedStringArray = call[1]
	_check(not args.has("-c") and not args.has("/c"), "No shell command string")
	_check(args.has(text) or (call[2] as Dictionary).values().has(text), "The text is passed as is")


func _check_mac() -> void:
	var text := 'Say "hi" & quit" -- end tell'
	var call: Array = _notify.call("command", "Journeyman — Yard Day", text)
	_check(str(call[0]) == "/usr/bin/osascript", "macOS runs osascript")
	_no_shell(call, text)
	var args: PackedStringArray = call[1]
	var separator := args.find("--")
	_check(separator > 0 and args.size() == separator + 3, "argv after --: title and text")
	_check(args[separator + 1] == "Journeyman — Yard Day" and args[separator + 2] == text, "argv")
	for index: int in args.size():
		if args[index] == "-e":
			_check(
				not args[index + 1].contains("hi") and not args[index + 1].contains("Yard"),
				"The script's source never holds the text"
			)
	_check(" ".join(args.slice(0, separator)).contains("item 2 of argv"), "Read from argv")


func _check_linux() -> void:
	_notify.set("platform", "Linux")
	var saved := OS.get_environment("PATH")
	var folder := OS.get_user_data_dir().path_join("fake_bin")
	DirAccess.make_dir_recursive_absolute(folder)
	OS.set_environment("PATH", "/nowhere")
	_check((_notify.call("command", "T", "x") as Array).is_empty(), "No notify-send: skipped")
	var fake := FileAccess.open(folder.path_join("notify-send"), FileAccess.WRITE)
	fake.close()
	OS.set_environment("PATH", folder)
	var call: Array = _notify.call("command", "T", "Needs your answer: why?")
	OS.set_environment("PATH", saved)
	_check(str(call[0]) == folder.path_join("notify-send"), "notify-send from PATH")
	_no_shell(call, "Needs your answer: why?")
	var args: PackedStringArray = call[1]
	_check(args[-3] == "--" and args[-2] == "T", "Options end before the title and text")
	_notify.set("platform", "macOS")


func _check_windows() -> void:
	_notify.set("platform", "Windows")
	var text := "'; Remove-Item C:\\ -Recurse; '"
	var call: Array = _notify.call("command", "Journeyman — Yard Day", text)
	_notify.set("platform", "macOS")
	_check(str(call[0]) == "powershell.exe", "Windows runs PowerShell")
	var args: PackedStringArray = call[1]
	_check(not args.has(text) and not " ".join(args).contains("Remove-Item"), "Not in the args")
	var env: Dictionary = call[2]
	_check(env.get("JOINERY_NOTIFY_TEXT") == text, "The text goes in the environment")
	_no_shell(call, text)
	var encoded := args[args.find("-EncodedCommand") + 1]
	var script := Marshalls.base64_to_raw(encoded).get_string_from_utf16()
	_check(script == NOTIFY.TOAST, "The fixed toast script")
	_check(script.contains("$env:JOINERY_NOTIFY_TEXT"), "It reads the text from the environment")
	_check((_notify.call("command", "T", "x") as Array).size() == 3, "And macOS again")


func _check_clean() -> void:
	_check(NOTIFY.clean("a\u0007b\nc\td\u009b") == "a b c d", "Control characters go")
	var long := NOTIFY.clean("word ".repeat(60))
	_check(long.length() == NOTIFY.MAX_CHARS and long.ends_with("…"), "Long text is cut")


func _check_setting_rows() -> void:
	var body := VBoxContainer.new()
	SETTING.add_to(body)
	var main: Node = body.find_child("DesktopNotifications", false, false)
	var only: Node = body.find_child("NotifyInputOnly", false, false)
	_check(main != null and only != null, "Settings has both rows")
	var main_switch: CheckButton = main.get("switch_button")
	var only_switch: CheckButton = only.get("switch_button")
	_check(main_switch.button_pressed and not only_switch.button_pressed, "On; everything")
	main_switch.button_pressed = false
	_check(not NOTIFY.pref(NOTIFY.ENABLED) and only_switch.disabled, "Off: the sub-option rests")
	main_switch.button_pressed = true
	only_switch.button_pressed = true
	_check(NOTIFY.pref(NOTIFY.INPUT_ONLY) and not only_switch.disabled, "Only when needed, saved")
	only_switch.button_pressed = false
	body.free()
