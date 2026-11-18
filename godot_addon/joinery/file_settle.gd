@tool
extends RefCounted
## Brings files a change wrote (or a revert restored) into the editor in order: the project is
## scanned and every new or changed model, texture or sound is imported before any scene that
## uses it is opened or reloaded. A scene loaded earlier fails with "missing dependencies".
##
## EditorFileSystem's scan thread clears its scanning flag before the editor imports what it
## found, so is_scanning() alone reads idle a frame too early. The scan is awaited through
## filesystem_changed instead, which the editor emits once those imports are done.

const TIMEOUT_MSEC := 60000
## Milliseconds after a scan ends that errors naming its files still count as import noise.
const WINDOW_MSEC := 10000
const IMPORTED := "res://.godot/imported/"
## Files Godot loads as they are, never through an import.
const NATIVE := [
	"tscn", "scn", "tres", "res", "gd", "cs", "gdshader", "gdshaderinc", "gdextension", "json"
]

## Files a change just wrote or a revert restored: res:// path -> until when (msec) an error
## naming one is import noise, not a game error. Read by the console error feed.
static var recent := {}
## Scans still running.
static var _busy := 0
static var _paths := RegEx.create_from_string("res://[^\\s'\"(),]+")
## Errors that mean a resource couldn't be read: only these hold a file that has a .import.
static var _load_failed := RegEx.create_from_string(
	(
		"(Failed loading resource|Cannot open file|Failed to load resource|Resource file not found"
		+ "|No loader found for resource|Error loading resource)"
	)
)


## Scans the project and returns once the editor has indexed and imported what it found.
## [param paths] (res:// paths) are the files that landed: errors naming them while they are
## imported are held back from the console error feed ([method import_noise]).
static func scan(paths: Array[String] = []) -> void:
	_busy += 1
	for path: String in paths:
		recent[path] = Time.get_ticks_msec() + TIMEOUT_MSEC + WINDOW_MSEC
	await _scan()
	_busy -= 1
	for path: String in paths:
		recent[path] = Time.get_ticks_msec() + WINDOW_MSEC


static func _scan() -> void:
	var tree := Engine.get_main_loop() as SceneTree
	var files := EditorInterface.get_resource_filesystem()
	var deadline := Time.get_ticks_msec() + TIMEOUT_MSEC
	# A scan already running may have started before the files landed: let it end, then scan.
	await _idle(tree, files, deadline)
	var done := {"changed": false}
	var mark := func() -> void: done["changed"] = true
	files.filesystem_changed.connect(mark)
	files.scan()
	while not done["changed"] and files.is_scanning() and Time.get_ticks_msec() < deadline:
		await tree.process_frame
	# The scan thread has ended; the editor imports and emits filesystem_changed in its next
	# process step.
	while not done["changed"] and Time.get_ticks_msec() < deadline:
		await tree.process_frame
		if not done["changed"] and not files.is_scanning() and not files.is_importing():
			# Nothing new reported within a frame of going idle: a scan that found no change.
			await tree.process_frame
			break
	files.filesystem_changed.disconnect(mark)
	await _idle(tree, files, deadline)


## True when [param scene] and every scene it instances can load: each resource they use
## exists and is imported. A file on disk the editor has not imported yet is imported now.
static func loadable(scene: String) -> bool:
	var missing := missing_dependencies(scene)
	if missing.is_empty():
		return true
	var files := EditorInterface.get_resource_filesystem()
	var pending := PackedStringArray()
	for path: String in missing:
		if FileAccess.file_exists(path) and _importable(path):
			pending.append(path)
	if pending.is_empty() or files.is_importing():
		return false
	for path: String in pending:
		files.update_file(path)
	files.reimport_files(pending)
	return missing_dependencies(scene).is_empty()


## The resources [param scene] and the scenes it instances use that cannot be loaded yet.
static func missing_dependencies(scene: String) -> PackedStringArray:
	var missing := PackedStringArray()
	for dependency: String in dependencies(scene):
		if not ResourceLoader.exists(dependency):
			missing.append(dependency)
	return missing


## Every resource [param scene] uses, through the scenes it instances too.
static func dependencies(scene: String) -> PackedStringArray:
	var found := PackedStringArray()
	var queue: Array[String] = [scene]
	while not queue.is_empty():
		var path: String = queue.pop_back()
		for entry: String in ResourceLoader.get_dependencies(path):
			# "uid://…::Type::res://path", or just the path when the scene names no uid.
			var dependency := entry.get_slice("::", entry.get_slice_count("::") - 1)
			if dependency.begins_with("uid://"):
				var id := ResourceUID.text_to_id(dependency)
				if ResourceUID.has_id(id):
					dependency = ResourceUID.get_id_path(id)
			if found.has(dependency):
				continue
			found.append(dependency)
			if dependency.get_extension() in ["tscn", "scn"] and ResourceLoader.exists(dependency):
				queue.append(dependency)
	return found


## Reloads the open [param scenes] that use none of the [param touched] files, before a scan:
## the editor then stops holding files a revert removed. Returns the scenes left to reload.
static func reload_untouched(scenes: Array[String], touched: Array[String]) -> Array[String]:
	var unsaved := EditorInterface.get_unsaved_scenes()
	var left: Array[String] = []
	for path: String in scenes:
		var clean := not unsaved.has(path)
		for dependency: String in dependencies(path):
			if touched.has(dependency) or not ResourceLoader.exists(dependency):
				clean = false
		if clean:
			EditorInterface.reload_scene_from_path(path)
		else:
			left.append(path)
	return left


## Reloads the open [param scenes] once their files are imported. Returns a note for each one
## left as it is: a scene with unsaved edits (they would be lost) or one that uses missing files.
static func reload_open(scenes: Array[String]) -> Array[String]:
	var unsaved := EditorInterface.get_unsaved_scenes()
	var kept: Array[String] = []
	for path: String in scenes:
		if unsaved.has(path):
			kept.append(path.get_file() + " (unsaved edits)")
		elif not loadable(path):
			kept.append(path.get_file() + " (uses missing files)")
		else:
			EditorInterface.reload_scene_from_path(path)
	return kept


## True while files are still being scanned or imported, or were within the last moments.
static func settling() -> bool:
	var now := Time.get_ticks_msec()
	for path: String in recent.keys():
		if int(recent[path]) < now:
			recent.erase(path)
	if _busy > 0 or not recent.is_empty():
		return true
	if not Engine.is_editor_hint():
		return false
	var files := EditorInterface.get_resource_filesystem()
	return files.is_scanning() or files.is_importing()


## The files an error line names that are still being imported: an imported copy under
## .godot/imported, a file a change just wrote, one the editor has not imported yet, or (for
## an error that a resource couldn't be read) a file the editor imports. Empty when the error
## is not import noise.
static func import_noise(line: String) -> Array[String]:
	var found: Array[String] = []
	var load_failed := _load_failed.search(line) != null
	for hit: RegExMatch in _paths.search_all(line):
		var path := hit.get_string().rstrip(".:'")
		var place := path.rfind(":")
		if place > "res://".length() and path.substr(place + 1).is_valid_int():
			path = path.left(place)
		if found.has(path):
			continue
		if (
			path.begins_with(IMPORTED)
			or recent.has(path)
			or _unimported(path)
			or (load_failed and FileAccess.file_exists(path + ".import"))
		):
			found.append(path)
	return found


## True when every one of [param paths] loads now (an imported copy exists, a scene's
## dependencies resolve).
static func loads(paths: Array[String]) -> bool:
	for path: String in paths:
		if path.begins_with(IMPORTED):
			if not FileAccess.file_exists(path):
				return false
		elif FileAccess.file_exists(path + ".import"):
			if not FileAccess.file_exists(path) or not imported(path):
				return false
		elif not ResourceLoader.exists(path) or _unimported(path):
			return false
		elif path.get_extension() in ["tscn", "scn"] and not missing_dependencies(path).is_empty():
			return false
	return true


## True when [param path] has no .import, or its .import says the import worked and every
## copy it lists under .godot/imported is there.
static func imported(path: String) -> bool:
	if not FileAccess.file_exists(path + ".import"):
		return true
	var config := ConfigFile.new()
	if config.load(path + ".import") != OK or not bool(config.get_value("remap", "valid", true)):
		return false
	var copies: Array = config.get_value("deps", "dest_files", [])
	if copies.is_empty() and config.has_section_key("remap", "path"):
		copies = [config.get_value("remap", "path")]
	for copy: Variant in copies:
		if not FileAccess.file_exists(str(copy)):
			return false
	return true


## The files to import again so [param paths] load: each one the editor imports, and the file
## each imported copy was made from. Godot names a copy after its file and the MD5 of its path.
static func sources(paths: Array[String]) -> PackedStringArray:
	var found := PackedStringArray()
	for path: String in paths:
		var source := path
		if path.begins_with(IMPORTED):
			source = source_of(path)
		if not source.is_empty() and not found.has(source):
			var known := FileAccess.file_exists(source + ".import") or _importable(source)
			if FileAccess.file_exists(source) and known:
				found.append(source)
	return found


## The project file the imported copy [param copy] was made from, or "" when none is.
static func source_of(copy: String) -> String:
	var name := copy.get_file()
	var dash := name.rfind("-")
	if dash < 0:
		return ""
	var file := name.left(dash)
	var digest := name.substr(dash + 1).get_basename()
	var folders: Array[String] = ["res://"]
	while not folders.is_empty():
		var folder: String = folders.pop_back()
		var dir := DirAccess.open(folder)
		if dir == null:
			continue
		var base := folder if folder.ends_with("/") else folder + "/"
		for child: String in dir.get_directories():
			if not child.begins_with("."):
				folders.append(base + child)
		for candidate: String in [file, file + ".import"]:
			var path := base + candidate.trim_suffix(".import")
			if dir.file_exists(candidate) and path.md5_text() == digest:
				return path
	return ""


## Imports [param paths] again (the editor only).
static func reimport(paths: PackedStringArray) -> void:
	if paths.is_empty() or not Engine.is_editor_hint():
		return
	var files := EditorInterface.get_resource_filesystem()
	if files.is_importing():
		return
	for path: String in paths:
		files.update_file(path)
	files.reimport_files(paths)


static func _unimported(path: String) -> bool:
	return (
		FileAccess.file_exists(path)
		and not FileAccess.file_exists(path + ".import")
		and _importable(path)
	)


## A file the editor imports (a model, image, sound), not one Godot loads as it is.
static func _importable(path: String) -> bool:
	var extension := path.get_extension().to_lower()
	return (
		extension not in NATIVE
		and ResourceLoader.get_recognized_extensions_for_type("Resource").has(extension)
	)


static func _idle(tree: SceneTree, files: EditorFileSystem, deadline: int) -> void:
	# Two idle frames in a row: the frame a scan thread ends in still has the import to run.
	var quiet := 0
	while quiet < 2 and Time.get_ticks_msec() < deadline:
		await tree.process_frame
		quiet = 0 if files.is_scanning() or files.is_importing() else quiet + 1
