@tool
extends Node
## Tells the user when Journeyman is done or needs them while the editor is in the background:
## the Dock icon bounces (the taskbar button flashes) and a desktop notification says why in
## one line. Nothing happens while any editor window has focus.
##
## It follows the chat host (chat_bridge.gd) through one request and speaks once, when the
## request ends: a change waiting for Accept or Discard, a to-do waiting for Go, a question, a
## stop (needs_user, a Rule Zero stop, an error) or, otherwise, the reply finished. Steps along
## the way say nothing. A request the user stopped says nothing either. A chat working out of
## sight (New chat while it works) says it finished in another chat.
##
## The notification runs the platform's own tool as a process with an argument list, never
## through a shell, and never puts the text into a script's source: on macOS the Joinery
## notifier (JoineryNotifier.app, so the banner shows Joinery's name and icon) takes it as its
## arguments, and `osascript` (shown as Script Editor) as the run handler's argv when there is
## no notifier or it ends with an error; Linux `notify-send` as its arguments (skipped when
## not installed), Windows PowerShell builds a toast from environment variables with a fixed
## script. Clicking it is best effort: the Dock bounce or taskbar flash brings the user back
## either way.
##
## One notification per kind each [constant SPACING_MSEC]; a newer one in that time replaces
## the waiting one. Two editor settings (also on Journeyman's settings page) turn it off, or
## keep only what needs the user.

## Editor settings: notifications at all (on by default), and only those that need the user.
const ENABLED := "interface/joinery/desktop_notifications"
const INPUT_ONLY := "interface/joinery/desktop_notifications_input_only"
## Kinds that need the user: a change to review, a to-do waiting for Go, a question or stop.
const NEEDS_YOU := ["review", "go", "input"]
const SPACING_MSEC := 5000
const MAX_CHARS := 120
const POLICY_STOP := "Stopped by project policy:"
const OSASCRIPT := "/usr/bin/osascript"
## The toast, built from JOINERY_NOTIFY_TITLE and JOINERY_NOTIFY_TEXT: never from its source.
const TOAST := (
	"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
	+ "ContentType = WindowsRuntime] > $null"
	+ """
$kind = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($kind)
$lines = $xml.GetElementsByTagName('text')
$lines.Item(0).AppendChild($xml.CreateTextNode($env:JOINERY_NOTIFY_TITLE)) > $null
$lines.Item(1).AppendChild($xml.CreateTextNode($env:JOINERY_NOTIFY_TEXT)) > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
"""
)

## The notifier app's executable, inside the release's Joinery.app or built by
## `joinery notifier build` into the launcher's cache folder.
const NOTIFIER_IN_APP := "../Helpers/JoineryNotifier.app/Contents/MacOS/JoineryNotifier"
const NOTIFIER_CACHED := "Library/Caches/Joinery/JoineryNotifier.app/Contents/MacOS/JoineryNotifier"

## Preferences outside the editor (tests, the visual suite).
static var _prefs_here := {}

## Starts a process: (program: String, args: PackedStringArray, env: Dictionary) -> int, its
## process id (0 when none). Unset, the program starts in the background; tests record it.
var runner := Callable()
## (pid: int) -> int: a started process's exit code, -1 while it runs; unset, asks the OS.
var exit_code := Callable()
## () -> String: the notifier's executable, empty when there is none; unset, looks for it.
var notifier := Callable()
## True while the user looks at the editor; unset, asks the display server.
var focused := Callable()
## Bounces the Dock icon / flashes the taskbar; unset, asks the display server.
var attention := Callable()
## Milliseconds now; unset, the engine's clock.
var clock := Callable()
## "macOS", "Linux", "Windows", …; empty is this computer's.
var platform := ""
## The project's name in the title; empty reads it from the project settings.
var project_name := ""
var _bridge: Object
var _dock: Object
## kind -> when it last went out (msec).
var _sent := {}
## kind -> [due msec, line]: one held back by the spacing.
var _held := {}
var _pids: Array[int] = []
## pid of a notifier still running -> the osascript call to make if it fails.
var _fallbacks := {}
## What this request came to.
var _turn := {}


## Follow [param dock] (journeyman_dock.gd) and the chat host it holds.
static func attach(dock: Control) -> Node:
	var notify: Node = load("res://addons/joinery/desktop_notify.gd").new()
	notify.set("_dock", dock)
	dock.add_child(notify)
	dock.child_entered_tree.connect(Callable(notify, "_on_dock_child"))
	for child: Node in dock.get_children():
		notify.call("_on_dock_child", child)
	if dock.has_signal("cancel_requested"):
		dock.connect("cancel_requested", func() -> void: notify.call("_note", "stopped", true))
	register()
	return notify


func _init() -> void:
	name = "DesktopNotify"
	set_process(false)


## Add both settings to the editor's (once), so the Editor Settings dialog lists them too.
static func register() -> void:
	var settings := _settings()
	if settings == null:
		return
	for key: String in [ENABLED, INPUT_ONLY]:
		var initial := key == ENABLED
		if not settings.has_setting(key):
			settings.set_setting(key, initial)
		settings.set_initial_value(key, initial, false)
		settings.add_property_info({"name": key, "type": TYPE_BOOL})


## A preference: [constant ENABLED] or [constant INPUT_ONLY].
static func pref(key: String) -> bool:
	var settings := _settings()
	var initial := key == ENABLED
	if settings == null:
		return bool(_prefs_here.get(key, initial))
	return bool(settings.get_setting(key)) if settings.has_setting(key) else initial


static func set_pref(key: String, on: bool) -> void:
	var settings := _settings()
	if settings == null:
		_prefs_here[key] = on
	else:
		settings.set_setting(key, on)


static func _settings() -> EditorSettings:
	if not Engine.is_editor_hint() or not Engine.has_singleton("EditorInterface"):
		return null
	return Engine.get_singleton("EditorInterface").call("get_editor_settings") as EditorSettings


## Follow [param bridge] (chat_bridge.gd).
func listen(bridge: Object) -> void:
	if bridge == null or bridge == _bridge:
		return
	_bridge = bridge
	var links := {
		"busy_changed": _on_busy,
		"message_delta": _on_delta,
		"message_received": _on_message,
		"error_received": _on_notice.bind("error"),
		"notice": _on_notice,
		"turn_state": _on_turn_state,
		"file_preview": _on_preview,
		"file_decision": func(_event: Dictionary) -> void: _note("review", false),
		"todo_event": _on_todo,
		"completed": _on_completed,
	}
	for signal_name: String in links:
		if bridge.has_signal(signal_name):
			bridge.connect(signal_name, links[signal_name])


func _on_dock_child(node: Node) -> void:
	if node.has_signal("sent") and node.has_signal("file_preview"):
		listen(node)


func _note(key: String, value: Variant) -> void:
	_turn[key] = value


func _on_busy(busy: bool) -> void:
	if busy:
		_turn = {}


func _on_delta(text: String, new_message: bool) -> void:
	_turn["reply"] = text if new_message else str(_turn.get("reply", "")) + text


func _on_message(text: String) -> void:
	if text.begins_with(POLICY_STOP):
		_turn["stop"] = text
	else:
		_turn["reply"] = text


func _on_notice(text: String, kind: String) -> void:
	if text.begins_with(POLICY_STOP):
		_turn["stop"] = text
	elif kind == "error":
		_turn["error"] = text


func _on_turn_state(event: Dictionary) -> void:
	var state := str(event.get("state", ""))
	var reason := str(event.get("reason", ""))
	if state == "in_review" and reason.is_empty() and not bool(event.get("auto_accept", false)):
		_turn["review"] = true
	elif state == "idle" and reason == "needs_user":
		_turn["needs_user"] = true


func _on_preview(event: Dictionary) -> void:
	if not bool(event.get("auto_accept", false)):
		_turn["review"] = true
		_turn["summary"] = str(event.get("summary", ""))


func _on_todo(event: Dictionary) -> void:
	var steps: Variant = event.get("steps")
	if str(event.get("state", "")) == "waiting" and steps is Array:
		_turn["go"] = (steps as Array).size()
	elif str(event.get("state", "")) in ["dropped", "cancelled", "going"]:
		_turn.erase("go")


func _on_completed(ok: bool) -> void:
	var turn := _turn
	_turn = {}
	if bool(turn.get("stopped", false)):
		return
	var said := describe(turn, ok, _elsewhere())
	if not said.is_empty():
		notify(said[0], said[1])


## The chat that just finished is not the one on screen.
func _elsewhere() -> bool:
	if _dock == null or not _dock.has_method("_turn"):
		return false
	var turn: Variant = _dock.call("_turn")
	return turn != null and turn != _dock.get("_transcript")


## [kind, line] for a request that ended ([param ok]) with [param turn]'s facts.
static func describe(turn: Dictionary, ok: bool, elsewhere := false) -> Array:
	var reply := _first_line(str(turn.get("reply", "")))
	if turn.has("stop"):
		return ["input", str(turn["stop"])]
	if bool(turn.get("review", false)):
		var summary := str(turn.get("summary", ""))
		return ["review", "Change ready for review" + (": " + summary if summary else "")]
	if turn.has("go"):
		var steps := int(turn["go"])
		var count := "1 step" if steps == 1 else "%d steps" % steps
		return ["go", "Waiting for your Go on the to-do (%s)" % count]
	if bool(turn.get("needs_user", false)):
		return ["input", "Needs your input" + (": " + reply if reply else "")]
	if not ok:
		var why := str(turn.get("error", "")) if turn.has("error") else reply
		return ["input", "Stopped: " + why if why else "Journeyman stopped"]
	var question := _question(str(turn.get("reply", "")))
	if not question.is_empty():
		return ["input", "Needs your answer: " + question]
	var done := "Finished in another chat" if elsewhere else "Finished"
	return ["finished", done + (": " + reply if reply else "")]


## The reply's first line of words, without Markdown marks.
static func _first_line(text: String) -> String:
	for line: String in text.split("\n", false):
		var plain := line.strip_edges().lstrip("#>-*• ").replace("**", "").replace("`", "")
		if not plain.strip_edges().is_empty():
			return plain.strip_edges()
	return ""


## The reply's last line when it asks something.
static func _question(text: String) -> String:
	var lines := text.strip_edges().split("\n", false)
	var last := _first_line(lines[-1]) if not lines.is_empty() else ""
	return last if last.ends_with("?") else ""


## Tell the user [param line] ([param kind]: review, go, input or finished), unless they are
## looking at the editor or the settings say not to.
func notify(kind: String, line: String) -> void:
	if not pref(ENABLED) or (pref(INPUT_ONLY) and kind not in NEEDS_YOU) or _focused():
		return
	# No one sits at a headless editor, or at one that never takes focus (JOINERY_BACKGROUND).
	if not runner.is_valid() and (_headless() or OS.has_environment("JOINERY_BACKGROUND")):
		return
	if attention.is_valid():
		attention.call()
	else:
		DisplayServer.window_request_attention()
	var now := _now()
	var due := int(_sent.get(kind, -SPACING_MSEC)) + SPACING_MSEC
	if now >= due:
		_send(kind, line)
	else:
		_held[kind] = [due, line]
		set_process(true)


func _process(_delta: float) -> void:
	var now := _now()
	for kind: String in _held.keys():
		if now >= int(_held[kind][0]):
			var line := str(_held[kind][1])
			_held.erase(kind)
			if pref(ENABLED) and not _focused():
				_send(kind, line)
	# Starting a process leaves it to be reaped: asking whether it runs does that.
	_pids = _pids.filter(_running)
	set_process(not _held.is_empty() or not _pids.is_empty())


## Whether [param pid] still runs; a notifier that ended with an error hands over to osascript.
func _running(pid: int) -> bool:
	var code := _exit_code(pid)
	if code == -1:
		return true
	var fallback: Array = _fallbacks.get(pid, [])
	_fallbacks.erase(pid)
	if code != 0 and not fallback.is_empty():
		_start(fallback)
	return false


func _exit_code(pid: int) -> int:
	if exit_code.is_valid():
		return int(exit_code.call(pid))
	if OS.is_process_running(pid):
		return -1
	return OS.get_process_exit_code(pid)


func _send(kind: String, line: String) -> void:
	_sent[kind] = _now()
	var heading := title()
	var call := command(heading, line)
	if call.is_empty():
		return
	var pid := _start(call)
	if _system() != "macOS" or call[0] == OSASCRIPT:
		return
	# The notifier: osascript says it instead if it fails to start or ends with an error.
	if pid > 0:
		_fallbacks[pid] = script_command(heading, line)
	else:
		_start(script_command(heading, line))


## Starts [param call] ([program, args, env]); its process id, 0 when none started.
func _start(call: Array) -> int:
	var pid := 0
	if runner.is_valid():
		var started: Variant = runner.call(call[0], call[1], call[2])
		pid = int(started) if started is int else 0
	else:
		var env: Dictionary = call[2]
		for key: String in env:
			OS.set_environment(key, env[key])
		pid = OS.create_process(call[0], call[1])
		for key: String in env:
			OS.unset_environment(key)
	if pid > 0:
		_pids.append(pid)
		set_process(true)
	return pid


## "Journeyman — <project>".
func title() -> String:
	var project := project_name
	if project.is_empty():
		project = str(ProjectSettings.get_setting("application/config/name", ""))
	return clean("Journeyman — " + project if project else "Journeyman")


## [program, args, env] showing [param heading] and [param line] on this platform; empty
## when it has no way to.
func command(heading: String, line: String) -> Array:
	var text := clean(line)
	match _system():
		"macOS":
			var program := notifier_path()
			if program.is_empty():
				return script_command(heading, line)
			return [program, PackedStringArray(["--", heading, text]), {}]
		"Windows":
			var encoded := Marshalls.raw_to_base64(TOAST.to_utf16_buffer())
			var args := PackedStringArray(
				["-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden"]
			)
			args.append_array(["-EncodedCommand", encoded])
			var env := {"JOINERY_NOTIFY_TITLE": heading, "JOINERY_NOTIFY_TEXT": text}
			return ["powershell.exe", args, env]
		"Linux", "FreeBSD", "NetBSD", "OpenBSD", "BSD":
			var program := _on_path("notify-send")
			if program.is_empty():
				return []
			var args := PackedStringArray(["--app-name=Godot", "--", heading, text])
			return [program, args, {}]
	return []


## [program, args, env] for macOS's own osascript: the text is the run handler's argv.
static func script_command(heading: String, line: String) -> Array:
	var script := PackedStringArray(
		[
			"-e",
			"on run argv",
			"-e",
			"display notification (item 2 of argv) with title (item 1 of argv)",
			"-e",
			"end run",
			"--",
			heading,
			clean(line),
		]
	)
	return [OSASCRIPT, script, {}]


## The Joinery notifier's executable: the release's, next to the editor, else the one built in
## the cache; empty when neither is there.
func notifier_path() -> String:
	if notifier.is_valid():
		return str(notifier.call())
	var beside := OS.get_executable_path().get_base_dir().path_join(NOTIFIER_IN_APP)
	for path: String in [beside.simplify_path(), _home().path_join(NOTIFIER_CACHED)]:
		if not path.is_empty() and FileAccess.file_exists(path):
			return path
	return ""


static func _home() -> String:
	var home := OS.get_environment("HOME")
	return home if not home.is_empty() else "/nonexistent"


func _system() -> String:
	return platform if not platform.is_empty() else OS.get_name()


## [param text] on one line, without control characters, at most [constant MAX_CHARS] long.
static func clean(text: String) -> String:
	var out := ""
	for index: int in text.length():
		var code := text.unicode_at(index)
		out += " " if code < 32 or (code >= 127 and code < 160) else char(code)
	var words := " ".join(out.split(" ", false))
	return words if words.length() <= MAX_CHARS else words.left(MAX_CHARS - 1) + "…"


static func _on_path(program: String) -> String:
	# PATH's separator is the host's: ";" on Windows, whose folders hold a drive's ":".
	var separator := ";" if OS.get_name() == "Windows" else ":"
	for folder: String in OS.get_environment("PATH").split(separator, false):
		var path := folder.path_join(program)
		if FileAccess.file_exists(path):
			return path
	return ""


func _focused() -> bool:
	if focused.is_valid():
		return bool(focused.call())
	if DisplayServer.window_get_mode() == DisplayServer.WINDOW_MODE_MINIMIZED:
		return false
	for id: int in DisplayServer.get_window_list():
		if DisplayServer.window_is_focused(id):
			return true
	return false


static func _headless() -> bool:
	return DisplayServer.get_name() == "headless"


func _now() -> int:
	return int(clock.call()) if clock.is_valid() else Time.get_ticks_msec()
