@tool
extends RefCounted
## The user's own asset categories ("Yard props"), in their order, kept in
## design/asset_categories.toml so they travel with the project through git like the brief.
## Each asset (its source path) is in at most one. The file is small plain TOML that reads
## well in a diff: one [[category]] table per category, its assets sorted, paths as res://.
## It organises the project; it is not an asset, so it has no lockfile entry. Paths of assets
## that no longer exist are dropped quietly when the file is next written.

const PATH := "res://design/asset_categories.toml"
const HEADER := (
	"# The Assets view's categories, in order. Joinery keeps this file; editing it by hand\n"
	+ "# is fine. Each asset is listed by its source file, in one category at most.\n"
)
## A new category's name before the user types one.
const NEW_NAME := "New category"

var path := PATH
## [{name: String, assets: PackedStringArray}] in the user's order; asset paths have no res://.
var categories: Array[Dictionary] = []
var _stamp := -1


func _init(file := PATH) -> void:
	path = file


## Read the file again when it changed on disk (a pull, an edit by hand); true if it did.
func reload_if_changed() -> bool:
	var stamp := FileAccess.get_modified_time(path) if FileAccess.file_exists(path) else 0
	if stamp == _stamp:
		return false
	_stamp = stamp
	var text := FileAccess.get_file_as_string(path) if stamp != 0 else ""
	var read := parse(text)
	if read == categories:
		return false
	categories = read
	return true


## Write the categories, dropping assets not in [param known] (path → anything) unless it is
## empty. No categories left removes the file.
func save(known: Dictionary = {}) -> Error:
	if not known.is_empty():
		for category: Dictionary in categories:
			var kept := PackedStringArray()
			for asset: String in category["assets"]:
				if known.has(asset):
					kept.append(asset)
			category["assets"] = kept
	var absolute := ProjectSettings.globalize_path(path)
	var error := OK
	if categories.is_empty():
		if FileAccess.file_exists(path):
			error = DirAccess.remove_absolute(absolute)
	else:
		DirAccess.make_dir_recursive_absolute(absolute.get_base_dir())
		var file := FileAccess.open(path, FileAccess.WRITE)
		if file == null:
			return FileAccess.get_open_error()
		file.store_string(serialize(categories))
		file.close()
	_stamp = FileAccess.get_modified_time(path) if FileAccess.file_exists(path) else 0
	return error


func names() -> PackedStringArray:
	var result := PackedStringArray()
	for category: Dictionary in categories:
		result.append(str(category["name"]))
	return result


func find(name: String) -> int:
	return names().find(name)


## The category [param asset] is in, or "".
func category_of(asset: String) -> String:
	for category: Dictionary in categories:
		if (category["assets"] as PackedStringArray).has(asset):
			return str(category["name"])
	return ""


## Add a category at the end and return its name: [param name], or "New category 2" style
## when that is empty or taken.
func create(name := "") -> String:
	var wanted := clean(name)
	if wanted.is_empty():
		wanted = NEW_NAME
	var unique := wanted
	var number := 2
	while find(unique) >= 0:
		unique = "%s %d" % [wanted, number]
		number += 1
	categories.append({"name": unique, "assets": PackedStringArray()})
	return unique


## Rename [param old]; false when the new name is empty or another category has it.
func rename(old: String, name: String) -> bool:
	var index := find(old)
	var wanted := clean(name)
	if index < 0 or wanted.is_empty() or (wanted != old and find(wanted) >= 0):
		return false
	categories[index]["name"] = wanted
	return true


## Remove a category; its assets go back to their kind's group.
func delete(name: String) -> void:
	var index := find(name)
	if index >= 0:
		categories.remove_at(index)


## Move a category [param by] places (−1 up, 1 down); false at either end.
func shift(name: String, by: int) -> bool:
	var index := find(name)
	var target := index + by
	if index < 0 or target < 0 or target >= categories.size():
		return false
	var moved: Dictionary = categories[index]
	categories.remove_at(index)
	categories.insert(target, moved)
	return true


## Put [param name] just before [param before] (dragging one header onto another).
func place_before(name: String, before: String) -> bool:
	var index := find(name)
	if index < 0 or find(before) < 0 or name == before:
		return false
	var moved: Dictionary = categories[index]
	categories.remove_at(index)
	categories.insert(find(before), moved)
	return true


## Put [param asset] in [param name] (out of any other); "" takes it out of every category.
func assign(asset: String, name: String) -> void:
	for category: Dictionary in categories:
		var assets: PackedStringArray = category["assets"]
		var at := assets.find(asset)
		if at >= 0:
			assets.remove_at(at)
		if str(category["name"]) == name:
			assets.append(asset)
			assets.sort()
		category["assets"] = assets


## A name as typed, on one line, without surrounding spaces.
static func clean(name: String) -> String:
	var plain := name
	for code: int in range(1, 32):
		plain = plain.replace(String.chr(code), " ")
	return plain.strip_edges()


## The file's text: header, then each category in order with its assets sorted.
static func serialize(list: Array[Dictionary]) -> String:
	var lines := PackedStringArray([HEADER])
	for category: Dictionary in list:
		lines.append("[[category]]")
		lines.append("name = %s" % _quote(str(category["name"])))
		var assets := PackedStringArray(category["assets"])
		assets.sort()
		if assets.is_empty():
			lines.append("assets = []")
		else:
			lines.append("assets = [")
			for asset: String in assets:
				lines.append("    %s," % _quote("res://" + asset))
			lines.append("]")
		lines.append("")
	return "\n".join(lines)


## Categories from the file's [param text]. Tolerant: unknown keys and tables are skipped, a
## repeated name merges into the first, an asset already placed stays where it was first.
static func parse(text: String) -> Array[Dictionary]:
	var result: Array[Dictionary] = []
	var reader := _Reader.new(text)
	var current: Dictionary = {}
	var in_category := false
	var placed: Dictionary = {}
	while reader.skip_blank():
		if reader.peek() == "[":
			var table := reader.table()
			in_category = table == "[[category]]"
			current = {"name": "", "assets": PackedStringArray()} if in_category else {}
			if in_category:
				result.append(current)
			continue
		var key := reader.key()
		var value: Variant = reader.value()
		if not in_category:
			continue
		if key == "name" and value is String:
			current["name"] = clean(value)
		elif key == "assets" and value is Array:
			var assets: PackedStringArray = current["assets"]
			for item: Variant in value:
				if item is String:
					assets.append((item as String).strip_edges().trim_prefix("res://"))
			current["assets"] = assets
	var merged: Array[Dictionary] = []
	var by_name: Dictionary = {}
	for category: Dictionary in result:
		var name := str(category["name"])
		if name.is_empty():
			continue
		if not by_name.has(name):
			by_name[name] = {"name": name, "assets": PackedStringArray()}
			merged.append(by_name[name])
		var into: PackedStringArray = by_name[name]["assets"]
		for asset: String in category["assets"]:
			if not asset.is_empty() and not placed.has(asset):
				placed[asset] = true
				into.append(asset)
		into.sort()
		by_name[name]["assets"] = into
	return merged


static func _quote(text: String) -> String:
	var escaped := text.replace("\\", "\\\\").replace('"', '\\"')
	return '"%s"' % escaped


## A small reader for the TOML this file uses: tables, `key = value` with strings and arrays
## of strings; anything else is read past.
class _Reader:
	var text := ""
	var at := 0

	func _init(source: String) -> void:
		text = source

	func peek() -> String:
		return text[at] if at < text.length() else ""

	## Skip spaces, newlines and comments; false at the end.
	func skip_blank() -> bool:
		while at < text.length():
			var c := text[at]
			if c == "#":
				while at < text.length() and text[at] != "\n":
					at += 1
			elif c in [" ", "\t", "\r", "\n", ","]:
				at += 1
			else:
				return true
		return false

	## "[[category]]" or "[other]", spaces removed; the reader moves past the line.
	func table() -> String:
		var end := text.find("\n", at)
		end = text.length() if end < 0 else end
		var line := text.substr(at, end - at)
		at = end
		var hash := line.find("#")
		if hash >= 0:
			line = line.substr(0, hash)
		return line.replace(" ", "").replace("\t", "")

	func key() -> String:
		var end := text.find("=", at)
		var newline := text.find("\n", at)
		if end < 0 or (newline >= 0 and newline < end):
			at = text.length() if newline < 0 else newline
			return ""
		var name := text.substr(at, end - at).strip_edges().trim_prefix('"').trim_suffix('"')
		at = end + 1
		return name

	func value() -> Variant:
		while at < text.length() and text[at] in [" ", "\t"]:
			at += 1
		var c := peek()
		if c == '"' or c == "'":
			return _string(c)
		if c == "[":
			at += 1
			var items: Array = []
			while skip_blank():
				if peek() == "]":
					at += 1
					return items
				var item: Variant = value()
				if item == null:
					return items
				items.append(item)
			return items
		# A number, date or anything else: not used here.
		while at < text.length() and not (text[at] in ["\n", ",", "]"]):
			at += 1
		return null

	func _string(quote: String) -> String:
		at += 1
		var out := ""
		while at < text.length():
			var c := text[at]
			at += 1
			if c == quote:
				return out
			if c == "\\" and quote == '"' and at < text.length():
				var next := text[at]
				at += 1
				var escapes := {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
				out += str(escapes.get(next, next))
			elif c == "\n":
				return out
			else:
				out += c
		return out
