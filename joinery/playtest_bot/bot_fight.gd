extends RefCounted
## Fighting and finishing for the playtest bot (--fight): a combat game can't be proved
## finishable by a bot that never shoots back. With this mode on the bot turns the view to
## an enemy it can see, fires the game's attack action with the gun in its hands, switches
## guns when one runs dry, backs off and finds health when hurt, picks up ammo when short,
## and otherwise follows the level's goals: the places named with --reach first (stops on the
## way, never the end), each key before the door it opens, then the exit, along the coverage
## grid (bot_cover.gd).
##
## It plays through the input map only (move, look, attack, jump, interact, weapon_next), so
## a recorded run replays, and every choice comes from its own generator seeded from the
## run's seed. Enemies are the nodes in group `enemies` or `enemy` that can be hurt
## (`take_damage`, or a `health`) and aren't dead (`dead`, `is_alive()`, `health`). Goals
## are the blockout's markers: `key`, `door` (its key in `metadata/key`), `exit`/`goal`,
## and `pickup` (health, armour and ammo by name). 3D games only: in 2D it stays off.

## Actions that fire the held weapon, the first the game has.
const ATTACK_ACTIONS: Array[StringName] = [&"attack", &"fire", &"shoot", &"primary_fire"]
const ENEMY_GROUPS: Array[StringName] = [&"enemies", &"enemy"]
## Words in a pickup's name that say what it gives.
const HEALTH_WORDS: Array[String] = ["health", "medkit", "salve", "heal", "potion", "firstaid"]
const AMMO_WORDS: Array[String] = [
	"ammo", "shell", "rivet", "bullet", "round", "clip", "magazine", "arrow", "cell", "tube"
]
## Metres: how far an enemy is seen and fought, and the distances it is fought between.
const SIGHT := 40.0
const NEAR := 4.0
const FAR := 11.0
## Share of full health below which the bot backs off and looks for health.
const LOW_HEALTH := 0.35
const WANT_HEALTH := 0.6
## Look speed: the share of a full stick press per radian off target (closed loop each frame).
const LOOK_GAIN := 4.0
## Metres from a goal that count as there, and from a door that count as through it.
const AT_GOAL := 2.0
const STUCK_FRAMES := 75
## Metres: how far ahead a move looks for the floor, and the drop it won't walk off.
const EDGE_LOOK := 1.2
const DROP := 1.2
const SKIP_FRAMES := 900
const LOG_LIMIT := 60
const NO_MOVES: Array[StringName] = []
const TRAIL_EVERY := 300
const TRAIL_LIMIT := 120

var tree: SceneTree
var drive: RefCounted
var cover: RefCounted
## "auto" (on once the game shows enemies and has an attack action), "on" or "off".
var mode: String = "auto"
var active: bool = false
var why: String = ""
var kills: int = 0
var deaths: int = 0
var shots: int = 0
var switches: int = 0
var keys: Array[String] = []
var doors: Array[String] = []
var pickups: Array[String] = []
var reached_exit: bool = false
var exit_t: Variant = null
var finished: bool = false
var finished_t: Variant = null
## Goals in the order the bot took them up: {t, goal, kind}.
var goal_log: Array[Dictionary] = []
## Where the player died: {t, at, near} (the enemy closest then).
var death_log: Array[Dictionary] = []
## Where the player was every TRAIL_EVERY frames: [t, x, y, z, goal or "fighting <enemy>"].
var trail: Array = []

var _rng := RandomNumberGenerator.new()
var _attack: StringName = &""
var _player: Node = null
var _frame: int = 0
var _enemies: Array[Node] = []
var _alive: Dictionary = {}
var _seen_enemies: Dictionary = {}
var _killed: Dictionary = {}
## Doors the player went through in this level (a new level shuts them all again).
var _opened: Array[String] = []
var _level_id: int = 0
var _target_enemy: Node = null
var _goal: Node = null
var _goal_kind: String = ""
var _goal_since: int = 0
var _skip: Dictionary = {}
var _closest: Dictionary = {}
var _moves: Dictionary = {}
var _looks: Dictionary = {}
var _steering: bool = false
var _last_at := Vector3.ZERO
var _last_check: int = 0
var _stuck: int = 0
var _sidestep: StringName = &""
var _sidestep_until: int = 0
var _strafe: StringName = &"move_left"
var _strafe_until: int = 0
var _switch_at: int = 0
var _interact_at: int = 0
var _jump_at: int = 0
## The way ahead goes down on purpose: walking off an edge is allowed this frame.
var _drop_ok: bool = false
var _reprobe: Dictionary = {}
var _last_health: float = -1.0


func _init(scene_tree: SceneTree, bot_drive: RefCounted, seed_value: int, fight: String) -> void:
	tree = scene_tree
	drive = bot_drive
	mode = fight
	_rng.seed = hash([seed_value, "fight"])
	_find_attack()
	var events := tree.root.get_node_or_null(^"GameEvents")
	if events != null and events.has_signal(&"level_completed"):
		events.connect(&"level_completed", _on_level_completed.unbind(1))
	if mode == "off":
		why = "off (--no-fight)"
	elif _attack.is_empty():
		why = "the game has no attack action"


## The game's attack action (looked for again until found: a game may add it at run time).
func _find_attack() -> void:
	for action: StringName in ATTACK_ACTIONS:
		if InputMap.has_action(action):
			_attack = action
			if why == "the game has no attack action":
				why = ""
			return


## One physics frame. True when this frame's movement is the fight's (the drive then leaves
## the keys alone), false when the bot should explore as usual.
func step(frame: int, player: Node) -> bool:
	_frame = frame
	if player != _player:
		_follow(player)
	if mode == "off" or not player is Node3D or not is_instance_valid(player):
		return _stop()
	if frame % 10 == 0 or _level_changed():
		_scan()
	if not active:
		if mode == "on" or (not _attack.is_empty() and not _enemies.is_empty()):
			active = true
			why = "on" if mode == "on" else "the game has enemies and an attack action"
		else:
			return _stop()
	_watch_player()
	if frame % TRAIL_EVERY == 0 and trail.size() < TRAIL_LIMIT:
		var at := _round(_at())
		var heading := String(_goal.name) if _goal != null and is_instance_valid(_goal) else ""
		var fighting := _target_enemy
		if fighting != null and is_instance_valid(fighting) and _is_alive(fighting):
			heading = "fighting " + String(fighting.name)
		trail.append([_t(), at[0], at[1], at[2], heading])
	_take_doors()
	_switch_if_dry()
	var enemy := _pick_enemy()
	_target_enemy = enemy
	if enemy != null:
		_fight(enemy)
		_steering = true
		return true
	_release_attack()
	var goal := _pick_goal()
	if goal == null:
		_release_looks()
		return _stop()
	_go_to(goal)
	_steering = true
	return true


## What the fight did, for bot.json's "fight".
func summary() -> Dictionary:
	return {
		"mode": mode,
		"active": active,
		"why": why,
		"attack": String(_attack),
		"kills": kills,
		"enemies": _seen_enemies.size(),
		"deaths": deaths,
		"shots": shots,
		"weapon_switches": switches,
		"keys": keys,
		"doors": doors,
		"pickups": pickups,
		"reached_exit": reached_exit,
		"exit_t": exit_t,
		"finished": finished,
		"finished_t": finished_t,
		"goal": _describe(_goal) if _goal != null and is_instance_valid(_goal) else "",
		"goals": goal_log,
		"death_places": death_log,
		"trail": trail,
		"way": _way(),
		"obstacle": "" if finished or reached_exit else _obstacle(),
	}


# --- The player -------------------------------------------------------------------------------


func _follow(player: Node) -> void:
	_release_all()
	_player = player
	_goal = null
	_last_health = -1.0
	if player != null and player.has_signal(&"died"):
		player.connect(&"died", _on_died)


func _on_died() -> void:
	deaths += 1
	if death_log.size() < LOG_LIMIT and _player is Node3D:
		var near := ""
		var best := INF
		for enemy: Node in _enemies:
			if is_instance_valid(enemy) and _distance(enemy) < best:
				best = _distance(enemy)
				near = String(enemy.name)
		death_log.append({"t": _t(), "at": _round(_at()), "near": near})
	_goal = null


func _on_level_completed() -> void:
	if not finished:
		finished = true
		finished_t = _t()
	if _goal != null and is_instance_valid(_goal) and _goal_kind == "exit":
		_mark_exit()


## A player without a `died` signal: a fall to 0 health counts as a death.
func _watch_player() -> void:
	if _player.has_signal(&"died") or not "health" in _player:
		return
	var health := float(_player.get(&"health"))
	if _last_health > 0.0 and health <= 0.0:
		_on_died()
	_last_health = health


func _health_share() -> float:
	if not "health" in _player:
		return 1.0
	var most := float(_player.get(&"max_health")) if "max_health" in _player else 100.0
	return clampf(float(_player.get(&"health")) / maxf(most, 1.0), 0.0, 1.0)


## The gun in the hands is empty: try the next one (weapon_next), now and then.
func _switch_if_dry() -> void:
	if not _dry() or _frame < _switch_at:
		return
	_switch_at = _frame + 30
	for action: StringName in [&"weapon_next", &"next_weapon", &"weapon_2"]:
		if InputMap.has_action(action):
			drive.call(&"tap", action, _frame, 3)
			switches += 1
			return


func _dry() -> bool:
	if _player.has_method(&"current_ammo_type") and _player.has_method(&"ammo_count"):
		var kind: Variant = _player.call(&"current_ammo_type")
		return int(_player.call(&"ammo_count", kind)) <= 0
	if "ammo" in _player and (_player.get(&"ammo") is int or _player.get(&"ammo") is float):
		return float(_player.get(&"ammo")) <= 0.0
	return false


## Every gun the player holds is empty, or nearly.
func _short_of_ammo() -> bool:
	if not _player.has_method(&"ammo_count"):
		return _dry()
	var total := 0
	var held: Variant = _player.get(&"ammo") if "ammo" in _player else null
	if held is Dictionary:
		for kind: Variant in held:
			total += int(_player.call(&"ammo_count", kind))
		return total <= 4
	return _dry()


# --- Enemies ----------------------------------------------------------------------------------


func _level_changed() -> bool:
	var level := _level()
	return (level.get_instance_id() if level != null else 0) != _level_id


func _level() -> Node:
	var game := tree.root.get_node_or_null(^"Game")
	if game != null and game.has_method(&"current_level"):
		var level: Variant = game.call(&"current_level")
		if level is Node:
			return level
	return tree.current_scene


## Look for enemies again; one that was alive and now isn't (or is gone) in the same level
## is a kill. A new level (a reload, a load) starts the count over without kills.
func _scan() -> void:
	var level := _level()
	var level_id := level.get_instance_id() if level != null else 0
	if level_id != _level_id:
		_level_id = level_id
		_alive.clear()
		_opened.clear()
		_goal = null
	_enemies.clear()
	var seen := {}
	for group: StringName in ENEMY_GROUPS:
		for node: Node in tree.get_nodes_in_group(group):
			if seen.has(node) or not _is_enemy(node):
				continue
			seen[node] = true
			# By path: a level loaded again has the same enemies under the same names.
			_seen_enemies[str(node.get_path())] = true
			if _is_alive(node):
				_enemies.append(node)
	if _attack.is_empty():
		_find_attack()
	var now := {}
	for node: Node in _enemies:
		now[str(node.get_path())] = (node as Node3D).global_position
	for path: String in _alive:
		if not now.has(path):
			_killed[path] = true
			# It may have stood in a doorway: the way through it is open now.
			if cover != null:
				cover.call(&"reprobe", _player, _alive[path], 3.0)
	kills = _killed.size()
	_alive = now


static func _is_enemy(node: Node) -> bool:
	return node is Node3D and (node.has_method(&"take_damage") or "health" in node)


static func _is_alive(node: Node) -> bool:
	if not is_instance_valid(node) or not node.is_inside_tree():
		return false
	if "dead" in node and bool(node.get(&"dead")):
		return false
	if node.has_method(&"is_alive"):
		return bool(node.call(&"is_alive"))
	if "health" in node:
		return float(node.get(&"health")) > 0.0
	return (node as Node3D).is_visible_in_tree()


## The nearest live enemy in sight (a clear line from the eyes), or null.
func _pick_enemy() -> Node:
	var best: Node = null
	var best_distance := SIGHT
	for enemy: Node in _enemies:
		if not _is_alive(enemy):
			continue
		var distance := _distance(enemy)
		# Keep shooting at the one already aimed at unless another is much closer.
		if enemy == _target_enemy:
			distance *= 0.7
		if distance < best_distance and _in_sight(enemy):
			best_distance = distance
			best = enemy
	return best


func _aim_point(enemy: Node) -> Vector3:
	if enemy.has_method(&"core_position"):
		var core: Variant = enemy.call(&"core_position")
		if core is Vector3:
			return core
	for child: Node in enemy.get_children():
		if child is CollisionShape3D:
			return (child as Node3D).global_position
	return (enemy as Node3D).global_position + Vector3.UP


func _in_sight(enemy: Node) -> bool:
	var eyes := _eyes()
	var point := _aim_point(enemy)
	if eyes.distance_to(point) > SIGHT:
		return false
	var space := (_player as Node3D).get_world_3d().direct_space_state
	var query := PhysicsRayQueryParameters3D.create(eyes, point)
	if _player is CollisionObject3D:
		query.exclude = [(_player as CollisionObject3D).get_rid()]
	var hit := space.intersect_ray(query)
	if hit.is_empty():
		return true
	var collider := hit["collider"] as Node
	return collider != null and (collider == enemy or enemy.is_ancestor_of(collider))


## Face the enemy, fire when on target, and keep a fighting distance (or back off, hurt).
func _fight(enemy: Node) -> void:
	_drop_ok = false
	var error := _look_at(_aim_point(enemy))
	var distance := _distance(enemy)
	var tolerance := maxf(atan2(0.45, maxf(distance, 0.5)), 0.035)
	if absf(error.x) < tolerance * 1.5 and absf(error.y) < tolerance * 1.5:
		_fire()
	else:
		_release_attack()
	var wanted: Array[StringName] = []
	var hurt := _health_share() < LOW_HEALTH
	if hurt or distance < NEAR:
		wanted.append(&"move_back")
	elif distance > FAR:
		var aim: Vector3 = _route((enemy as Node3D).global_position)
		wanted.append_array(_moves_toward(aim))
	if _frame >= _strafe_until:
		_strafe = &"move_left" if _rng.randf() < 0.5 else &"move_right"
		_strafe_until = _frame + _rng.randi_range(40, 100)
	if not &"move_left" in wanted and not &"move_right" in wanted:
		wanted.append(_strafe)
	_check_stuck(wanted)
	_hold_moves(wanted)


func _fire() -> void:
	if _attack.is_empty():
		return
	# Held most frames, let go now and then: a gun that fires per press fires again too.
	if _frame % 6 < 4:
		if not Input.is_action_pressed(_attack):
			drive.call(&"_hold", _attack)
			shots += 1
	else:
		_release_attack()


func _release_attack() -> void:
	if not _attack.is_empty() and Input.is_action_pressed(_attack):
		drive.call(&"_release", _attack)


# --- Goals ------------------------------------------------------------------------------------


## What to head for: health when hurt, ammo when short, else the next step to the exit (a
## key the bot can get to, then its door, then the exit or the --reach places).
func _pick_goal() -> Node:
	if _goal != null and (not is_instance_valid(_goal) or not _live_goal(_goal)):
		_goal = null
	var kind := ""
	var pick: Node = null
	if _health_share() < WANT_HEALTH:
		pick = _nearest_pickup(HEALTH_WORDS)
		kind = "health"
	if pick == null and _short_of_ammo():
		pick = _nearest_pickup(AMMO_WORDS)
		kind = "ammo"
	if pick == null:
		var planned := _plan()
		pick = planned[0]
		kind = planned[1]
	if pick != _goal:
		_goal = pick
		_goal_kind = kind
		_goal_since = _frame
		_stuck = 0
		if pick != null and goal_log.size() < LOG_LIMIT:
			goal_log.append({"t": _t(), "goal": _describe(pick), "kind": kind})
	return _goal


## [node, kind]: the next step toward the end of the level. A --reach place not reached yet
## is a stop on the way: the bot goes there first when it can (kind "reach"), takes keys and
## doors when it can't yet, and heads for the level's exit only once no place is left to reach
## (or nothing else is). The exit stays the exit: reaching a place never counts as finishing.
func _plan() -> Array:
	var held := _held_keys()
	var exits := _exits()
	var place := _reach_place()
	if place != null and _reachable(place):
		return [place, "exit" if place in exits else "reach"]
	if place == null:
		for node: Node in _by_distance(exits):
			if _reachable(node):
				return [node, "exit"]
	# Keys not taken yet: the nearest the grid joins to the player first.
	var open_keys: Array[Node] = []
	for node: Node in tree.get_nodes_in_group(&"key"):
		if _live_goal(node) and not String(node.name) in held:
			open_keys.append(node)
	for node: Node in _by_distance(open_keys):
		if _reachable(node):
			return [node, "key"]
	# Doors whose key is held and the bot hasn't been through yet.
	var closed: Array[Node] = []
	for node: Node in tree.get_nodes_in_group(&"door"):
		var key := str(node.get_meta(&"key", ""))
		if not key.is_empty() and key in held and not String(node.name) in _opened and _free(node):
			closed.append(node)
	if not closed.is_empty():
		return [_by_distance(closed)[0], "door"]
	if place != null and _free(place):
		# The grid joins nothing on the way to it: straight there.
		return [place, "exit" if place in exits else "reach"]
	return _fallback(exits, open_keys)


## [node, kind] when the grid joins the player to nothing on the way: fallen below the exit
## (a pit), a hazard the grid joins sends the player back to the last checkpoint, where the
## way on is; else the nearest key, then the exit, straight.
func _fallback(finals: Array[Node], open_keys: Array[Node]) -> Array:
	var fallen := false
	for node: Node in finals:
		fallen = fallen or (node as Node3D).global_position.y > _at().y + 1.5
	var hazards: Array[Node] = []
	for node: Node in tree.get_nodes_in_group(&"hazard"):
		# Straight there when the grid doesn't join it: nothing else is left down here.
		if fallen and node is Node3D and node.is_inside_tree() and _free(node):
			hazards.append(node)
	if not hazards.is_empty():
		return [_by_distance(hazards)[0], "back to a checkpoint"]
	for node: Node in _by_distance(open_keys) + _by_distance(finals):
		if _free(node):
			return [node, "key" if node in open_keys else "exit"]
	return [null, ""]


## The level's exits: the `exit` group's markers, else the `goal` group's.
func _exits() -> Array[Node]:
	var found: Array[Node] = []
	for group: StringName in [&"exit", &"goal"]:
		for node: Node in tree.get_nodes_in_group(group):
			if node is Node3D and node.is_inside_tree():
				found.append(node)
		if not found.is_empty():
			return found
	return found


## The nearest --reach place the player hasn't got to yet (the drive keeps the score), or null.
func _reach_place() -> Node:
	if not drive.has_method(&"next_reach_target"):
		return null
	var place: Variant = drive.call(&"next_reach_target")
	return place as Node if place is Node3D else null


## Keys the player holds in this level: the level's `keys` (a blockout level's), else the
## key markers taken (gone or no longer watching). Every key ever held goes in the report.
func _held_keys() -> Array[String]:
	var held: Array[String] = []
	var said := false
	var node: Node = _level()
	while node != null and not said:
		if "keys" in node and node.get(&"keys") is Array:
			said = true
			for name: Variant in node.get(&"keys"):
				held.append(str(name))
		node = node.get_parent()
	if not said:
		for key: Node in tree.get_nodes_in_group(&"key"):
			if not _live_goal(key):
				held.append(String(key.name))
	for name: String in held:
		if not name in keys:
			keys.append(name)
	return held


func _nearest_pickup(words: Array[String]) -> Node:
	var found: Array[Node] = []
	for node: Node in tree.get_nodes_in_group(&"pickup"):
		if node.is_in_group(&"key") or not _live_goal(node) or not _free(node):
			continue
		var name := String(node.name).to_lower()
		for word: String in words:
			if name.contains(word):
				found.append(node)
				break
	for node: Node in _by_distance(found):
		if _reachable(node) and _distance(node) < 60.0:
			return node
	return null


## A goal still to be had: in the tree, shown, and (an area) still watching for the player.
static func _live_goal(node: Node) -> bool:
	if not node.is_inside_tree() or not node is Node3D:
		return false
	if not (node as Node3D).is_visible_in_tree() and not node.is_in_group(&"door"):
		return false
	return not (node is Area3D and not (node as Area3D).monitoring)


func _free(node: Node) -> bool:
	return int(_skip.get(node.get_instance_id(), 0)) <= _frame


func _reachable(node: Node) -> bool:
	if not _free(node):
		return false
	if cover == null:
		return true
	return bool(cover.call(&"reachable", _at(), (node as Node3D).global_position))


func _by_distance(nodes: Array[Node]) -> Array[Node]:
	var sorted := nodes.duplicate()
	sorted.sort_custom(func(a: Node, b: Node) -> bool: return _distance(a) < _distance(b))
	return sorted


## Walk to the goal; once there, use it (an exit switch) or count it (a key, a door).
func _go_to(goal: Node) -> void:
	var where := (goal as Node3D).global_position
	if _goal_kind == "reach":
		# A room or zone: its middle, where the drive counts it reached.
		where = drive.call(&"_aim", goal)
	var distance := _flat(where)
	var key := String(goal.name)
	if not _closest.has(key) or distance < float(_closest[key]):
		_closest[key] = snappedf(distance, 0.01)
	if _goal_kind == "exit" and distance <= AT_GOAL * 1.5:
		_mark_exit()
		_use(goal)
		_drop_ok = false
		_hold_moves(_moves_toward(where) if distance > 1.0 else NO_MOVES)
		return
	if _goal_kind == "door" and distance <= AT_GOAL:
		# Walk on through: the door opens for a player holding its key.
		_open_door(goal)
	var direct := distance < 3.5 or _goal_kind == "door" and distance < 6.0
	var aim := where if direct else _route(where)
	_look_at(aim + Vector3.UP * 1.5, true)
	var wanted := _moves_toward(aim)
	# The way goes down (off a ledge, into a pit on purpose): the drop is meant.
	_drop_ok = aim.y < _at().y - 1.0
	if aim.y > _at().y + 0.35 and _flat(aim) < 3.0 and _frame >= _jump_at:
		_jump_at = _frame + 40
		_jump(10)
	_check_stuck(wanted)
	_hold_moves(wanted)


func _mark_exit() -> void:
	if not reached_exit:
		reached_exit = true
		exit_t = _t()


## Aim at what can be used at the goal and press interact now and then.
func _use(goal: Node) -> void:
	var thing := _usable(goal)
	var point := (thing as Node3D).global_position if thing is Node3D else _at()
	if thing is Node3D:
		point += Vector3.UP * 0.6
	_look_at(point)
	if _frame >= _interact_at:
		_interact_at = _frame + 20
		drive.call(&"tap", &"interact", _frame, 3)


func _usable(goal: Node) -> Node:
	if goal.is_in_group(&"interactable"):
		return goal
	for node: Node in goal.find_children("*", "Node3D", true, false):
		if node.is_in_group(&"interactable"):
			return node
	return goal


func _open_door(door: Node) -> void:
	var name := String(door.name)
	if name in _opened:
		return
	_opened.append(name)
	if not name in doors:
		doors.append(name)
	# The door slides away: probe its doorway again once it has.
	_reprobe[_frame + 60] = (door as Node3D).global_position


## Doors opened by anything with an `is_open` (a door prop under the marker) count too, and
## the grid around a door that opened is probed again once it has finished moving.
func _take_doors() -> void:
	for when: int in _reprobe.keys():
		if _frame >= when:
			if cover != null:
				cover.call(&"reprobe", _player, _reprobe[when], 5.0)
			_reprobe.erase(when)
	if _frame % 30 != 0:
		return
	for door: Node in tree.get_nodes_in_group(&"door"):
		if String(door.name) in _opened:
			continue
		for child: Node in door.get_children():
			if "is_open" in child and bool(child.get(&"is_open")):
				_open_door(door)
				break
	for node: Node in tree.get_nodes_in_group(&"pickup"):
		if node is Area3D and not (node as Area3D).monitoring:
			var name := String(node.name)
			if not node.is_in_group(&"key") and not name in pickups and pickups.size() < LOG_LIMIT:
				pickups.append(name)


# --- Moving and looking -----------------------------------------------------------------------


func _route(where: Vector3) -> Vector3:
	if cover == null or not bool(cover.get(&"built")):
		return where
	return cover.call(&"route", _at(), where)


## The move actions that walk toward [param point], camera-relative.
func _moves_toward(point: Vector3) -> Array[StringName]:
	var wanted: Array[StringName] = []
	var direction: Vector2 = drive.call(&"_toward_point", point)
	if direction.x > 0.35:
		wanted.append(&"move_right")
	elif direction.x < -0.35:
		wanted.append(&"move_left")
	if direction.y > 0.35:
		wanted.append(&"move_forward")
	elif direction.y < -0.35:
		wanted.append(&"move_back")
	return wanted


## No headway while walking: jump and sidestep; a goal that keeps the bot stuck is put aside.
func _check_stuck(wanted: Array[StringName]) -> void:
	if _frame >= _sidestep_until:
		_sidestep = &""
	elif not _sidestep.is_empty():
		wanted.clear()
		wanted.append(_sidestep)
		wanted.append(&"move_back")
	if _frame - _last_check < STUCK_FRAMES:
		return
	var moved := _flat(_last_at)
	_last_at = _at()
	_last_check = _frame
	if wanted.is_empty() or moved > 0.8:
		_stuck = 0
		return
	_stuck += 1
	_jump(12)
	_sidestep = &"move_left" if _rng.randf() < 0.5 else &"move_right"
	_sidestep_until = _frame + _rng.randi_range(20, 50)
	if _stuck >= 4 and _goal != null and is_instance_valid(_goal):
		_skip[_goal.get_instance_id()] = _frame + SKIP_FRAMES
		if goal_log.size() < LOG_LIMIT:
			goal_log.append(
				{"t": _t(), "goal": _describe(_goal), "kind": "stuck", "at": _round(_at())}
			)
		_goal = null
		_stuck = 0


func _hold_moves(asked: Array[StringName]) -> void:
	var wanted := asked if _drop_ok else _safe(asked)
	for action: StringName in [&"move_forward", &"move_back", &"move_left", &"move_right"]:
		var want := action in wanted
		if want and not Input.is_action_pressed(action):
			drive.call(&"_hold", action)
		elif not want and Input.is_action_pressed(action):
			drive.call(&"_release", action)
	_moves.clear()
	for action: StringName in wanted:
		_moves[action] = true


## Jumps, unless a jump would carry the player over an edge (a catwalk's end).
func _jump(frames: int) -> void:
	if _drop_ok or _safe([&"move_forward", &"move_back", &"move_left", &"move_right"]).size() == 4:
		drive.call(&"tap", &"jump", _frame, frames)


## The moves in [param wanted] that don't walk off an edge: a floor a little way that
## direction no more than DROP below the feet (a strafe in a fight, a corner on a catwalk).
func _safe(wanted: Array[StringName]) -> Array[StringName]:
	var camera := tree.root.get_viewport().get_camera_3d()
	if camera == null or not camera.is_inside_tree() or wanted.is_empty():
		return wanted
	var forward := Vector3(-camera.global_basis.z.x, 0.0, -camera.global_basis.z.z).normalized()
	var right := Vector3(camera.global_basis.x.x, 0.0, camera.global_basis.x.z).normalized()
	var ways := {
		&"move_forward": forward, &"move_back": -forward, &"move_left": -right, &"move_right": right
	}
	var space := (_player as Node3D).get_world_3d().direct_space_state
	var kept: Array[StringName] = []
	for action: StringName in wanted:
		var ahead: Vector3 = _at() + (ways[action] as Vector3) * EDGE_LOOK + Vector3.UP * 0.5
		var query := PhysicsRayQueryParameters3D.create(ahead, ahead + Vector3.DOWN * (DROP + 0.5))
		if _player is CollisionObject3D:
			query.exclude = [(_player as CollisionObject3D).get_rid()]
			query.collision_mask = (_player as CollisionObject3D).collision_mask
		if not space.intersect_ray(query).is_empty():
			kept.append(action)
	return kept


## Turns the view toward [param point] with the look actions, pressed as far as the error
## asks (closed loop: checked again every frame). Returns the error in radians: x yaw
## (positive to the right), y pitch (positive up). [param level] keeps the view level.
func _look_at(point: Vector3, level: bool = false) -> Vector2:
	var camera := tree.root.get_viewport().get_camera_3d()
	if camera == null or not camera.is_inside_tree():
		return Vector2.ZERO
	var basis := camera.global_basis
	var forward := -basis.z
	var to := point - camera.global_position
	var flat_forward := Vector2(forward.x, forward.z).normalized()
	var flat_to := Vector2(to.x, to.z)
	if flat_to.length() < 0.01:
		return Vector2.ZERO
	var right := Vector2(basis.x.x, basis.x.z).normalized()
	var yaw := atan2(flat_to.dot(right), flat_to.dot(flat_forward))
	var pitch_now := atan2(forward.y, Vector2(forward.x, forward.z).length())
	var pitch_to := 0.0 if level else atan2(to.y, flat_to.length())
	var error := Vector2(yaw, pitch_to - pitch_now)
	var stick := Vector2(
		clampf(error.x * LOOK_GAIN, -1.0, 1.0), clampf(-error.y * LOOK_GAIN, -1.0, 1.0)
	)
	if absf(error.x) < 0.01:
		stick.x = 0.0
	if absf(error.y) < 0.02:
		stick.y = 0.0
	_press_look(stick)
	return error


## Presses look_left/right/up/down so the game's Input.get_vector reads [param stick] past
## the actions' dead zone.
func _press_look(stick: Vector2) -> void:
	var size := stick.length()
	var strengths := {&"look_left": 0.0, &"look_right": 0.0, &"look_up": 0.0, &"look_down": 0.0}
	if size > 0.001:
		var zone := 0.0
		for action: StringName in strengths:
			if InputMap.has_action(action):
				zone = maxf(zone, InputMap.action_get_deadzone(action))
		var raw := stick / size * minf(zone + minf(size, 1.0) * (1.0 - zone) + 0.01, 1.0)
		strengths[&"look_right" if raw.x > 0.0 else &"look_left"] = absf(raw.x)
		strengths[&"look_down" if raw.y > 0.0 else &"look_up"] = absf(raw.y)
	for action: StringName in strengths:
		if not InputMap.has_action(action):
			continue
		var strength := float(strengths[action])
		var was := float(_looks.get(action, 0.0))
		if is_equal_approx(strength, was) and Input.is_action_pressed(action) == (strength > 0.0):
			continue
		_looks[action] = strength
		if strength > 0.0:
			Input.action_press(action, strength)
		else:
			Input.action_release(action)
		var event := InputEventAction.new()
		event.action = action
		event.pressed = strength > 0.0
		event.strength = strength
		# Delivered now, in this physics frame, as the press above already is: left in
		# Input's buffer it would reach a recording a frame later than the game felt it,
		# and its replay would look round a frame late.
		Input.parse_input_event(event)
		Input.flush_buffered_events()


func _release_looks() -> void:
	_press_look(Vector2.ZERO)


## Let go of what the fight holds, once, when the drive takes over again.
func _stop() -> bool:
	if _steering:
		_release_all()
	return false


func _release_all() -> void:
	_steering = false
	_hold_moves(NO_MOVES)
	_release_attack()
	_release_looks()


# --- Places -----------------------------------------------------------------------------------


func _at() -> Vector3:
	return (_player as Node3D).global_position if _player is Node3D else Vector3.ZERO


func _eyes() -> Vector3:
	var camera := tree.root.get_viewport().get_camera_3d()
	if camera != null and camera.is_inside_tree():
		return camera.global_position
	return _at() + Vector3.UP * 1.6


func _distance(node: Node) -> float:
	return (node as Node3D).global_position.distance_to(_at()) if node is Node3D else INF


func _flat(point: Vector3) -> float:
	var at := _at()
	return Vector2(point.x, point.z).distance_to(Vector2(at.x, at.z))


func _t() -> float:
	return snappedf(_frame / float(Engine.physics_ticks_per_second), 0.01)


func _describe(node: Node) -> String:
	return String(node.name)


## Why the exit wasn't reached, from what the bot was after when the run ended.
func _obstacle() -> String:
	if not active:
		return why
	var held := _held_keys()
	for door: Node in tree.get_nodes_in_group(&"door"):
		var key := str(door.get_meta(&"key", ""))
		if key.is_empty() or key in held:
			continue
		var key_node: Node = null
		for node: Node in tree.get_nodes_in_group(&"key"):
			if String(node.name) == key:
				key_node = node
		if key_node != null and not _reachable(key_node) and _goal_kind != "exit":
			var close: Variant = _closest.get(key, null)
			return (
				"%s needs %s, and the bot found no way to it%s."
				% [door.name, key, "" if close == null else " (closest %s m)" % close]
			)
	if _goal == null or not is_instance_valid(_goal):
		return "No goal left that the bot could reach."
	var name := String(_goal.name)
	var closest: Variant = _closest.get(name, null)
	var text := "Heading for %s (%s)" % [name, _goal_kind]
	if closest != null:
		text += ", closest %s m" % closest
	var here := _round(_at())
	if not _reachable(_goal):
		text += "; the map has no way there from (%s, %s, %s)" % here
	elif _still_for() >= 20.0:
		text += "; stuck at (%s, %s, %s) for %d s" % (here + [roundi(_still_for())])
	if deaths > 0:
		text += "; died %d times" % deaths
	return text + "."


## Seconds the trail shows the player within 2 m of where it is now.
func _still_for() -> float:
	var still := 0.0
	for index: int in range(trail.size() - 1, -1, -1):
		var point := Vector3(trail[index][1], trail[index][2], trail[index][3])
		if point.distance_to(_at()) > 2.0:
			break
		still = _t() - float(trail[index][0])
	return still


## The grid's way from the player to the goal it is after (up to 40 cells), for the report.
func _way() -> Array:
	var cells: Array = []
	if cover == null or _goal == null or not is_instance_valid(_goal) or not _player is Node3D:
		return cells
	var found: Array = cover.call(&"way", _at(), (_goal as Node3D).global_position)
	for point: Vector3 in found.slice(0, 40):
		cells.append(_round(point))
	return cells


static func _round(at: Vector3) -> Array:
	return [snappedf(at.x, 0.01), snappedf(at.y, 0.01), snappedf(at.z, 0.01)]
