# gdlint: disable=max-public-methods
extends RefCounted
## End-to-end UI scenarios for the Journeyman sidebar, the way chat apps test their agent UIs:
## each scenario drives the real sidebar against the real chat host (bridge.stdio) with a
## scripted provider behind it (scripted_host.py), so every run is the same and costs nothing.
## A host script builds the sidebar and calls [method run]: ui_scenario_dock.gd (the dock alone,
## at each width and theme) or ui_scenario_editor.gd (inside a real editor, with the plugin's
## own wiring and Review panel).
##
## Every scenario prints what it checked, one line each, for run_ui_scenarios.py:
##   SCENARIO-CHECK <scenario> pass|fail|xfail|xpass <what>   (xfail: a known bug, with why)
##   SCENARIO-METRIC <scenario> <name> <value> <unit>
## and takes screenshots through ui_checks.gd (VISUAL-CAPTURE, UI-CHECK lines), named
## scn-<scenario>-<state>-<theme>-<width>.png.
##
## JOINERY_SCENARIOS (comma-separated) runs only those scenarios. Each scenario starts with a
##   SCENARIO-BEGIN <scenario>   and   SCENARIO-END <scenario> <seconds>
## lines and may take at most SCENARIO_MS. A host that never connects, or a scenario that never
## ends, prints the host's exit code and stderr and ends the run there, rather than every later
## scenario waiting the same way in turn.

const UI_CHECKS_FILE := "ui_checks.gd"
## The scenarios themselves, one function each: the everyday ones, then the edge cases.
const CASES_FILE := "ui_scenario_cases.gd"
const EDGES_FILE := "ui_scenario_edges.gd"
## A failed check's own error line (not an error of the sidebar's).
const FAILED_CHECK := "Scenario check failed: "
## The longest one scenario may take (they take a few seconds each).
const SCENARIO_MS := 60000
## Every scenario, in the order they run (ui_scenario_cases.gd has one function each).
const ALL := [
	"empty",
	"first-run",
	"stream-short",
	"stream-long",
	"scroll-away",
	"tool-steps",
	"thinking",
	"stop",
	"keyboard",
	"provider-error",
	"rate-limit",
	"network-drop",
	"timeout",
	"rule-zero-stop",
	"denied-call",
	"review-accept",
	"review-reject",
	"revert-undo",
	"queued",
	"two-chats",
	"switch-mid-stream",
	"rewind",
	"todo",
	"playtest",
	"console-errors",
	"auto-fix",
	"idle",
	"rapid-input",
	"odd-content",
	"huge-reply",
	"long-chat",
	"noisy-host",
	"never-ready",
	"queued-retry",
	"accept-twice",
	"accept-offline",
	"switch-in-review",
	"finish-together",
	"other-chat-stop",
	"crash-two-chats",
	"revert-elsewhere",
	"queue-follows",
	"editor-life",
	"reopen",
]

## Builds a fresh sidebar for each scenario: returns [dock, chat bridge], wired and in the tree.
var build: Callable
## Frees what [member build] made (the editor host keeps its own dock).
var teardown: Callable
var tree: SceneTree
var theme_name := "dark"
var width := 480
var dock: Control
var chat: Node
## What the sidebar would have told the desktop (desktop_notify.gd), one line each.
var notifications: PackedStringArray = []
var _checks: GDScript
var _cases: RefCounted
var _edges: RefCounted
var _script_path := ""
var _scenario := ""
var _failed := false
## The scripted hosts this scenario started (process ids), to check each one ends.
var _hosts: Array[int] = []
## The last scripted host started, for its stderr when it never connects.
var _host_job: RefCounted
## Why the remaining scenarios are not run (a host that never connects, a scenario that never
## ends): each would only wait the same way.
var _halted := ""
## Every error Godot printed while a scenario ran (a known gotcha shows only there).
var _errors := ErrorLog.new()


## Records the errors Godot prints (push_error, failed checks in the engine, script errors).
class ErrorLog:
	extends Logger
	var lines: PackedStringArray = []
	## Only errors raised from Joinery's own scripts count (inside the editor, others print too).
	var only_joinery := false
	var _lock := Mutex.new()

	func _log_error(
		function: String,
		file: String,
		line: int,
		code: String,
		rationale: String,
		_editor_notify: bool,
		error_type: int,
		script_backtraces: Array[ScriptBacktrace]
	) -> void:
		if error_type == ERROR_TYPE_WARNING:
			return
		var where := "%s (%s:%d)" % [function, file.get_file(), line]
		var ours := file.contains("addons/joinery")
		for trace: ScriptBacktrace in script_backtraces:
			if trace.get_frame_count() > 0:
				where = (
					"%s:%d %s"
					% [
						trace.get_frame_file(0).get_file(),
						trace.get_frame_line(0),
						trace.get_frame_function(0)
					]
				)
				ours = ours or trace.format().contains("addons/joinery")
		var said := rationale if not rationale.is_empty() else code
		if said.begins_with(FAILED_CHECK) or (only_joinery and not ours):
			return
		_lock.lock()
		lines.append("%s at %s" % [said, where])
		_lock.unlock()

	## Take what was recorded so far.
	func take() -> PackedStringArray:
		_lock.lock()
		var taken := lines.duplicate()
		lines.clear()
		_lock.unlock()
		return taken


func _init(scene_tree: SceneTree) -> void:
	tree = scene_tree
	var here: String = get_script().resource_path.get_base_dir()
	_checks = load(here.path_join(UI_CHECKS_FILE))
	_cases = load(here.path_join(CASES_FILE)).new()
	_cases.set("s", self)
	_edges = load(here.path_join(EDGES_FILE)).new()
	_edges.set("s", self)
	_errors.only_joinery = Engine.is_editor_hint()
	OS.add_logger(_errors)


## Run every scenario (or JOINERY_SCENARIOS); true when nothing failed unexpectedly.
func run(only: Array = []) -> bool:
	_write_script()
	var wanted := only.duplicate()
	var named := OS.get_environment("JOINERY_SCENARIOS")
	if not named.is_empty():
		wanted = Array(named.split(",", false))
	for scenario: String in ALL:
		if not wanted.is_empty() and not wanted.has(scenario):
			continue
		_scenario = scenario
		_errors.take()
		if not _halted.is_empty():
			_line("fail", "Not run: %s" % _halted)
			_failed = true
			continue
		print("SCENARIO-BEGIN ", scenario)
		var built: Array = build.call()
		dock = built[0]
		chat = built[1]
		_quiet_notifications()
		await frames(2)
		var finished := [false]
		var function := "_" + scenario.replace("-", "_")
		var play := func() -> void:
			await (_cases if _cases.has_method(function) else _edges).call(function)
			finished[0] = true
		play.call()
		var started := Time.get_ticks_msec()
		if not await wait_until(func() -> bool: return finished[0], SCENARIO_MS):
			check(false, "The scenario ends within %d s" % (SCENARIO_MS / 1000))
			_host_output()
			# What it still awaits must not run on into the next scenario's sidebar.
			_halted = "%s did not end" % scenario
		print("SCENARIO-END %s %.1f s" % [scenario, (Time.get_ticks_msec() - started) / 1000.0])
		if chat != null and is_instance_valid(chat):
			chat.call("close")
		if teardown.is_valid():
			await teardown.call()
		await frames(2)
		await _hosts_end()
		_no_errors()
	OS.remove_logger(_errors)
	# The scenarios hold this runner and it holds them: let go, so nothing is in use at exit.
	_cases.set("s", null)
	_edges.set("s", null)
	return not _failed


## Nothing printed an error while the scenario ran, or as its sidebar went away.
func _no_errors() -> void:
	var printed := _errors.take()
	var first := printed[0] if not printed.is_empty() else ""
	check(printed.is_empty(), "Nothing printed an error (%d): %s" % [printed.size(), first])
	for line: String in printed.slice(1, 4):
		print("SCENARIO-ERROR %s %s" % [_scenario, line])


# --- Reporting ---------------------------------------------------------------------------------


## A check that must hold.
func check(ok: bool, what: String) -> void:
	_line("pass" if ok else "fail", what)
	if not ok:
		_failed = true
		push_error("%s%s: %s" % [FAILED_CHECK, _scenario, what])


## A check that fails today because of a known bug ([param why]); passing again is reported.
func expect_bug(ok: bool, what: String, why: String) -> void:
	_line("xpass" if ok else "xfail", "%s (%s)" % [what, why])


func metric(name: String, value: float, unit: String) -> void:
	print("SCENARIO-METRIC %s %s %.1f %s" % [_scenario, name, value, unit])


func _line(state: String, what: String) -> void:
	var words := what.replace("\n", " ")
	print("SCENARIO-CHECK %s/%s-%d %s %s" % [_scenario, theme_name, width, state, words])


## Screenshot and design checks of the sidebar as it is now.
func shot(state: String) -> void:
	await frames(3)
	var screen := "scn-%s-%s-%s-%d" % [_scenario, state, theme_name, width]
	_checks.call("snapshot", screen, tree.root, [dock])
	_layout_checks(state)


# --- Driving the sidebar -----------------------------------------------------------------------


func frames(count := 1) -> void:
	for index: int in count:
		await tree.process_frame


## The windows here never have focus, so the sidebar would post real desktop notifications
## and bounce the Dock icon on every stop or error: record them instead (desktop_notify.gd's
## own test hooks), never touching the user's desktop.
func _quiet_notifications() -> void:
	notifications.clear()
	var notify := dock.get_node_or_null("DesktopNotify")
	if notify == null:
		return
	notify.set(
		"runner",
		func(program: String, args: PackedStringArray, _env: Dictionary) -> int:
			notifications.append("%s %s" % [program.get_file(), " ".join(args)])
			return 0
	)
	notify.set("attention", func() -> void: notifications.append("attention"))
	notify.set("notifier", func() -> String: return "")


## Closing the chat ends its host process (the editor would otherwise keep it running).
func _hosts_end() -> void:
	for pid: int in _hosts:
		if pid <= 0:
			continue
		var ended := await wait_until(func() -> bool: return not OS.is_process_running(pid), 5000)
		check(ended, "Closing the chat ends its host process (pid %d)" % pid)
		if not ended:
			# Ask it where it is stuck first (scripted_host.py writes its stacks), then end it.
			# Windows has no SIGUSR1: there it is only ended.
			if OS.get_name() != "Windows":
				OS.execute("kill", ["-USR1", str(pid)])
				await wait_ms(500)
			OS.kill(pid)
	_hosts.clear()


## Wait until [param condition] holds, at most [param ms]; true when it did.
func wait_until(condition: Callable, ms := 8000) -> bool:
	var deadline := Time.get_ticks_msec() + ms
	while Time.get_ticks_msec() < deadline:
		if condition.call():
			return true
		await tree.process_frame
	return bool(condition.call())


func wait_ms(ms: int) -> void:
	await tree.create_timer(ms / 1000.0).timeout


## Start the scripted chat host and wait until the sidebar shows it connected; with
## [param wait] false, return once it started. [param changes] go into this host's script
## ({"connect_ms": …}).
func connect_host(wait := true, changes := {}) -> bool:
	chat.call("close")
	var job: RefCounted = load("res://addons/joinery/command_job.gd").new()
	var script := _script_path
	if not changes.is_empty():
		script = _script_path.get_basename() + "-changed.json"
		var data: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(_script_path))
		data.merge(changes, true)
		var file := FileAccess.open(script, FileAccess.WRITE)
		file.store_string(JSON.stringify(data))
		file.close()
	var args := PackedStringArray(
		[
			_host_script(),
			"--project",
			ProjectSettings.globalize_path("res://").rstrip("/"),
			"--script",
			script,
		]
	)
	if job.call("start", OS.get_environment("JOINERY_TEST_PYTHON"), args) != OK:
		check(false, "The scripted chat host starts")
		return false
	chat.set("_agent", "codex")
	_host_job = job
	_hosts.append(int((job.get("_process") as Dictionary).get("pid", 0)))
	chat.set("_job", job)
	dock.call("set_chat_agent", "codex")
	chat.emit_signal("status_changed", "connecting", "Connecting to Codex CLI…")
	if not wait:
		return true
	var ok := await wait_until(func() -> bool: return bool(chat.call("is_ready")), 20000)
	check(ok, "The sidebar connects to the scripted chat host")
	if not ok:
		_host_output()
		_halted = "the scripted chat host never connected"
	await wait_until(func() -> bool: return dock.get("connection_state") == "connected", 2000)
	await frames(2)
	return ok


## Print what the last scripted host said on stderr, and whether it is still running.
func _host_output() -> void:
	if _host_job == null:
		return
	var pid := int(_hosts[-1]) if not _hosts.is_empty() else 0
	var state := "running" if pid > 0 and OS.is_process_running(pid) else "ended"
	if state == "ended" and pid > 0:
		state = "ended with code %d" % OS.get_process_exit_code(pid)
	if not (_host_job.get("_process") as Dictionary).is_empty():
		_host_job.call("take_lines")
	var stderr: PackedByteArray = _host_job.get("_stderr")
	print("SCENARIO-HOST %s pid %d %s, stderr:" % [_scenario, pid, state])
	print(stderr.get_string_from_utf8().strip_edges())


## The scripted hosts die at once, as a crash would. Killed from outside (OS.kill would also
## reap them, which the sidebar's own job then can't).
func kill_hosts() -> void:
	for pid: int in _hosts:
		if pid > 0 and OS.is_process_running(pid):
			OS.execute("kill", ["-9", str(pid)])


## scripted_host.py: JOINERY_SCRIPTED_HOST, or the one beside this script.
func _host_script() -> String:
	var named := OS.get_environment("JOINERY_SCRIPTED_HOST")
	if not named.is_empty():
		return named
	return get_script().resource_path.get_base_dir().path_join("scripted_host.py")


func prompt() -> TextEdit:
	return dock.get("_prompt")


func composer() -> Control:
	return dock.get("_composer")


func transcript() -> Control:
	return dock.get("_transcript")


func card() -> Control:
	return dock.call("_card")


## Type [param text] into the message box and press Enter, as a user does.
func send(text: String) -> int:
	var field := prompt()
	field.grab_focus()
	field.text = text
	field.set_caret_column(text.length())
	field.text_changed.emit()
	var started := Time.get_ticks_usec()
	key(KEY_ENTER)
	return started


## Press [param code] (with Shift, or the command key, when asked) where the focus is.
func key(code: Key, shift := false, command := false) -> void:
	for pressed: bool in [true, false]:
		var event := InputEventKey.new()
		event.keycode = code
		event.physical_keycode = code
		event.pressed = pressed
		event.shift_pressed = shift
		event.command_or_control_autoremap = command
		tree.root.push_input(event)


## Click [param control] where it is on screen, as a mouse does (hit-testing included).
func click(control: Control) -> void:
	var at := control.get_global_rect().get_center()
	for pressed: bool in [true, false]:
		var event := InputEventMouseButton.new()
		event.button_index = MOUSE_BUTTON_LEFT
		event.pressed = pressed
		event.position = at
		event.global_position = at
		tree.root.push_input(event)
	await frames(2)


## Every word the sidebar shows now (labels, rich text and buttons in view); with [param root],
## only those under it.
func visible_text(root: Node = null) -> String:
	var words := PackedStringArray()
	for node: Node in (root if root != null else dock).find_children("*", "Control", true, false):
		var control := node as Control
		if not control.is_visible_in_tree():
			continue
		if control is Label:
			words.append((control as Label).text)
		elif control is RichTextLabel:
			words.append((control as RichTextLabel).get_parsed_text())
		elif control is Button:
			words.append((control as Button).text)
	return " | ".join(words)


## The last reply's text as shown.
func reply_shown() -> String:
	var messages: Array = transcript().get("_messages")
	for index: int in range(messages.size() - 1, -1, -1):
		if messages[index]["who"] == "Journeyman":
			var markdown: GDScript = load("res://addons/joinery/chat_markdown.gd")
			return str(markdown.call("shown", messages[index]["parts"]))
	return ""


## Lines the bridge wrote to the host (a stand-in job records nothing; the real one, its pipe).
func card_state() -> String:
	var found := card()
	return str(found.get("state")) if found != null else ""


# --- Layout checks run with each screenshot ------------------------------------------------------


## Interactive controls must not overlap each other; icon-only buttons need a tooltip; nothing
## reaches past the sidebar's right edge.
func _layout_checks(state: String) -> void:
	var interactive: Array[Control] = []
	var bounds := dock.get_global_rect()
	for node: Node in dock.find_children("*", "Control", true, false):
		var control := node as Control
		if not control.is_visible_in_tree() or control.modulate.a < 0.05:
			continue
		if not (control is BaseButton or control is TextEdit or control is LineEdit):
			continue
		if control.top_level or visible_rect(control).get_area() < 1.0:
			continue
		interactive.append(control)
		var button := control as Button
		if button != null and button.text.strip_edges().is_empty() and button.icon != null:
			if button.tooltip_text.strip_edges().is_empty():
				check(
					false, "%s: icon-only button %s has a tooltip" % [state, control_name(button)]
				)
		var rect := control.get_global_rect()
		if rect.end.x > bounds.end.x + 1.0 or rect.position.x < bounds.position.x - 1.0:
			check(false, "%s: %s stays inside the sidebar" % [state, control_name(control)])
	_header_aligned(state)
	for first: int in interactive.size():
		for second: int in range(first + 1, interactive.size()):
			var a := interactive[first]
			var b := interactive[second]
			if a.is_ancestor_of(b) or b.is_ancestor_of(a) or _status_over_field(a, b):
				continue
			var overlap := visible_rect(a).intersection(visible_rect(b))
			if overlap.get_area() > 16.0:
				check(
					false, "%s: %s and %s don't overlap" % [state, control_name(a), control_name(b)]
				)


## The header's title starts, and its row ends, on the composer's edges: in a wide sidebar both
## share the chat's centred column.
func _header_aligned(state: String) -> void:
	var header: Variant = dock.get("_header")
	var box := composer()
	if not header is Control or not (header as Control).is_visible_in_tree():
		return
	if box == null or not box.is_visible_in_tree():
		return
	var title: Control = (header as Control).get("title")
	var row: Control = (header as Control).get("row")
	var edges := box.get_global_rect()
	var inset := (row.get_parent() as Control).get_global_rect()
	var aligned := (
		absf(title.get_global_rect().position.x - edges.position.x) <= 1.5
		and absf(inset.end.x - edges.end.x) <= 1.5
	)
	if not aligned:
		check(
			false,
			(
				"%s: the header lines up with the composer (title %.0f-%.0f, composer %.0f-%.0f)"
				% [
					state,
					title.get_global_rect().position.x,
					inset.end.x,
					edges.position.x,
					edges.end.x
				]
			)
		)


## The part of [param control] on screen: its rect cut by every scrolling or clipping parent.
func visible_rect(control: Control) -> Rect2:
	var rect := control.get_global_rect()
	var parent := control.get_parent()
	while parent != null and parent != dock:
		var box := parent as Control
		if box != null and (box is ScrollContainer or box.clip_contents):
			rect = rect.intersection(box.get_global_rect())
		parent = parent.get_parent()
	return rect


## At least half of [param control] is on screen.
func on_screen(control: Control) -> bool:
	var whole := control.get_global_rect().get_area()
	return control.is_visible_in_tree() and visible_rect(control).get_area() >= whole * 0.5


## The status line sits over the message box's first line on purpose (sidebar_composer.gd).
func _status_over_field(a: Control, b: Control) -> bool:
	var line: Variant = composer().get("_status_line")
	if not line is Control:
		return false
	var status := line as Control
	return (
		(a == prompt() and (status == b or status.is_ancestor_of(b)))
		or (b == prompt() and (status == a or status.is_ancestor_of(a)))
	)


func control_name(control: Control) -> String:
	var label := ""
	if control is Button and not (control as Button).text.is_empty():
		label = "'%s'" % (control as Button).text
	elif not control.tooltip_text.is_empty():
		label = "'%s'" % control.tooltip_text.left(30)
	elif not str(control.accessibility_name).is_empty():
		label = "'%s'" % control.accessibility_name
	return "%s%s" % [control.get_class(), label]


func _write_script() -> void:
	var script: Dictionary = _cases.call("script_data")
	var turns: Dictionary = script["turns"]
	turns.merge(_edges.get("SCRIPT").duplicate(true))
	turns[_edges.get("ODD")][-1]["stream"] = _edges.call("odd_reply")
	turns[_edges.get("HUGE")][-1]["stream"] = _edges.call("huge_reply")
	DirAccess.make_dir_recursive_absolute(OS.get_cache_dir())
	_script_path = OS.get_cache_dir().path_join(
		"joinery-ui-scenarios-%d.json" % OS.get_process_id()
	)
	var file := FileAccess.open(_script_path, FileAccess.WRITE)
	file.store_string(JSON.stringify(script))
	file.close()
