extends SceneTree
## The UI scenarios (ui_scenarios.gd) against the Journeyman sidebar on its own, at the
## sidebar's three widths in dark and light. The chat bridge is wired to the dock as the
## plugin wires it (plugin.gd _connect_chat); a change's review is decided through the dock's
## own Accept and Discard handlers, which the Review panel calls. Run with
## --path <project with addons/joinery> --script; JOINERY_TEST_PYTHON names the Python for
## the scripted chat host (scripted_host.py beside this script, or JOINERY_SCRIPTED_HOST). Ends with
## "ui scenarios dock: ok" or "… FAILED".
##
## JOINERY_SCENARIO_WIDTHS and JOINERY_SCENARIO_THEMES (comma-separated) narrow the matrix;
## headless (no screenshots) it runs at 480 in dark only, as run_widget_tests.py does.

const WIDTHS: Array[int] = [340, 480, 1000]
const HEIGHT := 760
var _lib: RefCounted
var _host: Control
var _light := false


func _initialize() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	var theme: GDScript = load("res://addons/joinery/ui_theme.gd")
	theme.call("set_pref", "theme", "match")
	theme.call("set_pref", "accent", "brass")
	_run.call_deferred()


func _run() -> void:
	var ok := true
	var widths := WIDTHS.duplicate()
	var themes := ["dark", "light"]
	if DisplayServer.get_name() == "headless":
		# No screenshots without a window: one width and theme checks the behaviour.
		widths = [480]
		themes = ["dark"]
	var wanted := OS.get_environment("JOINERY_SCENARIO_WIDTHS")
	if not wanted.is_empty():
		widths = Array(wanted.split(",", false)).map(func(value: String) -> int: return int(value))
	if not OS.get_environment("JOINERY_SCENARIO_THEMES").is_empty():
		themes = Array(OS.get_environment("JOINERY_SCENARIO_THEMES").split(",", false))
	for theme: String in themes:
		for width: int in widths:
			_light = theme == "light"
			root.size = Vector2i(width, HEIGHT)
			_lib = load(get_script().resource_path.get_base_dir().path_join("ui_scenarios.gd")).new(
				self
			)
			_lib.set("theme_name", theme)
			_lib.set("width", width)
			_lib.set("build", _build)
			_lib.set("teardown", _teardown)
			if not await _lib.call("run"):
				ok = false
	# Let go of everything the scenarios held, so nothing is still in use at exit.
	_lib.set("dock", null)
	_lib.set("chat", null)
	_lib.set("build", Callable())
	_lib.set("teardown", Callable())
	_lib.get("_cases").set("s", null)
	_lib = null
	for index: int in 10:
		await process_frame
	print("ui scenarios dock: ", "ok" if ok else "FAILED")
	quit(0 if ok else 1)


## What remote_relay.gd reads of plugin.gd: the sidebar, the file review on screen and whether
## a decision on it is on its way.
class RelayPlugin:
	extends RefCounted
	var _dock: Control
	var _file_review: Dictionary = {}
	var _deciding := false


## A fresh sidebar on the editor's ground, with a chat bridge wired as the plugin wires it.
func _build() -> Array:
	_host = Control.new()
	_host.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_host.theme = Theme.new()
	var ground_color := Color("f7f6f3") if _light else Color("202123")
	_host.theme.set_color("base_color", "Editor", ground_color)
	var ground := ColorRect.new()
	ground.color = ground_color
	ground.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	ground.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_host.add_child(ground)
	root.add_child(_host)
	var dock: Control = load("res://addons/joinery/journeyman_dock.gd").new()
	if root.size.x < 480:
		dock.set("min_em", dock.get("SHARED_MIN_EM"))
	_host.add_child(dock)
	dock.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var chat: Node = load("res://addons/joinery/chat_bridge.gd").new()
	dock.add_child(chat)
	_wire(dock, chat)
	return [dock, chat]


## plugin.gd's _connect_chat links, plus the file review it opens (review_flow.gd).
func _wire(dock: Control, chat: Node) -> void:
	var links := {
		"status_changed": "set_chat_state",
		"busy_changed": "set_chat_busy",
		"message_delta": "append_agent_text",
		"message_received": "show_agent_message",
		"completed": "finish_agent_response",
		"models_received": "set_model_options",
		"execution_changed": "show_execution",
		"timing_received": "show_timing",
	}
	for signal_name: String in links:
		chat.connect(signal_name, Callable(dock, links[signal_name]))
	chat.connect("error_received", Callable(dock, "show_agent_message").bind(true))
	dock.connect("prompt_submitted", Callable(chat, "submit"))
	dock.connect("cancel_requested", Callable(chat, "cancel_turn"))
	var review := {}
	# A paired phone's send, Stop, Go and decisions, through the sidebar (review_flow.gd).
	var plugin := RelayPlugin.new()
	plugin._dock = dock
	plugin._file_review = review
	load("res://addons/joinery/remote_relay.gd").call("attach", dock, plugin, chat)
	chat.connect(
		"file_preview",
		func(event: Dictionary) -> void:
			review.merge(event, true)
			dock.call(
				"show_file_review",
				str(event.get("summary", "")),
				str(event.get("diff", "")),
				event.get("media", []),
				event.get("changes", []),
				str(event.get("transaction_id", ""))
			)
	)
	# As plugin.gd decides: never while the chat host is away (the decision would go nowhere).
	var decide := func(accept: bool) -> void:
		if not bool(chat.call("is_ready")):
			dock.call("show_review_error", "Journeyman isn't connected. Reconnect, then decide.")
			return
		plugin._deciding = true
		chat.call("decide_files", str(review.get("transaction_id", "")), accept)
	dock.connect("accept_edit_requested", decide.bind(true))
	dock.connect("reject_edit_requested", decide.bind(false))
	chat.connect(
		"file_decision",
		func(event: Dictionary) -> void:
			plugin._deciding = false
			# As plugin.gd: the decided change is no longer the one in review.
			review.clear()
			var accepted := bool(event.get("accepted", false))
			dock.call(
				"hide_edit_review",
				"Change accepted" if accepted else "Discarded",
				"success" if accepted else "rejected"
			)
	)


func _teardown() -> void:
	if is_instance_valid(_host):
		_host.queue_free()
	await process_frame
