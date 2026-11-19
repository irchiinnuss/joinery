extends RefCounted
## The visual suite's Assets list groups: the list before any category (New category and the
## hint, at the browser's narrowest and widened), the user's categories above the kind groups
## (one of them empty), a row's ⋯ with keyboard focus, a lit header under a drag, a category's
## delete question in place (narrow and wide), a folded group with its count, an asset's menu,
## and a search that opens the groups it matches. Copied beside visual_editor_test.gd by
## run_visual_suite.py; [param suite] is that test, whose `_shot`, `_check`, `_frames`,
## `_plugin` and `_wait_workspace` these use. Leaves the Assets screen for the 3D one when done.


static func screens(suite: Variant) -> void:
	var plugin: EditorPlugin = suite._plugin
	var workspace: Control = plugin.get("_workspace")
	var rail: Control = plugin.get("_rail")
	workspace.call("select_tool", "")
	var groups: Node = workspace.get("_groups")
	var list: Control = workspace.get("_assets")
	await _widths(suite, "assets-start", workspace, rail)
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
	var rows: Array = workspace.get("_rows")
	var door := rows.find_custom(
		func(row: Dictionary) -> bool: return row["path"] == "art/props/door.blend"
	)
	var door_row: Button = list.get("_rows")[door]
	door_row.grab_focus()
	await suite._shot("assets-more", [workspace, rail])
	var more: Button = door_row.get("more")
	suite._check(more != null and more.self_modulate.a > 0.9, "The focused row shows its ⋯")
	var drag := {"type": "joinery_asset_row", "index": door, "list": list.get_instance_id()}
	suite._check(list.call("_hover_drop", drag, "category:Level kit"), "Door lands on Level kit")
	# The pointer is elsewhere in a capture: hold the light the drag would keep on.
	var kit: Node = list.call("get_section", "category:Level kit")
	kit.set_process(false)
	suite._check(kit.get("drop_lit"), "Level kit is lit")
	await suite._shot("assets-drop", [workspace, rail])
	list.call("_put_lights_out")
	groups.call("ask_delete", "Doors")
	await _widths(suite, "assets-confirm", workspace, rail)
	var doors: Variant = list.call("get_section", "category:Doors")
	var question: Control = doors.get("question")
	suite._check(question != null and question.is_visible_in_tree(), "Delete asks in place")
	if question != null:
		(question.find_child("Cancel", true, false) as Button).pressed.emit()
	var images: Variant = list.call("get_section", "kind:image")
	suite._check(images != null, "An Images group")
	if images != null:
		images.get("header").pressed.emit()
		await suite._shot("assets-folded", [workspace, rail])
	rows = workspace.get("_rows")
	door = rows.find_custom(
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


## Shoot [param name] with the browser at its narrowest, then widened by 12em as "-wide".
static func _widths(suite: Variant, name: String, workspace: Control, rail: Control) -> void:
	var browser: Control = workspace.get("_browser")
	var before := browser.custom_minimum_size.x
	await suite._shot(name, [workspace, rail])
	browser.custom_minimum_size.x = (
		before + workspace.get_theme_font_size("font_size", "Label") * 12
	)
	await suite._shot(name + "-wide", [workspace, rail])
	browser.custom_minimum_size.x = before
