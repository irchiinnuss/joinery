extends RefCounted
## The visual suite's Assets list groups: the user's categories above the kind groups (one of
## them empty), a folded group with its count, an asset's right-click menu, and a search that
## opens the groups it matches. Copied beside visual_editor_test.gd by run_visual_suite.py;
## [param suite] is that test, whose `_shot`, `_check`, `_frames`, `_plugin` and
## `_wait_workspace` these use. Leaves the Assets screen for the 3D one when done.


static func screens(suite: Variant) -> void:
	var plugin: EditorPlugin = suite._plugin
	var workspace: Control = plugin.get("_workspace")
	var rail: Control = plugin.get("_rail")
	workspace.call("select_tool", "")
	var groups: Node = workspace.get("_groups")
	var list: Control = workspace.get("_assets")
	for name: String in ["Doors", "Level kit", "Lighting"]:
		groups.get("model").call("create", name)
	for path: String in ["art/props/door.blend", "audio/sfx/door_close.wav"]:
		groups.get("model").call("assign", path, "Doors")
	groups.get("model").call("assign", "audio/sfx/door_creak.wav", "Doors")
	groups.call("move", "art/kit/room.blend", "Level kit")
	await suite._frames()
	var keys: PackedStringArray = list.call("section_keys")
	suite._check(
		(
			keys.slice(0, 3)
			== PackedStringArray(["category:Doors", "category:Level kit", "category:Lighting"])
		),
		"Categories first: %s" % keys
	)
	await suite._shot("assets-categories", [workspace, rail])
	var images: Variant = list.call("get_section", "kind:image")
	suite._check(images != null, "An Images group")
	if images != null:
		images.get("header").pressed.emit()
		await suite._shot("assets-folded", [workspace, rail])
	var rows: Array = workspace.get("_rows")
	var door := rows.find_custom(
		func(row: Dictionary) -> bool: return row["path"] == "art/props/door.blend"
	)
	var menu: PopupMenu = groups.get("_menu")
	list.emit_signal(
		"item_menu_requested", door, workspace.get_screen_position() + Vector2(80, 120)
	)
	await suite._frames()
	suite._check(menu.visible, "The asset's menu opens")
	await suite._shot("assets-menu", [], menu)
	menu.hide()
	var search: LineEdit = workspace.get("_search")
	search.text = "door"
	search.text_changed.emit(search.text)
	await suite._shot("assets-search", [workspace, rail])
	search.text = ""
	search.text_changed.emit("")
	if images != null:
		list.call("get_section", "kind:image").get("header").pressed.emit()
	for name: String in ["Doors", "Level kit", "Lighting"]:
		groups.call("delete", name)
	EditorInterface.set_main_screen_editor("3D")
	await suite._frames()
