extends SceneTree
## Finding a scene by name (scene_finder.gd), headless: which files a name matches, and that
## "/open" asks the editor for the right thing. One scene the name matches opens straight away
## (open_scene_from_path); several, or none, show Godot's Quick Open Scene picker with the name
## typed in; a bare "/open" shows the picker. A name that matches no scene but one script opens
## it in the script editor, and one other file is selected in FileSystem. A stand-in records
## what the editor was asked.

const FINDER := preload("res://addons/joinery/scene_finder.gd")
const SLASH := preload("res://addons/joinery/slash_commands.gd")
const PROBE := "user://finder_probe.gd"
const EDITOR_SOURCE := """extends RefCounted
var calls: Array = []
func popup_quick_open(callback: Callable, types: Array) -> void:
	calls.append(["popup_quick_open", types])
func open_scene_from_path(path: String) -> void:
	calls.append(["open_scene_from_path", path])
func edit_script(script: Script) -> void:
	calls.append(["edit_script", script.resource_path if script != null else ""])
func select_file(path: String) -> void:
	calls.append(["select_file", path])
"""
const PATHS := [
	"res://main.tscn",
	"res://levels/cave.tscn",
	"res://levels/harbour.tscn",
	"res://levels/harbour_night.tscn",
	"res://ui/main_menu.tscn",
	"res://player/player.tscn",
	"res://player/player_hud.tscn",
	PROBE,
	"res://art/lamp.png",
]

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	_check_matching()
	var file := FileAccess.open(PROBE, FileAccess.WRITE)
	file.store_string("extends Node\n")
	file.close()
	var finder: RefCounted = FINDER.new()
	var editor := _stand_in()
	var calls: Array = editor.get("calls")
	finder.set("editor", editor)
	finder.set("paths", PackedStringArray(PATHS))
	var cases := [
		["main", ["open_scene_from_path", "res://main.tscn"]],
		["Main Menu", ["open_scene_from_path", "res://ui/main_menu.tscn"]],
		["levels/cave", ["open_scene_from_path", "res://levels/cave.tscn"]],
		["harbour.tscn", ["open_scene_from_path", "res://levels/harbour.tscn"]],
		["cav", ["open_scene_from_path", "res://levels/cave.tscn"]],
		["player", ["open_scene_from_path", "res://player/player.tscn"]],
		["harb", ["popup_quick_open", [&"PackedScene"]], "harb"],
		["levels", ["popup_quick_open", [&"PackedScene"]], "levels"],
		["nothing like it", ["popup_quick_open", [&"PackedScene"]], "nothing like it"],
		["", ["popup_quick_open", [&"PackedScene"]], ""],
		["finder_probe", ["edit_script", PROBE]],
		["lamp", ["select_file", "res://art/lamp.png"]],
	]
	for case: Array in cases:
		calls.clear()
		finder.set("last_filter", "-")
		finder.call("open", case[0])
		_check(calls == [case[1]], "/open %s asks for %s, not %s" % [case[0], case[1], calls])
		if case.size() > 2:
			_check(
				finder.get("last_filter") == case[2],
				'/open %s types "%s" into the picker' % [case[0], case[2]]
			)
	# The sidebar command reaches the same finder.
	var slash: Node = SLASH.new()
	root.add_child(slash)
	slash.set("finder", finder)
	_check(SLASH.resolve("/open") == "/open", "/open is a sidebar command")
	_check(SLASH.is_local("/open cave"), "/open runs at once, even while Journeyman works")
	_check(not SLASH.is_prompt("/open cave"), "/open is not a request for Journeyman")
	var entry: Array = SLASH.COMMANDS["/open"]
	_check(str(entry[0]).length() <= 32, "/open's help line fits the command menu")
	for typed: Array in [
		["/open cave", ["open_scene_from_path", "res://levels/cave.tscn"]],
		["/open level", ["popup_quick_open", [&"PackedScene"]]],
		["/open", ["popup_quick_open", [&"PackedScene"]]],
	]:
		calls.clear()
		_check(slash.call("run", typed[0]), typed[0] + " runs")
		_check(calls == [typed[1]], "%s asks for %s: %s" % [typed[0], typed[1], calls])
	slash.queue_free()
	DirAccess.remove_absolute(ProjectSettings.globalize_path(PROBE))
	await process_frame
	print("scene finder: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _check_matching() -> void:
	var paths := Array(PATHS)
	var expect := {
		"main": ["res://main.tscn"],
		"main_menu": ["res://ui/main_menu.tscn"],
		"res://levels/cave.tscn": ["res://levels/cave.tscn"],
		"harbour": ["res://levels/harbour.tscn"],
		"harbour night": ["res://levels/harbour_night.tscn"],
		"harb": ["res://levels/harbour.tscn", "res://levels/harbour_night.tscn"],
		"hud": ["res://player/player_hud.tscn"],
		"zzz": [],
	}
	for query: String in expect:
		var found := Array(FINDER.best(query, paths))
		_check(found == expect[query], "%s matches %s, not %s" % [query, expect[query], found])


func _stand_in() -> RefCounted:
	var script := GDScript.new()
	script.source_code = EDITOR_SOURCE
	script.reload()
	return script.new()
