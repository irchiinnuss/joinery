extends SceneTree
## "/mix" in the sidebar, headless (sidebar_mix.gd): Godot's Audio panel is the mixer, and the
## mix changes it doesn't make go to the chat host as one reviewed change. The project has only
## Master and a music track planted by run_widget_tests.py (music_fixture.py). Checks the exact
## mixer_save message for the game core's buses, ducking, a bus level or flag and one music
## layer; that nothing is sent or written without Journeyman, that a bare /mix says how, and
## that other words become a request in the message box.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const MIX := preload("res://addons/joinery/sidebar_mix.gd")
const TRACK_PATH := "res://music/harbour.tres"
const JOB_SOURCE := """extends RefCounted
var lines: Array[Dictionary] = []
func send_line(line: String) -> void:
	lines.append(JSON.parse_string(line))
func take_lines() -> Array:
	return []
func poll() -> Dictionary:
	return {}
func close_pipes() -> void:
	pass
"""

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	_check_messages()
	var dock: Control = load(DOCK_PATH).new()
	root.add_child(dock)
	dock.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	_check(
		load("res://addons/joinery/slash_commands.gd").call("resolve", "/mix") == "/mix",
		"/mix is a sidebar command"
	)
	# Without Journeyman nothing is sent, and the footer says why.
	var layout := FileAccess.get_file_as_string("res://default_bus_layout.tres")
	dock.call("run_command", "/mix buses")
	await process_frame
	_check(AudioServer.bus_count == 1, "Nothing plays or changes here")
	_check(
		FileAccess.get_file_as_string("res://default_bus_layout.tres") == layout,
		"Nothing is written here"
	)
	var backend: Node = load("res://addons/joinery/chat_bridge.gd").new()
	dock.add_child(backend)
	var fake := GDScript.new()
	fake.source_code = JOB_SOURCE
	fake.reload()
	var job: RefCounted = fake.new()
	backend.set("_job", job)
	backend.call("_handle", {"kind": "connection", "state": "connected", "parallel": 3})
	backend.set("_job", job)
	dock.call("set_chat_state", "connected", "")
	await process_frame
	dock.call("run_command", "/mix duck voice")
	await process_frame
	var lines: Array = job.get("lines")
	_check(lines.size() == 1, "One line per /mix: %s" % [lines])
	if not lines.is_empty():
		var sent: Dictionary = lines[-1]
		_check(sent.get("kind") == "mixer_save", "It is a mixer_save: %s" % sent)
		_check(sent.get("bus_layout") == {"ducking": "Voice"}, "Only the ducking: %s" % sent)
	# A bare /mix says how; other words become a request the user sends.
	dock.call("run_command", "/mix")
	await process_frame
	_check(lines.size() == 1, "A bare /mix sends nothing")
	dock.call("run_command", "/mix make the music quieter in combat")
	await process_frame
	_check(lines.size() == 1, "Words are not sent by themselves")
	_check(
		str(dock.get("_prompt").get("text")).contains("make the music quieter in combat"),
		"They wait in the message box: %s" % dock.get("_prompt").get("text")
	)
	dock.queue_free()
	await process_frame
	print("mix command: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_messages() -> void:
	var buses: Dictionary = MIX.message(PackedStringArray(["buses"]))
	var names: Array = []
	for bus: Dictionary in buses.get("bus_layout", {}).get("buses", []):
		names.append(bus["name"])
	_check(
		names == ["Master", "Music", "SFX", "Ambience", "UI", "Voice"], "Core buses %s" % [names]
	)
	_check(
		MIX.message(PackedStringArray(["duck", "off"])) == {"bus_layout": {"ducking": ""}},
		"Ducking off"
	)
	_check(
		(
			MIX.message(PackedStringArray(["music", "-4.5"]))
			== {"bus_layout": {"buses": [{"name": "Music", "volume_db": -4.5}]}}
		),
		"A core bus level, as the core spells it"
	)
	_check(
		(
			MIX.message(PackedStringArray(["Master", "mute"]))
			== {"bus_layout": {"buses": [{"name": "Master", "mute": true}]}}
		),
		"A bus flag"
	)
	_check(MIX.message(PackedStringArray(["Radio", "-3"])).is_empty(), "No such bus")
	_check(FileAccess.file_exists(TRACK_PATH), "The planted track exists")
	_check(
		(
			MIX.message(PackedStringArray(["harbour", "pads", "mute"]))
			== {"tracks": {TRACK_PATH: {"layers": {"pads": {"muted": true}}}}}
		),
		"A music layer flag"
	)
	_check(
		(
			MIX.message(PackedStringArray(["harbour", "lead", "-3dB"]))
			== {"tracks": {TRACK_PATH: {"layers": {"lead": {"volume_db": -3.0}}}}}
		),
		"A music layer level"
	)
	_check(MIX.message(PackedStringArray(["nowhere", "lead", "-3"])).is_empty(), "No such track")
	_check(MIX.message(PackedStringArray(["louder"])).is_empty(), "Not a mix command")
