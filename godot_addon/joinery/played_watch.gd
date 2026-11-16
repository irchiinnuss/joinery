@tool
extends Node
## Keeps "not yet played by you" up to date in the editor (joinery/played.py).
##
## The game core tells the editor through the debugger when a person has played an editor run
## long enough ("joinery:played", core/replays/play_report.gd); this records it with
## `joinery played record`. An accepted change's decision carries the new state; otherwise the
## state is read with `joinery played status` when the editor starts and when the user comes
## back to it. Whatever is current goes to everything in the "joinery_unplayed" group: the
## sidebar header's count, each change card and the Timeline call `show_unplayed(state)` with
## {count, text, ids, commits}.
##
## On stock Godot without the game core nothing reports a play, and the stamps simply stay.

const SELF_PATH := "res://addons/joinery/played_watch.gd"
const COMMAND := preload("command_job.gd")
const GROUP := &"joinery_unplayed"
## The debugger message the game core sends, and its prefix.
const PREFIX := "joinery"
const MESSAGE := "joinery:played"


## Hears the game core's message in every editor run's debugger session.
class Listener:
	extends EditorDebuggerPlugin
	signal played(started: float, seconds: float)

	func _has_capture(prefix: String) -> bool:
		return prefix == PREFIX

	func _capture(message: String, data: Array, _session_id: int) -> bool:
		if message != MESSAGE or data.size() < 2:
			return false
		played.emit(float(data[0]), float(data[1]))
		return true


## The Joinery CLI as tests set it ([] for the engine checkout's `uv run joinery`).
var cli_prefix := PackedStringArray()
## Off in tests that feed states by hand.
var cli_enabled := true
## The last state read: {count, text, ids, commits}.
var state: Dictionary = {"count": 0, "text": "", "ids": [], "commits": []}
var _listener: Listener
var _job: RefCounted
## Commands waiting for the running one, in order.
var _queued: Array[Array] = []


## Add the watch to [param plugin]: the debugger listener comes and goes with it.
static func install(plugin: EditorPlugin) -> Node:
	var watch: Node = load(SELF_PATH).new()
	watch.name = "JoineryPlayed"
	plugin.add_child(watch)
	var listener := Listener.new()
	listener.played.connect(watch.record)
	watch.set("_listener", listener)
	plugin.add_debugger_plugin(listener)
	plugin.tree_exiting.connect(
		func() -> void:
			if is_instance_valid(watch):
				plugin.remove_debugger_plugin(watch.get("_listener"))
				watch.queue_free(),
		CONNECT_ONE_SHOT
	)
	return watch


func _ready() -> void:
	set_process(false)
	refresh.call_deferred()


func _notification(what: int) -> void:
	if what == NOTIFICATION_APPLICATION_FOCUS_IN:
		# A play recorded from a terminal, or a revert, shows when the user comes back.
		refresh()


func _exit_tree() -> void:
	if _job != null:
		_job.call("cancel")
		_job = null


## Read the state again (`joinery played status`).
func refresh() -> void:
	_run(["played", "status", "--project", _project(), "--json"])


## A person's editor run, started at [param started] (seconds since the epoch), lasted
## [param seconds]: record it (`joinery played record`).
func record(started: float, seconds: float) -> void:
	_run(
		[
			"played",
			"record",
			"--project",
			_project(),
			"--started",
			str(started),
			"--seconds",
			str(seconds),
			"--json",
		]
	)


## A change was decided (the chat's file_decision): an accepted one says the new state.
func on_decision(event: Dictionary) -> void:
	if event.get("unplayed") is Dictionary:
		apply(event["unplayed"])


## Take [param data] ({count, text, ids, commits}) as current and tell the group.
func apply(data: Dictionary) -> void:
	state = {
		"count": int(data.get("count", 0)),
		"text": str(data.get("text", "")),
		"ids": data.get("ids", []) if data.get("ids") is Array else [],
		"commits": data.get("commits", []) if data.get("commits") is Array else [],
	}
	if is_inside_tree():
		get_tree().call_group(GROUP, &"show_unplayed", state)


func _run(args: Array) -> void:
	if not cli_enabled:
		return
	if _job != null:
		# One at a time; a play recorded while a read runs is not lost.
		if not _queued.has(args):
			_queued.append(args)
		return
	_job = COMMAND.joinery(cli_prefix, args)
	set_process(_job != null)


func _process(_delta: float) -> void:
	_job = COMMAND.finish(_job, _finished)
	if _job == null and not _queued.is_empty():
		_run(_queued.pop_front())
	set_process(_job != null)


func _finished(result: Dictionary) -> void:
	if int(result.get("code", 1)) != 0:
		return
	var data: Variant = COMMAND.parse(str(result.get("output", "")).get_slice("\n", 0))
	if data is Dictionary:
		apply(data)


static func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")
