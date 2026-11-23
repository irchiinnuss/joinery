@tool
extends RefCounted
## Brings files a change wrote (or a revert restored) into the editor in order: the project is
## scanned and every new or changed model, texture or sound is imported before any scene that
## uses it is opened or reloaded. A scene loaded earlier fails with "missing dependencies".
##
## EditorFileSystem's scan thread clears its scanning flag before the editor imports what it
## found, so is_scanning() alone reads idle a frame too early. The scan is awaited through
## filesystem_changed instead, which the editor emits once those imports are done.
##
## A scene opens or reloads only once every file it uses is imported: a model the change brought
## with its .import (Blender's check writes one) is known to the editor, and to ResourceLoader,
## before its imported copy exists, and a scene loaded then fails the same way. Each waits for the
## import (a while), and one still waiting is opened or reloaded once the import lands ([method
## later]).
##
## No open scene is reloaded while the editor reimports: from resources_reimporting to
## resources_reimported it holds the open scenes' nodes that use the files to swap in the new
## import, and its progress dialog runs frames meanwhile, so an awaiting coroutine resumes in the
## middle. is_importing() reads false before resources_reimported, so that window is tracked
## through the two signals ([method watch]). A scene reloaded inside it freed those nodes and the
## editor crashed swapping them.

const TIMEOUT_MSEC := 60000
## Milliseconds a scene waits for the files it uses to import before it is left for later.
const WAIT_MSEC := 30000
## Milliseconds after a scan ends that errors naming its files still count as import noise.
const WINDOW_MSEC := 10000
const IMPORTED := "res://.godot/imported/"
## Godot's editor setting: reload open scripts changed on disk without asking.
const AUTO_RELOAD := "text_editor/behavior/files/auto_reload_scripts_on_external_change"
## Files Godot loads as they are, never through an import.
const NATIVE := [
	"tscn", "scn", "tres", "res", "gd", "cs", "gdshader", "gdshaderinc", "gdextension", "json"
]

## Files a change just wrote or a revert restored: res:// path -> until when (msec) an error
## naming one is import noise, not a game error. Read by the console error feed.
static var recent := {}
## Scans still running.
static var _busy := 0
## Reimports the editor has begun and not finished (resources_reimporting, not yet
## resources_reimported).
static var _reimports := 0
static var _watching := false
## Scenes left until the files they use are imported: res:// path -> true to open it, false to
## reload it only if it is open ([method later]).
static var _later := {}
static var _paths := RegEx.create_from_string("res://[^\\s'\"(),]+")
## Errors that mean a resource couldn't be read: only these hold a file that has a .import.
static var _load_failed := RegEx.create_from_string(
	(
		"(Failed loading resource|Cannot open file|Failed to load resource|Resource file not found"
		+ "|No loader found for resource|Error loading resource)"
	)
)


## Follows the editor's reimports ([method importing]). Call it as the editor starts, so a reimport
## that begins before the first scan is seen.
static func watch() -> void:
	if _watching or not Engine.is_editor_hint():
		return
	_watching = true
	var files := EditorInterface.get_resource_filesystem()
	files.resources_reimporting.connect(_reimport_began)
	files.resources_reimported.connect(_reimport_ended)


## True from the editor's resources_reimporting until its resources_reimported, and while it
## imports: no open scene may be reloaded or closed then.
static func importing() -> bool:
	if not Engine.is_editor_hint():
		return false
	return _reimports > 0 or EditorInterface.get_resource_filesystem().is_importing()


## Returns once the editor is not reimporting ([method importing]), or after the timeout.
static func imports_done() -> void:
	watch()
	var tree := Engine.get_main_loop() as SceneTree
	var deadline := Time.get_ticks_msec() + TIMEOUT_MSEC
	while importing() and Time.get_ticks_msec() < deadline:
		await tree.process_frame


static func _reimport_began(_paths: PackedStringArray) -> void:
	_reimports += 1


static func _reimport_ended(_paths: PackedStringArray) -> void:
	_reimports = maxi(_reimports - 1, 0)


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
	watch()
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
		if not done["changed"] and not files.is_scanning() and not importing():
			# Nothing new reported within a frame of going idle: a scan that found no change.
			await tree.process_frame
			break
	files.filesystem_changed.disconnect(mark)
	await _idle(tree, files, deadline)


## True when [param scene] and every scene it instances can load: each resource they use
## exists and is imported. A file on disk the editor has not imported yet is imported now, unless
## the editor is scanning or importing (it imports what it finds then).
static func loadable(scene: String) -> bool:
	var missing := missing_dependencies(scene)
	if missing.is_empty():
		return true
	var files := EditorInterface.get_resource_filesystem()
	if importing() or files.is_scanning():
		return false
	var pending := PackedStringArray()
	for path: String in missing:
		var known := FileAccess.file_exists(path + ".import") or _importable(path)
		if FileAccess.file_exists(path) and known:
			pending.append(path)
	if pending.is_empty():
		return false
	for path: String in pending:
		files.update_file(path)
	files.reimport_files(pending)
	return missing_dependencies(scene).is_empty()


## The resources [param scene] and the scenes it instances use that cannot be loaded yet: each
## one that is not there, and each one the editor imports that has no imported copy yet.
static func missing_dependencies(scene: String) -> PackedStringArray:
	var missing := PackedStringArray()
	for dependency: String in dependencies(scene):
		if not _ready(dependency):
			missing.append(dependency)
	return missing


## True when [param path] loads now. ResourceLoader.exists() alone is true for a model the
## editor knows of, or one with a .import, before its imported copy is made.
static func _ready(path: String) -> bool:
	if FileAccess.file_exists(path + ".import"):
		return FileAccess.file_exists(path) and imported(path)
	return ResourceLoader.exists(path) and not _unimported(path)


## Waits until each of [param scenes] can load ([method loadable]): while the editor scans or
## imports, for [constant WAIT_MSEC] at most, then imports what it has not. Returns the ones that
## still cannot.
static func await_loadable(scenes: Array[String]) -> Array[String]:
	var left: Array[String] = []
	for path: String in scenes:
		if not missing_dependencies(path).is_empty():
			left.append(path)
	if left.is_empty() or not Engine.is_editor_hint():
		return left
	watch()
	var tree := Engine.get_main_loop() as SceneTree
	var files := EditorInterface.get_resource_filesystem()
	await _idle(tree, files, Time.get_ticks_msec() + WAIT_MSEC)
	var still: Array[String] = []
	for path: String in left:
		if not loadable(path):
			still.append(path)
	return still


## Leaves [param scene] until the files it uses are imported: then it reloads if it is open, or
## opens when [param open]. Checked each time the editor's files change or an import ends.
static func later(scene: String, open: bool) -> void:
	_later[scene] = open or bool(_later.get(scene, false))
	var files := EditorInterface.get_resource_filesystem()
	if not files.filesystem_changed.is_connected(_land_later):
		files.filesystem_changed.connect(_land_later, CONNECT_DEFERRED)
		files.resources_reimported.connect(_land_later, CONNECT_DEFERRED)


## The scenes left for later ([method later]).
static func waiting() -> Array:
	return _later.keys()


static func _land_later(_paths := PackedStringArray()) -> void:
	var files := EditorInterface.get_resource_filesystem()
	if importing() or files.is_scanning():
		return
	var unsaved := EditorInterface.get_unsaved_scenes()
	for scene: String in _later.keys():
		if not FileAccess.file_exists(scene):
			_later.erase(scene)
			continue
		if not missing_dependencies(scene).is_empty():
			continue
		var open: bool = _later[scene]
		_later.erase(scene)
		if EditorInterface.get_open_scenes().has(scene):
			if not unsaved.has(scene):
				EditorInterface.reload_scene_from_path(scene)
		elif open:
			EditorInterface.open_scene_from_path(scene)
	if _later.is_empty() and files.filesystem_changed.is_connected(_land_later):
		files.filesystem_changed.disconnect(_land_later)
		files.resources_reimported.disconnect(_land_later)


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
## the editor then stops holding files a revert removed. Returns the scenes left to reload: all
## of them while the editor reimports.
static func reload_untouched(scenes: Array[String], touched: Array[String]) -> Array[String]:
	if importing():
		return scenes.duplicate()
	var unsaved := EditorInterface.get_unsaved_scenes()
	var left: Array[String] = []
	for path: String in scenes:
		var clean := not unsaved.has(path)
		for dependency: String in dependencies(path):
			if touched.has(dependency) or not _ready(dependency):
				clean = false
		if clean:
			EditorInterface.reload_scene_from_path(path)
		else:
			left.append(path)
	return left


## Reloads the open [param scenes] once their files are imported ([method await_loadable]).
## Returns a note for each one left as it is: a scene with unsaved edits (they would be lost), one
## that uses missing files, or every one while the editor still reimports. Those but the unsaved
## ones reload once their files are imported ([method later]).
static func reload_open(scenes: Array[String]) -> Array[String]:
	await await_loadable(scenes)
	var unsaved := EditorInterface.get_unsaved_scenes()
	var kept: Array[String] = []
	for path: String in scenes:
		if importing():
			kept.append(path.get_file() + " (still importing)")
			later(path, false)
		elif unsaved.has(path):
			kept.append(path.get_file() + " (unsaved edits)")
		elif not loadable(path):
			kept.append(path.get_file() + " (uses missing files)")
			later(path, false)
		else:
			EditorInterface.reload_scene_from_path(path)
	return kept


## Brings the editor up to date with the files a change wrote or a revert restored ([param
## paths], res://): once they are scanned and imported, the open scenes among them are reloaded
## and so are the open scripts and text files ([method reload_scripts]), so Godot never asks
## about them ("Files have been modified outside Godot") when the editor gets the focus back.
## Returns a note for each one left as it is ([method reload_open]); Godot still asks about
## those, so unsaved edits are never dropped.
static func land(paths: Array[String]) -> Array[String]:
	await scan(paths)
	var scenes: Array[String] = []
	for path: String in EditorInterface.get_open_scenes():
		if paths.has(path) and FileAccess.file_exists(path):
			scenes.append(path)
	var kept: Array[String] = await reload_open(scenes)
	kept.append_array(reload_scripts(paths))
	return kept


## After Accept, once the files are scanned: each changed scene ([param changes], the
## decision's) that is open reloads, and a created one, or any when no scene is open, opens, each
## once the files it uses are imported ([method await_loadable]). Returns the scenes left
## unloaded because they use missing files; those open or reload once the files are imported
## ([method later]).
static func open_accepted(changes: Array) -> Array[String]:
	var scenes: Array[String] = []
	var created: Array[String] = []
	for change: Dictionary in changes:
		var path := "res://" + str(change["path"])
		if path.get_extension() == "tscn" and FileAccess.file_exists(path):
			scenes.append(path)
			if change.get("change") == "created":
				created.append(path)
	var left := await await_loadable(scenes)
	var waiting: Array[String] = []
	for path: String in scenes:
		var open := EditorInterface.get_open_scenes().has(path)
		var wanted := open or created.has(path) or EditorInterface.get_edited_scene_root() == null
		if left.has(path):
			waiting.append(path.get_file())
			if wanted:
				later(path, not open)
		elif open:
			EditorInterface.reload_scene_from_path(path)
		elif wanted:
			EditorInterface.open_scene_from_path(path)
	return waiting


## Reloads the scripts and text files open in the script editor that changed on disk, without
## Godot's "modified outside Godot" question, even when the editor setting asks for it: these
## are files Joinery itself wrote ([param paths], res://; [param every] for a change whose files
## are not known, from an earlier session). When one of them has unsaved edits, nothing is
## reloaded here: Godot asks about it, as it does for any outside edit, and the note returned
## says so.
static func reload_scripts(paths: Array[String], every := false) -> Array[String]:
	if not Engine.is_editor_hint() or (paths.is_empty() and not every):
		return []
	var scripts := EditorInterface.get_script_editor()
	if scripts == null:
		return []
	var unsaved := scripts.get_unsaved_files()
	var kept: Array[String] = []
	for path: String in unsaved:
		if paths.has(path):
			kept.append(path.get_file() + " (unsaved edits)")
	if not kept.is_empty() or (every and not unsaved.is_empty()):
		return kept
	var settings := EditorInterface.get_editor_settings()
	var ask := settings.has_setting(AUTO_RELOAD) and not bool(settings.get_setting(AUTO_RELOAD))
	if ask:
		settings.set_setting(AUTO_RELOAD, true)
	# Reloads each open file whose time on disk changed; with nothing unsaved it never asks.
	scripts.reload_open_files()
	if ask:
		settings.set_setting(AUTO_RELOAD, false)
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
	return files.is_scanning() or importing()


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
	if importing():
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
		quiet = 0 if files.is_scanning() or importing() else quiet + 1
