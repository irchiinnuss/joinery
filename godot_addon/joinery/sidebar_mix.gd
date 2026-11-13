@tool
extends Node
## "/mix" in the sidebar: the mix changes Godot's Audio panel doesn't make, each sent to the
## chat host as one reviewed change (bridge/mixer_save.py writes the bus layout and the music
## track descriptions with joinery/mixer.py; nothing is written until the user accepts it).
## Godot's own Audio panel is the place for buses, faders and effects; Joinery has no second
## mixer (ADR 0048, amendment).
##
##   /mix buses                     add the game core's buses the project lacks
##   /mix duck voice|sfx|off        music dips while Voice or SFX plays (or never)
##   /mix <bus> <dB>|mute|unmute|solo|unsolo
##   /mix <track> <layer> <dB>|mute|unmute|solo|unsolo     one layer of res://music/<track>.tres
##
## Anything else ("/mix make the music quieter in combat") goes to Journeyman as a request.

const USAGE := (
	"Type /mix buses, /mix duck voice, /mix music -4, or /mix <track> <layer> -3."
	+ " Buses and effects are in Godot's Audio panel."
)
const NOT_CONNECTED := "Connect Journeyman to change the mix: it is a change you review."
## The game core's buses, in index order (joinery/mixer.py STANDARD_BUSES).
const STANDARD: Array[String] = ["Master", "Music", "SFX", "Ambience", "UI", "Voice"]
const DUCK := {"voice": "Voice", "sfx": "SFX", "off": ""}
const FLAGS := {
	"mute": ["mute", true],
	"unmute": ["mute", false],
	"solo": ["solo", true],
	"unsolo": ["solo", false],
}
const TRACKS := "res://music"

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): its bridge is the chat's.
var tasks: Object


static func attach(sidebar: Control, list: Object) -> Node:
	var mix: Node = load("res://addons/joinery/sidebar_mix.gd").new()
	mix.set("dock", sidebar)
	mix.set("tasks", list)
	(list as Node).add_child(mix)
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("mix_requested"):
		slash.connect("mix_requested", mix.run)
	return mix


func _init() -> void:
	name = "Mix"


## The mixer_save message for [param words], {} when they are not a mix command.
static func message(words: PackedStringArray) -> Dictionary:
	var lower: Array[String] = []
	for word: String in words:
		lower.append(word.to_lower())
	if lower.size() == 1 and lower[0] == "buses":
		var buses: Array = []
		for bus: String in STANDARD:
			buses.append({"name": bus})
		return {"bus_layout": {"buses": buses}}
	if lower.size() == 2 and lower[0] == "duck" and DUCK.has(lower[1]):
		return {"bus_layout": {"ducking": DUCK[lower[1]]}}
	if lower.size() == 2:
		var bus := bus_name(words[0])
		var change := _change(lower[1], "mute")
		if not bus.is_empty() and not change.is_empty():
			return {"bus_layout": {"buses": [change.merged({"name": bus})]}}
	if lower.size() == 3:
		var path := TRACKS.path_join(words[0].get_basename() + ".tres")
		var layer := _change(lower[2], "muted")
		if FileAccess.file_exists(path) and not layer.is_empty():
			return {"tracks": {path: {"layers": {words[1]: layer}}}}
	return {}


## [param word] as a bus of this project or of the game core, as it is spelled there; "".
static func bus_name(word: String) -> String:
	for index: int in AudioServer.bus_count:
		if AudioServer.get_bus_name(index).to_lower() == word.to_lower():
			return AudioServer.get_bus_name(index)
	for bus: String in STANDARD:
		if bus.to_lower() == word.to_lower():
			return bus
	return ""


## {volume_db} for a number, {mute or muted / solo} for a flag word, else {}.
static func _change(word: String, mute_key: String) -> Dictionary:
	var number := word.trim_suffix("db")
	if number.is_valid_float():
		return {"volume_db": clampf(number.to_float(), -80.0, 24.0)}
	if FLAGS.has(word):
		var flag: Array = FLAGS[word]
		return {mute_key if flag[0] == "mute" else "solo": flag[1]}
	return {}


## "/mix …": a mix command goes to the host as a change in Review; other words go to
## Journeyman as a request; nothing says how.
func run(words: String) -> void:
	var text := words.strip_edges()
	if text.is_empty():
		dock.call("show_status", USAGE, 6.0)
		return
	var line := message(text.split(" ", false))
	if line.is_empty():
		dock.call("fill_composer", "Change the game's audio mix: " + text)
		return
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == null or not bridge.has_method("send_mixer"):
		dock.call("show_status", NOT_CONNECTED, 4.0)
		return
	if not bridge.call("send_mixer", line):
		dock.call("show_status", NOT_CONNECTED, 4.0)
