extends SceneTree
## The console error feed (sidebar_debug.gd) and Godot's import cache: errors a scene prints
## while a change's new model is still being imported are held, read as Output shows them (a
## thin space before "ERROR:"), dropped once the model's imported copy is there, imported
## again once when it isn't, and only then reported, all in one report the real chat host
## code turns into one finding per model.

const DEBUG := preload("res://addons/joinery/sidebar_debug.gd")
## How Output starts an error line: the icon (read as a space), a thin space, the label.
const OUTPUT_PREFIX := " \u2009ERROR: "

var _failed := false


## Stands in for the chat bridge: what the console error feed reports.
class FakeBridge:
	extends RefCounted
	var reported: Array[String] = []

	func report_errors(_source: String, text: String) -> void:
		reported.append(text)


class FakeTasks:
	extends Node
	var bridge: Object


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	await _held_until_imported()
	print("import hold: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


func _copy(model: String) -> String:
	return "res://.godot/imported/%s-%s.scn" % [model.get_file(), model.md5_text()]


## A model a change just wrote: the file and its .import, but no imported copy yet.
func _plant(model: String) -> void:
	DirAccess.make_dir_recursive_absolute(model.get_base_dir())
	DirAccess.make_dir_recursive_absolute(_copy(model).get_base_dir())
	var file := FileAccess.open(model, FileAccess.WRITE)
	file.store_string("glTF")
	file.close()
	var config := ConfigFile.new()
	config.set_value("remap", "importer", "scene")
	config.set_value("remap", "type", "PackedScene")
	config.set_value("remap", "path", _copy(model))
	config.set_value("deps", "source_file", model)
	config.set_value("deps", "dest_files", [_copy(model)])
	config.save(model + ".import")
	DirAccess.remove_absolute(_copy(model))


## The editor finished importing [param model].
func _import(model: String) -> void:
	var file := FileAccess.open(_copy(model), FileAccess.WRITE)
	file.store_string("RSRC")
	file.close()


## What Output shows when a scene loads [param model] before its import ends.
func _lines(model: String) -> String:
	return (
		OUTPUT_PREFIX
		+ "Cannot open file '%s'.\n" % _copy(model)
		+ OUTPUT_PREFIX
		+ "Failed loading resource: %s.\n" % _copy(model)
		+ OUTPUT_PREFIX
		+ "Failed loading resource: %s.\n" % model
	)


## Waits out a held error's hold, by the same clock the feed reads.
func _wait_hold() -> void:
	var until := Time.get_ticks_msec() + int((DEBUG.HOLD + 0.1) * 1000.0)
	while Time.get_ticks_msec() < until:
		await process_frame


func _held_until_imported() -> void:
	var bin := "res://art/props/green_waste_bin.glb"
	var rake := "res://art/props/leaf_rake.glb"
	var hose := "res://art/props/hose_coupler.glb"
	for model: String in [bin, rake, hose]:
		_plant(model)
	var bridge := FakeBridge.new()
	var tasks := FakeTasks.new()
	tasks.bridge = bridge
	root.add_child(tasks)
	var watcher: Node = DEBUG.new()
	watcher.set("tasks", tasks)
	var imported_again: Array[PackedStringArray] = []
	# The editor's reimport: the hose coupler imports when asked again, the rake never does.
	var reimport := func(paths: PackedStringArray) -> void:
		imported_again.append(paths)
		if paths.has(hose):
			_import(hose)
	watcher.set("reimport", reimport)
	tasks.add_child(watcher)
	# The test steps the feed itself.
	watcher.set_process(false)
	watcher.call(
		"take_output",
		(
			_lines(bin)
			+ _lines(rake)
			+ _lines(hose)
			+ OUTPUT_PREFIX
			+ "Failed loading resource: res://art/props/never_made.glb.\n"
		)
	)
	var reported := bridge.reported
	_check(
		reported.size() == 1 and reported[0].contains("never_made.glb"),
		"Only the error that is not import noise goes at once: %s" % [reported]
	)
	_check(
		not "\n".join(reported).contains(".godot/imported"),
		"Errors naming a model still being imported are held, thin space and all"
	)
	_check(watcher.call("last_error").contains("never_made"), "/debug picks the real error")
	# The bin's import ends: its errors go, unreported. The others are imported again, once.
	_import(bin)
	await _wait_hold()
	watcher.call("release_held")
	_check(reported.size() == 1, "Nothing more is reported before the reimport: %s" % [reported])
	_check(
		imported_again.size() == 1 and imported_again[0] == PackedStringArray([rake, hose]),
		"The models that still don't load are imported again, once each: %s" % [imported_again]
	)
	await _wait_hold()
	watcher.call("release_held")
	var text := "\n".join(reported)
	_check(imported_again.size() == 1, "No second reimport: %s" % [imported_again])
	_check(not text.contains("green_waste_bin"), "An import that finished is never reported")
	_check(not text.contains("hose_coupler"), "One that the reimport fixed is never reported")
	_check(
		reported.size() == 2 and reported[1].count("leaf_rake.glb") == 3,
		"The rake's three lines go together, once: %s" % [reported]
	)
	_check(watcher.call("last_error").contains("leaf_rake"), "/debug picks the rake's error")
	if reported.size() == 2:
		_check_findings(reported[1], rake)
	watcher.call("release_held")
	_check(reported.size() == 2, "Nothing is reported twice")
	tasks.queue_free()
	await process_frame


## The chat host's own code reads the report: one finding for the rake, with its cause.
func _check_findings(report: String, rake: String) -> void:
	var python := OS.get_environment("JOINERY_TEST_PYTHON")
	if python.is_empty():
		_check(false, "JOINERY_TEST_PYTHON is not set")
		return
	var script := (
		"import json, sys\nfrom pathlib import Path\n"
		+ "from joinery import console_errors as ce\n"
		+ "entries = ce.fold_imports(ce.parse(sys.argv[1]), Path(sys.argv[2]))\n"
		+ "print(json.dumps([[e.message, e.where] for e in entries]))\n"
	)
	var out: Array = []
	var code := OS.execute(
		python, ["-c", script, report, ProjectSettings.globalize_path("res://")], out, true
	)
	var found: Variant = JSON.parse_string(str(out[0]).strip_edges().get_slice("\n", 0))
	_check(code == 0 and found is Array, "The host reads the report: %s" % [out])
	if not found is Array:
		return
	_check((found as Array).size() == 1, "One finding for the rake: %s" % [found])
	var finding: Array = (found as Array)[0]
	_check(
		str(finding[0]).begins_with("leaf_rake.glb didn't import") and finding[1] == rake,
		"It says the rake didn't import: %s" % [finding]
	)
