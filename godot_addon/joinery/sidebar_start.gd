@tool
extends RefCounted
## What the empty chat offers, read from what is selected or open (sidebar_empty.gd): the
## thing's name, what it is and where ("Door", "MeshInstance3D in visual.tscn"), its icon, and
## three requests that fit its kind. A mesh offers "Make it taller", "Change its material" and
## "Add a sound to it"; nothing selected offers scene-level requests; no scene, starting ones.

## Node classes (and their parents) -> the requests offered for them. The first match in this
## order wins, so a light is a light before it is a Node3D.
const BY_CLASS := [
	["Light3D", ["Make it warmer", "Make it flicker gently", "Soften its shadows"]],
	["Light2D", ["Make it warmer", "Make it flicker gently", "Make it reach further"]],
	["Camera3D", ["Frame the player closer", "Add a gentle shake", "Lower the angle"]],
	["Camera2D", ["Frame the player closer", "Add a gentle shake", "Follow more smoothly"]],
	["CharacterBody3D", ["Make it jump higher", "Make it move faster", "Add footstep sounds"]],
	["CharacterBody2D", ["Make it jump higher", "Make it move faster", "Add footstep sounds"]],
	["AudioStreamPlayer", ["Make it quieter", "Make it sound warmer", "Add a variation"]],
	["AudioStreamPlayer2D", ["Make it quieter", "Make it sound warmer", "Add a variation"]],
	["AudioStreamPlayer3D", ["Make it quieter", "Make it sound warmer", "Add a variation"]],
	["Sprite2D", ["Repaint it warmer", "Add more detail", "Match it to the look"]],
	["Control", ["Make it bigger", "Match it to the look", "Animate it in"]],
	["MeshInstance3D", ["Make it taller", "Change its material", "Add a sound to it"]],
	["CSGShape3D", ["Make it taller", "Change its material", "Add a sound to it"]],
	["Node3D", ["Make it taller", "Change its material", "Add a sound to it"]],
	["Node2D", ["Make it bigger", "Repaint it warmer", "Add a sound to it"]],
]
## Target kinds (target_chip.gd) -> requests, when no class says more.
const BY_KIND := {
	"Object": ["Make it taller", "Change its material", "Add a sound to it"],
	"Audio": ["Make it quieter", "Make it sound warmer", "Add a variation"],
	"Image": ["Repaint it warmer", "Add more detail", "Match it to the look"],
	"Material": ["Make it look worn", "Make it darker", "Match it to the look"],
	"Scene": ["Add ambient sound", "Make the lighting warmer", "Check it against the look"],
}
## Nothing selected in an open scene.
const SCENE := ["Add ambient sound", "Make the lighting warmer", "Check it against the look"]
## No scene open.
const NOTHING := ["Make a new level", "Add a player character", "Add background music"]
## Target kinds -> the icon drawn when there is no class icon or thumbnail.
const KIND_ICONS := {
	"Object": "model",
	"Audio": "audio",
	"Image": "image",
	"Material": "material",
	"Scene": "scene",
}


## {name, line, actions, icon, class, resource} for [param targets] (the message targets, the
## first one leads) and [param scene] (the open scene's file name, "" when none).
static func describe(targets: Array, scene: String) -> Dictionary:
	if targets.is_empty() or not targets[0] is Dictionary:
		if scene.is_empty():
			return {
				"name": "",
				"line": "Select something, then say what to change.",
				"actions": NOTHING,
				"icon": "",
				"class": "",
				"resource": "",
			}
		return {
			"name": scene,
			"line": "Scene · nothing selected",
			"actions": SCENE,
			"icon": "scene",
			"class": "PackedScene",
			"resource": "",
		}
	var target: Dictionary = targets[0]
	var node_class := str(target.get("node_type", ""))
	var kind := str(target.get("kind", "Object"))
	var name := str(target.get("label", ""))
	var where := str(target.get("scene_file", ""))
	if where.is_empty():
		where = scene
	var what := node_class if not node_class.is_empty() else _kind_word(target)
	var line := what if where.is_empty() or where == name else "%s in %s" % [what, where]
	if targets.size() > 1:
		name = "%s and %d more" % [name, targets.size() - 1]
	return {
		"name": name,
		"line": line,
		"actions": actions_for(node_class, kind),
		"icon": str(KIND_ICONS.get(kind, "model")),
		"class": node_class,
		"resource": str(target.get("resource", "")).split("#")[0],
	}


## The three requests for a node of [param node_class] (or a target of [param kind]).
static func actions_for(node_class: String, kind: String) -> Array:
	if not node_class.is_empty():
		for pair: Array in BY_CLASS:
			if node_class == pair[0] or ClassDB.is_parent_class(node_class, pair[0]):
				return pair[1]
	return BY_KIND.get(kind, BY_KIND["Object"])


## "Model", "Sound", "Picture", "Material" or "Scene" for a file target.
static func _kind_word(target: Dictionary) -> String:
	var words := {
		"Object": "Model",
		"Audio": "Sound",
		"Image": "Picture",
		"Material": "Material",
		"Scene": "Scene",
	}
	var file := str(target.get("resource", "")).split("#")[0].get_file()
	var word := str(words.get(str(target.get("kind", "")), "File"))
	return word if file.is_empty() else "%s · %s" % [word, file]
