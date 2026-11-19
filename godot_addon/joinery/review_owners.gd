@tool
extends RefCounted
## The assets a change made or changed, as a person names them, with the tool that owns each:
## "engine sound" in SuperCollider, "door model" in Blender, "level scene" in Godot. The chat
## card's preview note says them before Accept (sidebar_preview_note.gd).

## Extension -> [what it is, the tool that owns it ("" when any of several may)].
const KINDS := {
	"blend": ["model", "Blender"],
	"glb": ["model", ""],
	"gltf": ["model", ""],
	"fbx": ["model", ""],
	"obj": ["model", ""],
	"kra": ["picture", "Krita"],
	"png": ["picture", ""],
	"jpg": ["picture", ""],
	"jpeg": ["picture", ""],
	"webp": ["picture", ""],
	"svg": ["picture", "Inkscape"],
	"mmg": ["material", "Material Maker"],
	"tres": ["material", "Godot"],
	"scd": ["sound", "SuperCollider"],
	"fxp": ["sound", "Surge XT"],
	"wav": ["sound", ""],
	"ogg": ["sound", ""],
	"mid": ["music", ""],
	"tscn": ["scene", "Godot"],
	"scn": ["scene", "Godot"],
	"gd": ["script", "Godot"],
}
## Words that already say what a thing is.
const NOUNS := ["model", "sound", "music", "song", "material", "scene", "script", "level"]


## Each asset in [param media] and [param changes] (both {path, …}) once: {path, name, kind,
## tool}, a native source first so its tool is known ("door.blend" and "door.glb" are one
## "door model" in Blender). Files that aren't assets (notes, settings) are left out.
static func owned(media: Array, changes: Array) -> Array:
	var found: Array = []
	var seen := {}
	for entry: Variant in changes + media:
		if not entry is Dictionary:
			continue
		var path := str((entry as Dictionary).get("path", "")).trim_prefix("res://")
		var spec: Array = KINDS.get(path.get_extension().to_lower(), [])
		if path.is_empty() or spec.is_empty():
			continue
		var key := "%s/%s" % [path.get_file().get_basename().to_lower(), spec[0]]
		if seen.has(key):
			var known: Dictionary = seen[key]
			if str(known["tool"]).is_empty() and not str(spec[1]).is_empty():
				known["tool"] = spec[1]
				known["path"] = path
			continue
		var item := {"path": path, "name": name_of(path, spec[0]), "kind": spec[0]}
		item["tool"] = spec[1]
		seen[key] = item
		found.append(item)
	return found


## "art/audio/engine_sound.scd" -> "engine sound"; "art/props/door.blend" -> "door model".
static func name_of(path: String, kind: String) -> String:
	var stem := path.get_file().get_basename()
	stem = RegEx.create_from_string("(?<=[a-z0-9])(?=[A-Z])").sub(stem, " ", true)
	var words := " ".join(stem.replace("_", " ").replace("-", " ").split(" ", false)).to_lower()
	for noun: String in NOUNS:
		if (" " + words + " ").contains(" " + noun + " "):
			return words
	return words + " " + kind


## The change card's headline: what changed, by name, and the tool that owns it ("Engine
## sound in SuperCollider", "Door model and 2 more in Blender", "Door script"). The card is
## the change, so it never says "Changed".
static func line(items: Array) -> String:
	if items.is_empty():
		return ""
	var first: Dictionary = items[0]
	var tool := str(first.get("tool", ""))
	# A scene or script is Godot's own: the editor the user is in goes without saying.
	if tool == "Godot":
		tool = ""
	var name := str(first.get("name", ""))
	var more := items.size() - 1
	var tail := " and %d more" % more if more > 0 else ""
	var place := " in " + tool if not tool.is_empty() else ""
	return name.substr(0, 1).to_upper() + name.substr(1) + tail + place
