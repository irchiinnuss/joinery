extends SceneTree
## The Assets list's groups (asset_groups.gd) in the real workspace, headless: categories are
## created, renamed, reordered and deleted; assets are moved in by menu and by drop and taken
## out; every group folds from its header with a count, remembered, and opens while a search
## matches it; design/asset_categories.toml round-trips, keeps a stable order and drops assets
## that are gone.

const WORKSPACE := preload("res://addons/joinery/tool_workspace.gd")
const CATEGORIES := preload("res://addons/joinery/asset_categories.gd")
const SECTION := preload("res://addons/joinery/asset_list_section.gd")
const FILE := "res://design/asset_categories.toml"

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _asset(path: String, kind: String) -> Dictionary:
	return {
		"path": path,
		"tool": "blender",
		"origin": "tool-created",
		"kind": kind,
		"parts": [],
		"part_of": "",
		"pending": "",
		"open_options": [],
		"previews": [],
	}


func _data() -> Dictionary:
	var tool := {"id": "blender", "name": "Blender", "enabled": true, "status": "ready"}
	var assets := [
		_asset("art/sources/push_mower.blend", "model"),
		_asset("art/sources/gas_can.blend", "model"),
		_asset("art/sources/glove_hands.blend", "model"),
		_asset("audio/sfx/mower_start.wav", "sound"),
		_asset("audio/sfx/mower_engine.wav", "sound"),
		_asset("art/textures/grime_mask.png", "image"),
	]
	return {"tools": [tool], "assets": assets, "issues": []}


## Section titles of the list, in order.
func _titles(workspace: WORKSPACE) -> Array[String]:
	var titles: Array[String] = []
	for key: String in workspace._assets.section_keys():
		titles.append(_section(workspace, key).title.text)
	return titles


func _section(workspace: WORKSPACE, key: String) -> SECTION:
	return workspace._assets.get_section(key)


func _row(workspace: WORKSPACE, path: String) -> int:
	return workspace._rows.find_custom(func(row: Dictionary) -> bool: return row["path"] == path)


func _run() -> void:
	root.size = Vector2i(1000, 700)
	if FileAccess.file_exists(FILE):
		DirAccess.remove_absolute(ProjectSettings.globalize_path(FILE))
	var workspace: WORKSPACE = WORKSPACE.new()
	root.add_child(workspace)
	workspace.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	workspace.set_process(false)
	workspace._apply_data(_data())
	var groups: Node = workspace._groups
	_check(_titles(workspace) == ["Models", "Sounds", "Images"], "Kinds: %s" % [_titles(workspace)])
	_check(not FileAccess.file_exists(FILE), "Nothing is written until the user makes a category")

	# Create from an asset's menu: the asset moves in and the new header is renamed in place.
	var mower := "art/sources/push_mower.blend"
	workspace._assets.item_menu_requested.emit(_row(workspace, mower), Vector2.ZERO)
	var menu: PopupMenu = groups._menu
	_check(menu.get_item_text(0) == "Move to category", "Menu leads with Move to category")
	_check(menu.is_item_disabled(0), "No category to move to yet")
	_check(menu.get_item_text(1) == "New category…", "New category…")
	_check(menu.item_count == 2, "No Remove while in no category")
	menu.hide()
	menu.id_pressed.emit(groups.NEW)
	await process_frame
	_check(_titles(workspace) == ["New category", "Models", "Sounds", "Images"], "Created first")
	var fresh := _section(workspace, "category:New category")
	_check(fresh.rename.visible and fresh.rename.has_focus(), "The new name is typed in place")
	fresh.rename.text = "Yard props"
	fresh.rename.text_submitted.emit(fresh.rename.text)
	await process_frame
	_check(_titles(workspace)[0] == "Yard props", "Renamed: %s" % [_titles(workspace)])
	_check(groups.model.category_of(mower) == "Yard props", "The mower moved in")

	# Move another by the submenu, one by drop, then take one out.
	var can := "art/sources/gas_can.blend"
	workspace._assets.item_menu_requested.emit(_row(workspace, can), Vector2.ZERO)
	_check(not menu.is_item_disabled(0), "Yard props is offered")
	menu.hide()
	groups._move_menu.index_pressed.emit(0)
	var engine := "audio/sfx/mower_engine.wav"
	groups.create()
	var made: SECTION = _section(workspace, "category:New category")
	made.rename.text = "Mower sounds"
	made.rename.text_submitted.emit(made.rename.text)
	await process_frame
	var empty := _section(workspace, "category:Mower sounds")
	_check(empty.empty.is_visible_in_tree(), "An empty category says Drag assets here")
	_check(empty.empty.text == "Drag assets here", empty.empty.text)
	var drag := {"type": SECTION.ROW_DRAG, "index": _row(workspace, engine)}
	drag["list"] = workspace._assets.get_instance_id()
	_check(workspace._assets._accepts(drag, "category:Mower sounds"), "A row drops on a header")
	_check(not workspace._assets._accepts(drag, "kind:sound"), "Not onto its own kind's group")
	_check(not workspace._assets._accepts(drag, "kind:model"), "Not onto another kind's group")
	workspace._assets.drop(drag, "category:Mower sounds")
	_check(groups.model.category_of(engine) == "Mower sounds", "Dropped in")
	_check(_titles(workspace) == ["Yard props", "Mower sounds", "Models", "Sounds", "Images"], "")
	var text := FileAccess.get_file_as_string(FILE)
	var expected := (
		CATEGORIES.HEADER
		+ '\n[[category]]\nname = "Yard props"\nassets = [\n'
		+ '    "res://art/sources/gas_can.blend",\n    "res://art/sources/push_mower.blend",\n]\n'
		+ '\n[[category]]\nname = "Mower sounds"\nassets = [\n'
		+ '    "res://audio/sfx/mower_engine.wav",\n]\n'
	)
	_check(text == expected, "The file:\n" + text)
	workspace._assets.item_menu_requested.emit(_row(workspace, can), Vector2.ZERO)
	_check(menu.get_item_text(menu.item_count - 1) == "Remove from category", "Remove offered")
	menu.hide()
	menu.id_pressed.emit(groups.REMOVE)
	_check(groups.model.category_of(can) == "", "Taken out")
	_check(_section(workspace, "kind:model").row_count() == 2, "Back with the models")

	# Reorder by header menu and by dragging a header; the first can't move up.
	workspace._assets.section_menu_requested.emit("category:Yard props", Vector2.ZERO)
	_check(menu.get_item_text(0) == "Rename…", "Header menu: Rename…")
	_check(menu.is_item_disabled(menu.get_item_index(groups.UP)), "First can't move up")
	menu.hide()
	menu.id_pressed.emit(groups.DOWN)
	_check(_titles(workspace).slice(0, 2) == ["Mower sounds", "Yard props"], "Moved down")
	var header := {"type": SECTION.SECTION_DRAG, "key": "category:Yard props"}
	header["list"] = workspace._assets.get_instance_id()
	workspace._assets.drop(header, "category:Mower sounds")
	_check(_titles(workspace).slice(0, 2) == ["Yard props", "Mower sounds"], "Dragged before")
	_check(not workspace._assets._accepts(header, "kind:model"), "Headers stay above the kinds")

	# Fold: count while folded, remembered by a new workspace; search opens matches.
	var sounds := _section(workspace, "kind:sound")
	sounds.header.pressed.emit()
	sounds = _section(workspace, "kind:sound")
	_check(not sounds.open and not sounds.body.visible, "Sounds folds")
	_check(sounds.count.visible and sounds.count.text == "1", "Count: " + sounds.count.text)
	workspace.queue_free()
	await process_frame
	workspace = WORKSPACE.new()
	root.add_child(workspace)
	await process_frame
	workspace.set_process(false)
	workspace._apply_data(_data())
	groups = workspace._groups
	_check(not _section(workspace, "kind:sound").open, "Folding is remembered")
	_check(_titles(workspace)[0] == "Yard props", "Categories read back from the file")
	workspace._search.text = "mower"
	workspace._filter()
	_check(_section(workspace, "kind:sound").open, "A search opens groups with matches")
	_check(_titles(workspace) == ["Yard props", "Mower sounds", "Sounds"], "Only matches")
	workspace._search.text = ""
	workspace._filter()
	_check(not _section(workspace, "kind:sound").open, "Folded again after the search")

	# Rename keeps the fold; delete puts assets back; a taken name is refused.
	_section(workspace, "category:Mower sounds").header.pressed.emit()
	_check(not groups.rename("Mower sounds", "Yard props"), "Names are unique")
	_check(groups.rename("Mower sounds", "Engine"), "Renamed")
	_check(not _section(workspace, "category:Engine").open, "The fold follows the rename")
	groups.delete("Engine")
	_check(groups.model.category_of(engine) == "", "Deleted: its asset goes back")
	_check(_section(workspace, "kind:sound").row_count() == 2, "Both sounds in Sounds")

	# Hand edits and pulls: read again; unknown keys, repeats and missing assets tolerated.
	var file := FileAccess.open(FILE, FileAccess.WRITE)
	file.store_string(
		(
			"[meta]\nversion = 3\n[[category]]\nname = 'Yard props' # mine\nassets = ["
			+ '"res://art/sources/glove_hands.blend", "res://art/gone.blend"]\ncolour = 4\n'
			+ '[[category]]\nname = "Yard props"\nassets = ["res://art/sources/push_mower.blend"]\n'
		)
	)
	file.close()
	groups.model._stamp = -1
	workspace.refresh()
	_check(_titles(workspace)[0] == "Yard props", "Read again")
	_check(_section(workspace, "category:Yard props").row_count() == 2, "Merged, gone skipped")
	groups.move(engine, "Yard props")
	text = FileAccess.get_file_as_string(FILE)
	_check(not text.contains("gone.blend"), "A missing asset is dropped when written")
	_check(not text.contains("meta"), "Only categories are written")
	var round := CATEGORIES.parse(CATEGORIES.serialize(groups.model.categories))
	_check(round == groups.model.categories, "Round trip")
	var odd := CATEGORIES.parse(CATEGORIES.serialize([{"name": 'Say "hi" \\', "assets": []}]))
	_check(odd[0]["name"] == 'Say "hi" \\', "Quotes survive")
	groups.delete("Yard props")
	_check(not FileAccess.file_exists(FILE), "No categories, no file")
	workspace.queue_free()
	await process_frame
	print("asset groups: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
