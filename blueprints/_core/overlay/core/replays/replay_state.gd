extends RefCounted
## The state a replay checks: what the player experiences, not the whole scene tree. A
## change that adds a node to the level leaves a replay alone; one that moves the player
## elsewhere, changes their health, anything the game saves (goals, pickups, doors), the
## scene they are in or the level time makes it diverge.
##
## The state holds the scene, whether the game is paused, the level time, each player's
## place (and health, when the player has one), the game core's saveable state as plain
## values, and the scene's node count (for reading only, never compared).
## `differences` compares two states with tolerance: places within POSITION_3D metres
## (POSITION_2D pixels in 2D), times within TIME seconds, numbers within NUMBER. A replay
## on another build (another engine, platform or CPU) than the one that recorded it is
## judged LOOSER times more loosely, since physics differs a little between builds.
## The hash covers the same parts, rounded, so the same build gives the same hash.

## Tolerances on the build that recorded it; another build gets LOOSER times these.
const POSITION_3D := 0.25
const POSITION_2D := 4.0
const TIME := 0.1
const NUMBER := 0.001
const LOOSER := 4.0
## Saved values named in the differences at most.
const MAX_SAVED := 12


## {hash, state} for the tree right now.
static func capture(tree: SceneTree) -> Dictionary:
	var scene: Node = tree.current_scene
	var state: Dictionary = {
		"scene": scene.scene_file_path if scene != null else "",
		"paused": tree.paused,
	}
	if scene != null:
		state["nodes"] = _count(scene)
	var game: Node = tree.root.get_node_or_null(^"Game")
	if game != null:
		state["level_time"] = snappedf(float(game.call(&"level_time")), 0.001)
		var places: Array[String] = []
		var players: Array[Dictionary] = []
		for player: Node in game.call(&"players"):
			places.append(_place(player))
			var one: Dictionary = {"at": plain(player.get(&"global_position"))}
			if &"health" in player:
				one["health"] = plain(player.get(&"health"))
			players.append(one)
		state["players"] = places
		state["player"] = players
		state["saveable"] = plain(game.call(&"capture_state"))
	var hashed: Array = [
		state["scene"],
		state["paused"],
		_rounded(plain(state.get("player", []))),
		_rounded(state.get("saveable", {})),
	]
	return {"hash": JSON.stringify(hashed).sha256_text().left(16), "state": state}


## What differs between a `recorded` and a `replayed` state, as "key: recorded ->
## replayed", within the tolerances (`loose`: replayed on another build). Empty when the
## player's experience is the same.
static func differences(
	recorded: Dictionary, replayed: Dictionary, loose: bool = false
) -> Array[String]:
	var scale: float = LOOSER if loose else 1.0
	var found: Array[String] = []
	for key: String in ["scene", "paused"]:
		var before: Variant = recorded.get(key)
		var after: Variant = replayed.get(key)
		if (recorded.has(key) or replayed.has(key)) and str(before) != str(after):
			found.append("%s: %s -> %s" % [key, before, after])
	if recorded.has("level_time") and replayed.has("level_time"):
		var then: float = float(recorded["level_time"])
		var now: float = float(replayed["level_time"])
		if absf(then - now) > TIME * scale:
			found.append("level_time: %s -> %s" % [then, now])
	found.append_array(_player_differences(recorded, replayed, scale))
	found.append_array(_saved_differences(recorded, replayed, scale))
	return found


static func _player_differences(
	recorded: Dictionary, replayed: Dictionary, scale: float
) -> Array[String]:
	var found: Array[String] = []
	var places_before: Array = _players(recorded)
	var places_after: Array = _players(replayed)
	if places_before.size() != places_after.size():
		found.append("players: %d -> %d" % [places_before.size(), places_after.size()])
		return found
	for index: int in places_before.size():
		var a: Array = places_before[index]
		var b: Array = places_after[index]
		var limit: float = (POSITION_2D if a.size() == 2 else POSITION_3D) * scale
		if a.size() != b.size() or _distance(a, b) > limit:
			found.append("players: %s -> %s" % [_text(a), _text(b)])
	var people_before: Array = recorded.get("player", [])
	var people_after: Array = replayed.get("player", [])
	for index: int in mini(people_before.size(), people_after.size()):
		var one: Variant = people_before[index]
		var other: Variant = people_after[index]
		if not (one is Dictionary and other is Dictionary):
			continue
		var health: Variant = (one as Dictionary).get("health")
		var later: Variant = (other as Dictionary).get("health")
		if (one as Dictionary).has("health") and not _same(health, later, scale):
			found.append("health: %s -> %s" % [health, later])
	return found


static func _saved_differences(
	recorded: Dictionary, replayed: Dictionary, scale: float
) -> Array[String]:
	var found: Array[String] = []
	var before: Variant = recorded.get("saveable")
	var after: Variant = replayed.get("saveable")
	if not (before is Dictionary and after is Dictionary):
		return found
	var saved_before: Dictionary = before
	var saved_after: Dictionary = after
	# A saveable node the change added is new to the level, not to the player.
	for key: Variant in saved_before.keys():
		if found.size() >= MAX_SAVED:
			break
		if not saved_after.has(key):
			found.append("saved %s: gone" % key)
		elif not _same(saved_before[key], saved_after[key], scale):
			found.append("saved %s: changed" % key)
	return found


## JSON-plain values: vectors as number lists, dictionaries with text keys.
static func plain(value: Variant) -> Variant:
	if value is Vector3 or value is Vector3i:
		var v: Vector3 = Vector3(value)
		return [v.x, v.y, v.z]
	if value is Vector2 or value is Vector2i:
		var w: Vector2 = Vector2(value)
		return [w.x, w.y]
	if value is Dictionary:
		var out: Dictionary = {}
		for key: Variant in (value as Dictionary).keys():
			out[str(key)] = plain((value as Dictionary)[key])
		return out
	if value is Array:
		var items: Array = []
		for item: Variant in value:
			items.append(plain(item))
		return items
	if value == null or value is bool or value is int or value is float or value is String:
		return value
	if value is StringName or value is NodePath:
		return str(value)
	return var_to_str(value)


## Numbers rounded for the hash, so it reads the same when the values do.
static func _rounded(value: Variant) -> Variant:
	if value is float:
		return snappedf(value, 0.01)
	if value is Dictionary:
		var out: Dictionary = {}
		for key: Variant in (value as Dictionary).keys():
			out[key] = _rounded((value as Dictionary)[key])
		return out
	if value is Array:
		var items: Array = []
		for item: Variant in value:
			items.append(_rounded(item))
		return items
	return value


## Equal within the tolerance: numbers within NUMBER (relative past 1), the rest alike.
static func _same(a: Variant, b: Variant, scale: float) -> bool:
	if (a is float or a is int) and (b is float or b is int):
		var x: float = float(a)
		var y: float = float(b)
		return absf(x - y) <= NUMBER * scale * maxf(1.0, maxf(absf(x), absf(y)))
	if a is Array and b is Array:
		var left: Array = a
		var right: Array = b
		if left.size() != right.size():
			return false
		for index: int in left.size():
			if not _same(left[index], right[index], scale):
				return false
		return true
	if a is Dictionary and b is Dictionary:
		var first: Dictionary = a
		var second: Dictionary = b
		if first.size() != second.size():
			return false
		for key: Variant in first.keys():
			if not second.has(key) or not _same(first[key], second[key], scale):
				return false
		return true
	return str(a) == str(b)


## Each player's place as a number list: from "player" or, in an older recording, from
## the "players" text ("(1.00, 2.00, 3.00)").
static func _players(state: Dictionary) -> Array:
	var places: Array = []
	var people: Array = state.get("player", [])
	if not people.is_empty():
		for one: Variant in people:
			var at: Variant = (one as Dictionary).get("at", []) if one is Dictionary else []
			places.append(at if at is Array else [])
		return places
	for text: Variant in state.get("players", []):
		var numbers: Array = []
		for part: String in str(text).trim_prefix("(").trim_suffix(")").split(",", false):
			numbers.append(float(part.strip_edges()))
		places.append(numbers)
	return places


static func _distance(a: Array, b: Array) -> float:
	var total: float = 0.0
	for index: int in mini(a.size(), b.size()):
		total += pow(float(a[index]) - float(b[index]), 2.0)
	return sqrt(total)


static func _text(numbers: Array) -> String:
	var parts: PackedStringArray = PackedStringArray()
	for value: Variant in numbers:
		parts.append("%.2f" % float(value))
	return "(%s)" % ", ".join(parts)


static func _count(node: Node) -> int:
	var total: int = 1
	for child: Node in node.get_children():
		total += _count(child)
	return total


static func _place(player: Node) -> String:
	var at: Variant = player.get(&"global_position")
	if at is Vector3:
		var p: Vector3 = at
		return "(%.2f, %.2f, %.2f)" % [p.x, p.y, p.z]
	if at is Vector2:
		var q: Vector2 = at
		return "(%.2f, %.2f)" % [q.x, q.y]
	return "?"
