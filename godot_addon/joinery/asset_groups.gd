@tool
extends Node
## The Assets list's groups: the user's own categories first, in their order, then a group per
## kind (Models, Sounds, …) for every asset in no category. Every group folds from its header
## (remembered per project in the editor's project metadata); while searching, groups with
## matches stay open. Above the list, [member bar] has a New category button and, while there
## are no categories, a line saying how grouping works. An asset's ⋯ (or right-click, the Menu
## key, Shift+F10) moves it to a category, starts a new one or takes it out; a category's ⋯
## renames, moves or deletes it (F2 renames, Delete asks to delete). Deleting asks first, in
## place, and only forgets the category: its assets go back to their kind's group and no file
## is touched. Drag an asset onto a header, or a category's header onto another. Categories
## are saved in design/asset_categories.toml (asset_categories.gd) so they travel with the
## project.

signal changed

const CATEGORIES_SCRIPT := preload("asset_categories.gd")
const SECTION_SCRIPT := preload("asset_list_section.gd")
const WORDING := preload("asset_wording.gd")
const UI_COMPONENTS := preload("ui_components.gd")
const FOLDED_KEY := "asset_groups_folded"
const EMPTY_TEXT := "Drag assets here"
const HINT_TEXT := "Group assets: start a category, then drag assets onto it."
## Menu ids.
const NEW := 1
const REMOVE := 2
const RENAME := 10
const UP := 11
const DOWN := 12
const DELETE := 13

## Folded keys outside the editor (tests), as the editor's metadata would keep them.
static var _folded_here := PackedStringArray()

## The categories as last read or written.
var model: CATEGORIES_SCRIPT
## Above the list: [member hint], then [member new_button]. The workspace places it.
var bar: VBoxContainer
## How grouping works, shown while there are no categories.
var hint: Label
## Starts a category and types its name in place.
var new_button: Button
var _list: Control
var _kind_of: Callable
var _titles: Dictionary
## Section keys folded by the user ("kind:model", "category:Yard props").
var _folded := PackedStringArray()
## Folded only for the search being typed; forgotten when it is cleared.
var _search_folded := PackedStringArray()
var _searching := false
var _rows: Array[Dictionary] = []
var _known: Dictionary = {}
var _menu: PopupMenu
var _move_menu: PopupMenu
var _menu_row := -1
var _menu_key := ""


## Group [param list]'s rows; [param kind_of] gives an asset's kind, a key of [param titles]
## (kind → group title, in order).
func setup(
	list: Control, kind_of: Callable, titles: Dictionary, file := CATEGORIES_SCRIPT.PATH
) -> void:
	name = "AssetGroups"
	_list = list
	_kind_of = kind_of
	_titles = titles
	model = CATEGORIES_SCRIPT.new(file)
	model.reload_if_changed()
	_folded = _load_folded()
	list.set("drag_rows", true)
	list.set("row_menus", true)
	list.set("can_drop", _can_drop)
	list.connect("section_toggled", _on_toggled)
	list.connect("section_menu_requested", _on_section_menu)
	list.connect("item_menu_requested", _on_item_menu)
	list.connect("item_dropped", _on_item_dropped)
	list.connect("section_dropped", _on_section_dropped)
	list.connect("section_renamed", _on_renamed)
	list.connect("section_delete_requested", _on_delete_requested)
	_menu = PopupMenu.new()
	_menu.name = "AssetMenu"
	_menu.id_pressed.connect(_on_menu)
	_move_menu = PopupMenu.new()
	_move_menu.name = "MoveMenu"
	_move_menu.index_pressed.connect(_on_move)
	_menu.add_child(_move_menu)
	list.add_child(_menu)
	_build_bar()


func _build_bar() -> void:
	bar = VBoxContainer.new()
	bar.name = "GroupBar"
	bar.theme_type_variation = "JoineryTight"
	# The hint's words start where the group titles do; the quiet button's own padding puts
	# its words there too.
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	bar.add_child(inset)
	hint = UI_COMPONENTS.label(HINT_TEXT, "JoineryCaption", true)
	hint.name = "Hint"
	inset.add_child(hint)
	new_button = UI_COMPONENTS.quiet("New category")
	new_button.name = "NewCategory"
	new_button.tooltip_text = "Start a category and name it"
	new_button.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	new_button.pressed.connect(func() -> void: create())
	bar.add_child(new_button)


## Read the file again if it changed on disk; true when the groups need filling again.
func reload_if_changed() -> bool:
	return model.reload_if_changed()


## Fill the list with [param found] (the assets that pass the filters) in groups and return
## them in row order. [param searching]: a search is typed (groups with matches open);
## [param filtered]: some assets are hidden (empty groups are left out). [param known] is
## every asset by path, so the file drops ones that are gone the next time it is written.
func fill(
	found: Array[Dictionary], searching: bool, filtered: bool, known: Dictionary
) -> Array[Dictionary]:
	_known = known
	hint.get_parent().visible = model.categories.is_empty() and not filtered
	if not searching:
		_search_folded.clear()
	_searching = searching
	var placed: Dictionary = {}
	var groups: Array = []
	for category: Dictionary in model.categories:
		var members: Array[Dictionary] = []
		for asset: Dictionary in found:
			if (category["assets"] as PackedStringArray).has(str(asset["path"])):
				members.append(asset)
				placed[asset["path"]] = true
		if not members.is_empty() or not filtered:
			var title := str(category["name"])
			groups.append(["category:" + title, title, members])
	for kind: String in _titles:
		var members: Array[Dictionary] = []
		for asset: Dictionary in found:
			if not placed.has(asset["path"]) and _kind_of.call(asset) == kind:
				members.append(asset)
		if not members.is_empty():
			groups.append(["kind:" + kind, str(_titles[kind]), members])
	var labels: Dictionary = {}
	for asset: Dictionary in found:
		var label := WORDING.file_title(str(asset["path"]))
		labels[label] = int(labels.get(label, 0)) + 1
	_rows = []
	for group: Array in groups:
		var key := str(group[0])
		var mine := key.begins_with("category:")
		var open := not (_search_folded if searching else _folded).has(key)
		_list.call("add_section", group[1], key, open, EMPTY_TEXT if mine else "", mine)
		var members: Array[Dictionary] = group[2]
		members.sort_custom(_by_title)
		for asset: Dictionary in members:
			_add_row(asset, labels)
	return _rows


func _add_row(asset: Dictionary, labels: Dictionary) -> void:
	var path := str(asset["path"])
	var kind := str(_kind_of.call(asset))
	var label := WORDING.file_title(path)
	if int(labels.get(label, 0)) > 1:
		label += " · " + path.get_extension().to_lower()
	var index: int = _list.call("add_item", label, kind if kind != "other" else "")
	var waiting := not str(asset.get("pending", "")).is_empty()
	_list.call("set_item_tooltip", index, path + (" · " + WORDING.WAITING if waiting else ""))
	_list.call("set_item_status", index, "working" if waiting else "")
	_rows.append(asset)


static func _by_title(left: Dictionary, right: Dictionary) -> bool:
	var title := WORDING.file_title(str(left["path"]))
	return title.naturalnocasecmp_to(WORDING.file_title(str(right["path"]))) < 0


## Write the file and fill the list again.
func _commit() -> void:
	var error: Error = model.save(_known)
	if error != OK:
		push_warning("Couldn't save asset categories: %s" % error_string(error))
	changed.emit()


func _on_toggled(key: String, open: bool) -> void:
	var folded := _search_folded if _searching else _folded
	if open:
		while folded.has(key):
			folded.remove_at(folded.find(key))
	elif not folded.has(key):
		folded.append(key)
	if _searching:
		_search_folded = folded
	else:
		_folded = folded
		_save_folded(folded)


func _can_drop(data: Dictionary, key: String) -> bool:
	if data.get("type") == SECTION_SCRIPT.SECTION_DRAG:
		var from := str(data["key"])
		return from != key and from.begins_with("category:") and key.begins_with("category:")
	var index := int(data.get("index", -1))
	if data.get("type") != SECTION_SCRIPT.ROW_DRAG or index < 0 or index >= _rows.size():
		return false
	var path := str(_rows[index]["path"])
	var current := model.category_of(path)
	if key.begins_with("category:"):
		return key.trim_prefix("category:") != current
	# Onto its own kind's group: out of its category.
	return not current.is_empty() and key == "kind:" + str(_kind_of.call(_rows[index]))


func _on_item_dropped(index: int, key: String) -> void:
	var mine := key.begins_with("category:")
	move(str(_rows[index]["path"]), key.trim_prefix("category:") if mine else "")


func _on_section_dropped(key: String, target: String) -> void:
	if model.place_before(key.trim_prefix("category:"), target.trim_prefix("category:")):
		_commit()


## Put asset [param path] in category [param category] ("" takes it out of its category).
func move(path: String, category: String) -> void:
	model.assign(path, category)
	_commit()


## Start a category (with asset [param path] in it, if given) and type its name in place.
func create(path := "") -> String:
	var made := model.create()
	if not path.is_empty():
		model.assign(path, made)
	_commit()
	_list.call("begin_rename", "category:" + made)
	return made


func rename(old: String, name: String) -> bool:
	if not model.rename(old, name):
		return false
	_rekey("category:" + old, "category:" + CATEGORIES_SCRIPT.clean(name))
	_commit()
	return true


## Forget category [param name]; its assets go back to their kind's group. No file is touched.
func delete(name: String) -> void:
	model.delete(name)
	_rekey("category:" + name, "")
	_commit()


## Ask under category [param name]'s header whether to delete it, saying where its assets go.
func ask_delete(name: String) -> void:
	var key := "category:" + name
	var section: Variant = _list.call("get_section", key)
	if section == null:
		return
	var index := model.find(name)
	var assets := PackedStringArray()
	if index >= 0:
		assets = model.categories[index]["assets"]
	(section as SECTION_SCRIPT).ask(
		delete_question(name, assets), "Delete category", _confirm_delete.bind(name)
	)


## "Delete “Doors”? Its 3 assets go back to Models and Sounds. No files are deleted."
func delete_question(name: String, assets: PackedStringArray) -> String:
	var groups := PackedStringArray()
	var count := 0
	for path: String in assets:
		if not _known.is_empty() and not _known.has(path):
			continue
		count += 1
		var kind := str(_kind_of.call(_known[path])) if _known.has(path) else "other"
		var title := str(_titles.get(kind, _titles.get("other", "Other files")))
		if not groups.has(title):
			groups.append(title)
	var text := "Delete “%s”? " % name
	if count == 0:
		return text + "It has no assets. No files are deleted."
	var where := groups[0]
	if groups.size() > 1:
		where = ", ".join(groups.slice(0, groups.size() - 1)) + " and " + groups[-1]
	var many := "Its asset goes" if count == 1 else "Its %d assets go" % count
	return text + "%s back to %s. No files are deleted." % [many, where]


func _confirm_delete(name: String) -> void:
	var keys: PackedStringArray = _list.call("section_keys")
	var at := keys.find("category:" + name)
	delete(name)
	keys = _list.call("section_keys")
	if not keys.is_empty():
		var next: Variant = _list.call("get_section", keys[clampi(at, 0, keys.size() - 1)])
		(next as SECTION_SCRIPT).header.grab_focus()


func _on_delete_requested(key: String) -> void:
	if key.begins_with("category:"):
		ask_delete(key.trim_prefix("category:"))


func shift(name: String, by: int) -> void:
	if model.shift(name, by):
		_commit()


func _rekey(old: String, new: String) -> void:
	var at := _folded.find(old)
	if at < 0:
		return
	_folded.remove_at(at)
	if not new.is_empty():
		_folded.append(new)
	_save_folded(_folded)


func _on_renamed(key: String, title: String) -> void:
	var old := key.trim_prefix("category:")
	if not title.is_empty() and title != old:
		rename(old, title)


func _on_item_menu(index: int, at: Vector2) -> void:
	_menu_row = index
	_menu_key = ""
	_menu.clear()
	_move_menu.clear()
	var current := model.category_of(str(_rows[index]["path"]))
	for category: String in model.names():
		if category != current:
			_move_menu.add_item(category)
	_menu.add_submenu_node_item("Move to category", _move_menu)
	_menu.set_item_disabled(0, _move_menu.item_count == 0)
	_menu.add_item("New category…", NEW)
	if not current.is_empty():
		_menu.add_item("Remove from category", REMOVE)
	_popup(at)


func _on_section_menu(key: String, at: Vector2) -> void:
	_menu_row = -1
	_menu_key = key
	_menu.clear()
	if key.begins_with("category:"):
		var index := model.find(key.trim_prefix("category:"))
		_menu.add_item("Rename…", RENAME)
		_menu.add_item("Move up", UP)
		_menu.set_item_disabled(_menu.item_count - 1, index <= 0)
		_menu.add_item("Move down", DOWN)
		_menu.set_item_disabled(_menu.item_count - 1, index >= model.categories.size() - 1)
		_menu.add_item("Delete category…", DELETE)
		_menu.add_separator()
	_menu.add_item("New category…", NEW)
	_popup(at)


func _popup(at: Vector2) -> void:
	_menu.reset_size()
	_menu.position = Vector2i(at)
	_menu.popup()


func _on_menu(id: int) -> void:
	var path := str(_rows[_menu_row]["path"]) if _menu_row >= 0 and _menu_row < _rows.size() else ""
	var category := _menu_key.trim_prefix("category:")
	match id:
		NEW:
			create(path)
		REMOVE:
			move(path, "")
		RENAME:
			_list.call("begin_rename", _menu_key)
		UP:
			shift(category, -1)
		DOWN:
			shift(category, 1)
		DELETE:
			ask_delete(category)


func _on_move(index: int) -> void:
	if _menu_row >= 0 and _menu_row < _rows.size():
		move(str(_rows[_menu_row]["path"]), _move_menu.get_item_text(index))


func _load_folded() -> PackedStringArray:
	if Engine.is_editor_hint() and Engine.has_singleton("EditorInterface"):
		var saved: Variant = EditorInterface.get_editor_settings().get_project_metadata(
			"joinery", FOLDED_KEY, PackedStringArray()
		)
		if saved is PackedStringArray or saved is Array:
			return PackedStringArray(saved)
		return PackedStringArray()
	return _folded_here.duplicate()


static func _save_folded(folded: PackedStringArray) -> void:
	if Engine.is_editor_hint() and Engine.has_singleton("EditorInterface"):
		EditorInterface.get_editor_settings().set_project_metadata("joinery", FOLDED_KEY, folded)
	else:
		_folded_here = folded.duplicate()
