@tool
extends RefCounted
## Finding a scene by name, with Godot's own Quick Open dialog rather than a second browser.
##
## The rail's Scenes icon and a bare "/open" show Godot's Quick Open Scene picker: every scene
## in the project, filtered as you type, the ones picked before first (Godot keeps that list,
## shared with File > Quick Open Scene, Ctrl+Shift+O). "/open <name>" opens the scene that
## matches the name, file name or path when only one does, and otherwise shows the picker
## with the name typed in. A name that matches no scene but one script opens it in the script
## editor; one other file is selected in FileSystem. Works in Godot's classic view and on stock
## Godot too, where only the rail is missing.

const SCENES: Array[StringName] = [&"PackedScene"]
const ANY: Array[StringName] = [&"Resource"]
const SCENE_EXTENSIONS := ["tscn", "scn"]
const SCRIPT_EXTENSIONS := ["gd", "cs"]
## The editor setting that holds Godot's Quick Open Scene shortcut.
const SHORTCUT := "editor/quick_open_scene"

## What opens things: EditorInterface; tests put a stand-in here.
var editor: Object
## The project's files as res:// paths; empty: read from Godot's file system each time.
var paths := PackedStringArray()
## The words the picker last opened with (for tests and the status line).
var last_filter := ""


func _init() -> void:
	if in_editor():
		editor = Engine.get_singleton(&"EditorInterface")


## True inside the editor, where EditorInterface is there to ask.
static func in_editor() -> bool:
	return Engine.is_editor_hint() and Engine.has_singleton(&"EditorInterface")


## Show Godot's Quick Open Scene picker, with [param filter] typed in.
func pick(filter := "", types: Array[StringName] = SCENES) -> void:
	last_filter = filter
	if editor == null:
		return
	editor.call("popup_quick_open", _picked, types)
	if not filter.is_empty():
		_type_in(filter)


## "/open [words]": open the one scene [param words] names, or the picker to choose from.
func open(words: String) -> void:
	var query := words.strip_edges()
	if editor == null:
		return
	if query.is_empty():
		pick()
		return
	var files := Array(_files())
	var scenes := best(query, files.filter(func(path: String) -> bool: return is_scene(path)))
	if scenes.size() == 1:
		editor.call("open_scene_from_path", scenes[0])
		return
	if scenes.is_empty():
		var others := best(
			query, files.filter(func(path: String) -> bool: return not is_scene(path))
		)
		if others.size() == 1:
			open_file(others[0])
			return
		if not others.is_empty():
			pick(query, ANY)
			return
	pick(query)


## Open [param path]: a scene in the editor, a script in the script editor, anything else
## selected in FileSystem.
func open_file(path: String) -> void:
	if editor == null:
		return
	if is_scene(path):
		editor.call("open_scene_from_path", path)
	elif SCRIPT_EXTENSIONS.has(path.get_extension().to_lower()):
		editor.call("edit_script", load(path))
	else:
		editor.call("select_file", path)
		var panels := _panels()
		if panels != null:
			panels.call("show_panel", "FileSystem")


## True while Godot's Quick Open dialog shows.
func picker_open() -> bool:
	var dialog := dialog_node()
	return dialog != null and dialog.visible


## Godot's Quick Open dialog, once the editor made one.
func dialog_node() -> Window:
	var base := _base()
	if base == null:
		return null
	var found := base.get_tree().root.find_children("*", "EditorQuickOpenDialog", true, false)
	return found[0] as Window if not found.is_empty() else null


## Godot's Quick Open Scene shortcut as text ("Ctrl+Shift+O", "Shift+Command+O"), or "".
func shortcut_text() -> String:
	var settings := EditorInterface.get_editor_settings() if in_editor() else null
	if settings == null or not settings.has_shortcut(SHORTCUT):
		return ""
	var shortcut := settings.get_shortcut(SHORTCUT)
	return shortcut.get_as_text() if shortcut != null and shortcut.has_valid_event() else ""


static func is_scene(path: String) -> bool:
	return SCENE_EXTENSIONS.has(path.get_extension().to_lower())


## The [param paths] that match [param query] best: an exact path or file name, then the same
## name ("main menu" finds main_menu.tscn), a name that starts with it, a name or path that
## holds it, and last its letters in order. Only the best kind of match counts.
static func best(query: String, paths: Array) -> PackedStringArray:
	var wanted := query.strip_edges().to_lower().trim_prefix("res://")
	var bare := _plain(wanted.get_basename() if _known(wanted) else wanted)
	var tiers: Array[PackedStringArray] = []
	for index: int in 5:
		tiers.append(PackedStringArray())
	for path: String in paths:
		var tier := _tier(path.trim_prefix("res://").to_lower(), wanted, bare)
		if tier >= 0:
			tiers[tier].append(path)
	for tier: PackedStringArray in tiers:
		if not tier.is_empty():
			return tier
	return PackedStringArray()


static func _tier(relative: String, wanted: String, bare: String) -> int:
	var file := relative.get_file()
	var stem := _plain(file.get_basename())
	if relative == wanted or file == wanted or relative.get_basename() == wanted:
		return 0
	if wanted.contains("/") and ("/" + relative.get_basename()).ends_with("/" + wanted):
		return 0
	if bare.is_empty():
		return -1
	if stem == bare:
		return 1
	if stem.begins_with(bare):
		return 2
	if stem.contains(bare) or relative.contains(wanted):
		return 3
	return 4 if _in_order(bare, _plain(relative)) else -1


## Lower case without spaces, underscores, hyphens or dots, so "Main Menu" is "mainmenu".
static func _plain(text: String) -> String:
	var out := text.to_lower()
	for mark: String in [" ", "_", "-", "."]:
		out = out.replace(mark, "")
	return out


static func _known(text: String) -> bool:
	var extension := text.get_extension()
	return not extension.is_empty() and not extension.contains("/")


## True when every letter of [param letters] appears in [param text], in order.
static func _in_order(letters: String, text: String) -> bool:
	var at := 0
	for letter: String in letters:
		at = text.find(letter, at)
		if at < 0:
			return false
		at += 1
	return true


func _picked(path: String) -> void:
	if not path.is_empty():
		open_file(path)


## Type [param filter] into the open picker's search box, so its list narrows at once.
func _type_in(filter: String) -> void:
	var dialog := dialog_node()
	if dialog == null:
		return
	var boxes := dialog.find_children("*", "LineEdit", true, false)
	if boxes.is_empty():
		return
	var box := boxes[0] as LineEdit
	box.text = filter
	box.caret_column = filter.length()
	box.text_changed.emit(filter)


func _files() -> PackedStringArray:
	if not paths.is_empty():
		return paths
	var found := PackedStringArray()
	if not in_editor():
		return found
	var system := EditorInterface.get_resource_filesystem()
	if system != null and system.get_filesystem() != null:
		_walk(system.get_filesystem(), found)
	return found


static func _walk(folder: EditorFileSystemDirectory, found: PackedStringArray) -> void:
	for index: int in folder.get_file_count():
		found.append(folder.get_file_path(index))
	for index: int in folder.get_subdir_count():
		_walk(folder.get_subdir(index), found)


func _base() -> Control:
	return EditorInterface.get_base_control() if in_editor() else null


## Joinery's panels (godot_panels.gd), in Joinery's layout; null in the classic view.
func _panels() -> Node:
	var base := _base()
	if base == null:
		return null
	return base.get_tree().root.find_child("JoineryGodotPanels", true, false)
