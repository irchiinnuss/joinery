extends RefCounted
## Coverage for the playtest bot: a grid over the level's navigable area, the cells the player
## has been to, and where to head next to see more of it. No model: the grid comes from the
## game's own physics, and every choice from the bot's seeded generator.
##
## The grid lies on the ground plane (x and z in 3D, x and y in 2D). A cell is navigable when
## the player could stand there and get there from where it started: in 3D a floor under the
## cell (a downward ray) with room above it, joined to its neighbours by a step or a short
## jump; top-down an empty cell joined to the start; side-on an empty cell with ground a few
## cells below it, joined to the start in any direction. Every cell the player really stands
## in counts as navigable too. The bot walks toward the nearest unvisited cell along the grid
## (a breadth-first search), gives a cell up for a while when it doesn't get there, and
## reports the share it reached and a picture of the map with the unreached cells marked.

const CELL_3D := 2.0
const CELL_2D := 64.0
## The grid never has more cells than this; a bigger level gets bigger cells.
const MAX_CELLS := 4096
## How far around the player a huge level is mapped, in cells.
const MAX_SPAN := 96
## 3D: the highest step between neighbouring floors the player is taken to climb, and the
## height above the start a floor may be and still count (higher is a roof or a tower top).
const STEP_3D := 1.6
const ABOVE_3D := 6.0
## 3D: the highest step a route climbs from one cell to the next (lower than STEP_3D: a route
## takes the ramp, not the side of it).
const CLIMB_3D := 1.1
## Side-on: how many cells below an empty cell the ground may be (jump height).
const GROUND_CELLS := 3
## Frames heading for a cell before it is given up for a while, and how long that is.
const TRY_FRAMES := 360
const SKIP_FRAMES := 1800
## The four ways out of a cell, in the order of the wall bits.
const DIRS: Array[Vector2i] = [Vector2i(1, 0), Vector2i(-1, 0), Vector2i(0, 1), Vector2i(0, -1)]
## Heights above the higher floor where a wall between two cells is looked for.
const WALL_LIFTS: Array[float] = [0.7, 1.5]
## Pixels a cell takes in the map picture, at most and at least.
const MAP_WIDTH := 320

## "3d", "topdown" or "side" once built ("" before).
var mode: String = ""
var cell: float = CELL_3D
var origin: Vector2 = Vector2.ZERO
var cols: int = 0
var rows: int = 0
## Per cell: 1 when navigable.
var navigable: PackedByteArray = PackedByteArray()
## Per cell: the frame the player first stood in it plus one, 0 never.
var visited: PackedInt32Array = PackedInt32Array()
## Per cell: the frames the player spent in it (the time-spent heatmap).
var dwell: PackedInt32Array = PackedInt32Array()
var start: Vector2i = Vector2i(-1, -1)
var built: bool = false

var _tree: SceneTree
var _floor: PackedFloat32Array = PackedFloat32Array()
## 3D, per cell: a bit per way out that a wall or a shut door blocks (DIRS order; +4 for the
## short jump over the next cell).
var _walls: PackedByteArray = PackedByteArray()
var _skip: Dictionary = {}
var _goal: int = -1
var _goal_since: int = 0
var _key: String = ""
## The cell the player stood in last frame (-1 none): a step from it to the next cell shows
## the way between them is open, whatever the probes said.
var _last_cell: int = -1
## Ways the player walked, as cell * 8 + wall bit: no probe walls them again (a doorway's
## jamb the probe's line grazes, a door probed while it was still opening).
var _passed: Dictionary = {}


func _init(scene_tree: SceneTree) -> void:
	_tree = scene_tree


## Map the level around [param player] (a CharacterBody or any Node2D/3D) for [param style]
## ("3d", "topdown", "platformer"). A rebuilt grid of the same level keeps what was visited.
func build(player: Node, style: String) -> void:
	var is_3d := player is Node3D
	mode = "3d" if is_3d else ("side" if style == "platformer" else "topdown")
	var at := _plane(_where(player))
	var bounds := _bounds(player, is_3d)
	var size := CELL_3D if is_3d else CELL_2D
	var span := bounds.size / size
	while ceili(span.x) * ceili(span.y) > MAX_CELLS:
		size *= 1.25
		span = bounds.size / size
	var corner := bounds.position
	if ceili(span.x) > MAX_SPAN:
		corner.x = at.x - MAX_SPAN * size / 2.0
	if ceili(span.y) > MAX_SPAN:
		corner.y = at.y - MAX_SPAN * size / 2.0
	# Cells on a fixed lattice, so a level mapped again lines up with itself.
	corner = (corner / size).floor() * size
	var far := bounds.end
	var wide := clampi(ceili((far.x - corner.x) / size), 1, MAX_SPAN)
	var tall := clampi(ceili((far.y - corner.y) / size), 1, MAX_SPAN)
	var scene := _tree.current_scene
	var key := scene.scene_file_path if scene != null else ""
	# The same level again (a restart, a new player): where the player has been still counts,
	# and so does the floor found before (the player may be mid-fall this time).
	var seen: Dictionary = {}
	var spent: Dictionary = {}
	var floors: Array[Vector2] = []
	var heights: Array[float] = []
	if key == _key and built:
		for index: int in visited.size():
			var centre := origin + Vector2(index % cols + 0.5, index / cols + 0.5) * cell
			if visited[index] != 0:
				seen[centre] = visited[index]
			if index < dwell.size() and dwell[index] != 0:
				spent[centre] = dwell[index]
			if navigable[index] != 0:
				floors.append(centre)
				heights.append(_floor[index])
	cell = size
	origin = corner
	cols = wide
	rows = tall
	_key = key
	visited = PackedInt32Array()
	visited.resize(cols * rows)
	dwell = PackedInt32Array()
	dwell.resize(cols * rows)
	for centre: Vector2 in seen:
		var index := _index(_cell_of(centre))
		if index >= 0:
			visited[index] = int(seen[centre])
	for centre: Vector2 in spent:
		var index := _index(_cell_of(centre))
		if index >= 0:
			dwell[index] += int(spent[centre])
	_skip.clear()
	_goal = -1
	_last_cell = -1
	_passed.clear()
	start = _cell_of(at)
	_map(player)
	for item: int in floors.size():
		var index := _index(_cell_of(floors[item]))
		if index >= 0 and navigable[index] == 0:
			navigable[index] = 1
			_floor[index] = heights[item]
	built = true


## The player is at [param at] on [param frame]: its cell counts as visited.
func visit(at: Vector3, frame: int) -> void:
	if not built:
		return
	var here := _index(_cell_of(_plane(at)))
	if here < 0:
		return
	navigable[here] = 1
	dwell[here] += 1
	if visited[here] == 0:
		visited[here] = frame + 1
	if here == _goal:
		_goal = -1
	if here != _last_cell:
		_walked(_last_cell, here)
		_last_cell = here


## The player walked from cell [param from] into the next cell [param to] (or over one, a short
## jump): no wall stands between them, now or after a later probe. A door probed while it was
## still sliding open, or a doorway whose jamb the probe's line grazes, keeps its wall bits
## otherwise, and once the player is somewhere else (sent back to a checkpoint) the grid has
## no way back through it. A respawn's jump across the map clears nothing.
func _walked(from: int, to: int) -> void:
	if mode != "3d" or from < 0 or to < 0 or from >= _walls.size() or to >= _walls.size():
		return
	var step := Vector2i(to % cols, to / cols) - Vector2i(from % cols, from / cols)
	for way: int in DIRS.size():
		var bits := Vector2i(-1, -1)
		if step == DIRS[way]:
			bits = Vector2i(way, way ^ 1)
		elif step == DIRS[way] * 2:
			bits = Vector2i(way + 4, (way ^ 1) + 4)
		if bits.x >= 0:
			_passed[from * 8 + bits.x] = true
			_passed[to * 8 + bits.y] = true
			_set_wall(from, bits.x, false)
			_set_wall(to, bits.y, false)


## The next place to walk to from [param at] to see more of the level (a world position), or
## null when everything reachable was seen (or nothing is mapped).
func explore(at: Vector3, frame: int, rng: RandomNumberGenerator) -> Variant:
	if not built:
		return null
	if _goal >= 0 and frame - _goal_since > TRY_FRAMES:
		_skip[_goal] = frame + SKIP_FRAMES
		_goal = -1
	if _goal < 0 or visited[_goal] != 0:
		_goal = _nearest_unvisited(at, frame, rng)
		_goal_since = frame
	if _goal < 0:
		return null
	return _step_toward(at, _goal)


## Where to walk next from [param at] to reach [param target] (world positions): the next
## grid cell on the way when both are mapped and joined, else the target itself.
func route(at: Vector3, target: Vector3) -> Vector3:
	if not built:
		return target
	var goal := _index(_cell_of(_plane(target)))
	if goal < 0 or navigable[goal] == 0:
		# A target off the grid (a switch, a doorway): the way to the cell beside it.
		var cells := way(at, target)
		return cells[0] if cells.size() > 1 else target
	var step: Variant = _step_toward(at, goal)
	return step if step is Vector3 else target


## Whether the grid joins [param at] to [param target], or to a navigable cell next to it (a
## marker in a doorway or on a ledge edge). True when nothing is mapped: the bot can't tell.
func reachable(at: Vector3, target: Vector3) -> bool:
	if not built or _index(_cell_of(_plane(at))) < 0:
		return true
	return not way(at, target).is_empty() or _cell_of(_plane(at)) == _cell_of(_plane(target))


## The cells from [param at] to [param target] (or a navigable cell next to it), as world
## positions, the first step first; empty when the grid doesn't join them.
func way(at: Vector3, target: Vector3) -> Array[Vector3]:
	var found: Array[Vector3] = []
	if not built:
		return found
	var first := _index(_cell_of(_plane(at)))
	var goal := _cell_of(_plane(target))
	var ends := {}
	for dx: int in [-1, 0, 1]:
		for dy: int in [-1, 0, 1]:
			var index := _index(goal + Vector2i(dx, dy))
			if index >= 0 and navigable[index] != 0:
				ends[index] = true
	if first < 0 or ends.is_empty():
		return found
	var parent := {first: -1}
	var queue: Array[int] = [first]
	var head := 0
	var end := -1
	while head < queue.size():
		var index := queue[head]
		head += 1
		if ends.has(index):
			end = index
			break
		for next: int in _neighbours(index):
			if not parent.has(next):
				parent[next] = index
				queue.append(next)
	var cell_index := end
	while cell_index >= 0 and cell_index != first:
		found.push_front(_world(cell_index, at))
		cell_index = int(parent[cell_index])
	return found


## Probes the floor and the walls again within [param radius] of [param at] (a door just
## opened, a bridge came down), then joins what that opens up to the rest of the grid.
func reprobe(player: Node, at: Vector3, radius: float) -> void:
	if not built or mode != "3d" or not player is Node3D:
		return
	var centre := _cell_of(_plane(at))
	var reach := ceili(radius / cell)
	var player_y := _where(player).y
	var area: Array[int] = []
	for dx: int in range(-reach, reach + 1):
		for dy: int in range(-reach, reach + 1):
			var at_cell := centre + Vector2i(dx, dy)
			var index := _index(at_cell)
			if index < 0:
				continue
			area.append(index)
			if navigable[index] != 0:
				continue
			var middle := origin + (Vector2(at_cell) + Vector2(0.5, 0.5)) * cell
			var height: Variant = _floor_at(player, middle, player_y)
			if height is float:
				_floor[index] = height
	# What was a shut door is a doorway now: look for the walls around here again.
	var open := PackedByteArray()
	open.resize(cols * rows)
	for index: int in cols * rows:
		open[index] = 1 if not is_nan(_floor[index]) else 0
	for index: int in area:
		if open[index] != 0:
			_find_walls(player, index, open)
	# Everything the doorway joins to the navigable cells counts now, beyond it too.
	var queue: Array[int] = []
	for index: int in cols * rows:
		if navigable[index] != 0:
			queue.append(index)
	var head := 0
	while head < queue.size():
		var index := queue[head]
		head += 1
		for next: int in _joined(index, open):
			if navigable[next] == 0:
				navigable[next] = 1
				queue.append(next)


## {mode, cell, origin, cols, rows, start, navigable, visited, percent, grid, dwell}: the
## grid as rows of text, "#" visited, "." navigable and never reached, " " not navigable;
## dwell lists [col, row, frames] for each cell the player spent time in.
func summary() -> Dictionary:
	if not built:
		return {}
	var lines: Array[String] = []
	var spent: Array = []
	var open := 0
	var seen := 0
	for row: int in rows:
		var line := ""
		for col: int in cols:
			var index := row * cols + col
			if dwell[index] != 0:
				spent.append([col, row, dwell[index]])
			if visited[index] != 0:
				line += "#"
				seen += 1
				open += 1
			elif navigable[index] != 0:
				line += "."
				open += 1
			else:
				line += " "
		lines.append(line)
	return {
		"mode": mode,
		"cell": snappedf(cell, 0.01),
		"origin": [snappedf(origin.x, 0.01), snappedf(origin.y, 0.01)],
		"cols": cols,
		"rows": rows,
		"start": [start.x, start.y],
		"navigable": open,
		"visited": seen,
		"percent": snappedf(100.0 * seen / maxf(open, 1.0), 0.1),
		"grid": lines,
		"dwell": spent,
	}


## The map as a picture: reached cells teal, unreached cells red, the start white, the rest
## clear. Saved at [param path]; returns false when there is nothing to draw.
func save_map(path: String) -> bool:
	if not built:
		return false
	var scale := clampi(MAP_WIDTH / maxi(cols, rows), 3, 16)
	var image := Image.create(cols * scale, rows * scale, false, Image.FORMAT_RGBA8)
	image.fill(Color(0, 0, 0, 0))
	for row: int in rows:
		for col: int in cols:
			var index := row * cols + col
			var colour := Color(0, 0, 0, 0)
			if visited[index] != 0:
				colour = Color(0.18, 0.62, 0.58)
			elif navigable[index] != 0:
				colour = Color(0.86, 0.29, 0.27)
			if colour.a > 0.0:
				image.fill_rect(
					Rect2i(col * scale, row * scale, maxi(scale - 1, 1), maxi(scale - 1, 1)), colour
				)
	if _index(start) >= 0:
		var dot := maxi(scale / 2, 2)
		image.fill_rect(
			Rect2i(
				start.x * scale + (scale - dot) / 2, start.y * scale + (scale - dot) / 2, dot, dot
			),
			Color.WHITE
		)
	return image.save_png(path) == OK


# --- The grid ------------------------------------------------------------------------------


func _map(player: Node) -> void:
	navigable = PackedByteArray()
	navigable.resize(cols * rows)
	_floor = PackedFloat32Array()
	_floor.resize(cols * rows)
	_floor.fill(NAN)
	var open := PackedByteArray()
	open.resize(cols * rows)
	var player_y := _where(player).y
	for row: int in rows:
		for col: int in cols:
			var index := row * cols + col
			var centre := origin + Vector2(col + 0.5, row + 0.5) * cell
			match mode:
				"3d":
					var height: Variant = _floor_at(player, centre, player_y)
					if height is float:
						open[index] = 1
						_floor[index] = height
				"topdown":
					open[index] = 1 if _free_2d(player, centre) else 0
				_:
					var free := _free_2d(player, centre)
					open[index] = 1 if free and _ground_below(player, centre) else 0
	_walls = PackedByteArray()
	_walls.resize(cols * rows)
	if mode == "3d":
		for index: int in cols * rows:
			if open[index] != 0:
				_find_walls(player, index, open)
	_flood(open, player_y)
	# The visited cells were stood in: they are navigable whatever the probes say.
	for index: int in visited.size():
		if visited[index] != 0:
			navigable[index] = 1


## Cells joined to the start, from [param open]: in 3D by steps and short jumps (one missing
## cell), top-down by the four sides, side-on in any of eight directions.
func _flood(open: PackedByteArray, player_y: float) -> void:
	var first := _index(start)
	if first < 0:
		return
	if open[first] == 0:
		open[first] = 1
		_floor[first] = player_y
	navigable[first] = 1
	var queue: Array[int] = [first]
	var head := 0
	while head < queue.size():
		var index := queue[head]
		head += 1
		for next: int in _joined(index, open):
			if navigable[next] == 0:
				navigable[next] = 1
				queue.append(next)


func _joined(index: int, open: PackedByteArray) -> Array[int]:
	var found: Array[int] = []
	var here := Vector2i(index % cols, index / cols)
	var directions: Array[Vector2i] = [
		Vector2i(1, 0), Vector2i(-1, 0), Vector2i(0, 1), Vector2i(0, -1)
	]
	if mode == "side":
		directions.append_array(
			[Vector2i(1, 1), Vector2i(-1, 1), Vector2i(1, -1), Vector2i(-1, -1)]
		)
	for direction: Vector2i in directions:
		var next := _index(here + direction)
		if next < 0:
			continue
		var way := DIRS.find(direction)
		if open[next] != 0:
			if (
				mode != "3d"
				or absf(_floor[next] - _floor[index]) <= STEP_3D and not _walled(index, way)
			):
				found.append(next)
		elif mode == "3d":
			# A gap of one cell: a short jump to the floor beyond it.
			var beyond := _index(here + direction * 2)
			if (
				beyond >= 0
				and open[beyond] != 0
				and absf(_floor[beyond] - _floor[index]) <= 1.2
				and not _walled(index, way + 4)
			):
				found.append(beyond)
	return found


## Whether the way [param way] (DIRS index, +4 for the jump) out of [param index] is walled.
func _walled(index: int, way: int) -> bool:
	return index < _walls.size() and (_walls[index] >> way) & 1 == 1


## Looks for walls (and shut doors) between [param index] and each neighbour with a floor,
## and the floor two cells away (a short jump), setting the wall bits both ways.
func _find_walls(player: Node, index: int, open: PackedByteArray) -> void:
	var here := Vector2i(index % cols, index / cols)
	for way: int in DIRS.size():
		var next := _index(here + DIRS[way])
		if next < 0:
			continue
		var target := next
		var bit := way
		if open[next] == 0:
			target = _index(here + DIRS[way] * 2)
			bit = way + 4
			if target < 0 or open[target] == 0:
				continue
		var back := (way ^ 1) + (bit - way)
		var blocked := not _passed.has(index * 8 + bit) and _wall_between(player, index, target)
		_set_wall(index, bit, blocked)
		_set_wall(target, back, blocked)


func _set_wall(index: int, bit: int, blocked: bool) -> void:
	if blocked:
		_walls[index] |= 1 << bit
	else:
		_walls[index] &= ~(1 << bit) & 0xFF


## A solid between the centres of cells [param a] and [param b] above the higher floor (bodies
## that move about, an enemy or a crate, don't count).
func _wall_between(player: Node, a: int, b: int) -> bool:
	if is_nan(_floor[a]) or is_nan(_floor[b]):
		return false
	var space := (player as Node3D).get_world_3d().direct_space_state
	var from := _world(a, Vector3.ZERO)
	var to := _world(b, Vector3.ZERO)
	var top := maxf(_floor[a], _floor[b])
	for lift: float in WALL_LIFTS:
		var exclude := _exclude(player)
		for _attempt: int in 3:
			var query := PhysicsRayQueryParameters3D.create(
				Vector3(from.x, top + lift, from.z), Vector3(to.x, top + lift, to.z)
			)
			query.collision_mask = _mask(player)
			query.exclude = exclude
			var hit := space.intersect_ray(query)
			if hit.is_empty():
				break
			var collider: Object = hit["collider"]
			if collider is CharacterBody3D or collider is RigidBody3D:
				exclude.append(hit["rid"])
				continue
			return true
	return false


## Breadth-first from the player's cell over the navigable grid to the nearest cell nobody has
## stood in (ties in a seeded order); -1 when none is left.
func _nearest_unvisited(at: Vector3, frame: int, rng: RandomNumberGenerator) -> int:
	var first := _index(_cell_of(_plane(at)))
	if first < 0:
		return -1
	var seen := PackedByteArray()
	seen.resize(cols * rows)
	seen[first] = 1
	var layer: Array[int] = [first]
	while not layer.is_empty():
		var candidates: Array[int] = []
		var next_layer: Array[int] = []
		for index: int in layer:
			for next: int in _neighbours(index):
				if seen[next] != 0:
					continue
				seen[next] = 1
				next_layer.append(next)
				if visited[next] == 0 and int(_skip.get(next, 0)) <= frame:
					candidates.append(next)
		if not candidates.is_empty():
			return candidates[rng.randi_range(0, candidates.size() - 1)]
		layer = next_layer
	return -1


## The world position of the cell two steps along the grid from [param at] toward
## [param goal] (the goal itself when it is that close), or null when the grid doesn't join them.
func _step_toward(at: Vector3, goal: int) -> Variant:
	var first := _index(_cell_of(_plane(at)))
	if first < 0 or first == goal:
		return _world(goal, at)
	var parent := {first: -1}
	var queue: Array[int] = [first]
	var head := 0
	while head < queue.size() and not parent.has(goal):
		var index := queue[head]
		head += 1
		for next: int in _neighbours(index):
			if not parent.has(next):
				parent[next] = index
				queue.append(next)
	if not parent.has(goal):
		return null
	var path: Array[int] = [goal]
	while int(parent[path[-1]]) != first and int(parent[path[-1]]) >= 0:
		path.append(int(parent[path[-1]]))
	path.reverse()
	# Two cells ahead; in 3D round a corner one cell at a time (a door's jamb, a catwalk).
	var ahead := 0
	if path.size() > 1 and (mode != "3d" or path[1] - path[0] == path[0] - first):
		ahead = 1
	return _world(path[ahead], at)


func _neighbours(index: int) -> Array[int]:
	var found: Array[int] = []
	var here := Vector2i(index % cols, index / cols)
	for way: int in DIRS.size():
		var next := _index(here + DIRS[way])
		if next >= 0 and navigable[next] != 0:
			if not _walled(index, way) and _climbable(index, next):
				found.append(next)
		elif mode == "3d" and next >= 0:
			var beyond := _index(here + DIRS[way] * 2)
			if beyond >= 0 and navigable[beyond] != 0:
				if not _walled(index, way + 4) and _climbable(index, beyond):
					found.append(beyond)
	return found


## 3D: from cell [param a] the player can get onto cell [param b]: down any drop, up a step.
func _climbable(a: int, b: int) -> bool:
	if mode != "3d" or a >= _floor.size() or b >= _floor.size():
		return true
	if is_nan(_floor[a]) or is_nan(_floor[b]):
		return true
	return _floor[b] - _floor[a] <= CLIMB_3D


# --- Probing the level ------------------------------------------------------------------------


## The floor height under [param centre] (3D), or null: the highest surface facing up that is
## no higher than [constant ABOVE_3D] over the player, with room to stand on it.
func _floor_at(player: Node, centre: Vector2, player_y: float) -> Variant:
	var space := (player as Node3D).get_world_3d().direct_space_state
	var top := player_y + 40.0
	for _attempt: int in 4:
		var query := PhysicsRayQueryParameters3D.create(
			Vector3(centre.x, top, centre.y), Vector3(centre.x, player_y - 40.0, centre.y)
		)
		query.collision_mask = _mask(player)
		query.exclude = _exclude(player)
		var hit := space.intersect_ray(query)
		if hit.is_empty():
			return null
		var point: Vector3 = hit["position"]
		var normal: Vector3 = hit["normal"]
		if point.y <= player_y + ABOVE_3D and normal.y > 0.6:
			var room := PhysicsPointQueryParameters3D.new()
			room.position = point + Vector3.UP * 0.9
			room.collision_mask = _mask(player)
			room.exclude = _exclude(player)
			if space.intersect_point(room, 1).is_empty():
				return point.y
			return null
		top = point.y - 0.05
	return null


func _free_2d(player: Node, centre: Vector2) -> bool:
	var space := (player as Node2D).get_world_2d().direct_space_state
	var query := PhysicsPointQueryParameters2D.new()
	query.position = centre
	query.collision_mask = _mask(player)
	query.exclude = _exclude(player)
	return space.intersect_point(query, 1).is_empty()


func _ground_below(player: Node, centre: Vector2) -> bool:
	var space := (player as Node2D).get_world_2d().direct_space_state
	var query := PhysicsRayQueryParameters2D.create(
		centre, centre + Vector2(0, cell * (GROUND_CELLS + 0.5))
	)
	query.collision_mask = _mask(player)
	query.exclude = _exclude(player)
	return not space.intersect_ray(query).is_empty()


## The level's extent on the ground plane: its geometry (meshes, CSG, grid maps, collision
## shapes, tile maps) and the player.
func _bounds(player: Node, is_3d: bool) -> Rect2:
	var at := _plane(_where(player))
	var rect := Rect2(at - Vector2.ONE * cell, Vector2.ONE * cell * 2.0)
	var scene := _tree.current_scene
	if scene == null:
		return rect
	for node: Node in scene.find_children("*", "Node", true, false):
		if node == player or player.is_ancestor_of(node):
			continue
		var area: Variant = _extent_3d(node) if is_3d else _extent_2d(node)
		if area is Rect2:
			rect = rect.merge(area)
	return rect.grow(cell * 0.5)


func _extent_3d(node: Node) -> Variant:
	if node is VisualInstance3D and (node as VisualInstance3D).is_visible_in_tree():
		var box := (node as Node3D).global_transform * (node as VisualInstance3D).get_aabb()
		if box.size.length() > 400.0:
			return null  # a sky dome or a far backdrop, not ground
		return Rect2(box.position.x, box.position.z, box.size.x, box.size.z)
	if node is GridMap:
		var grid := node as GridMap
		var cells := grid.get_used_cells()
		if cells.is_empty():
			return null
		var box := AABB(grid.to_global(grid.map_to_local(cells[0])), Vector3.ZERO)
		for each: Vector3i in cells:
			box = box.expand(grid.to_global(grid.map_to_local(each)))
		return Rect2(box.position.x, box.position.z, box.size.x, box.size.z)
	return null


func _extent_2d(node: Node) -> Variant:
	if node is CollisionShape2D and (node as CollisionShape2D).shape != null:
		var shape := node as CollisionShape2D
		var local := shape.shape.get_rect()
		return _global_rect(shape.global_transform, local)
	if node is TileMapLayer:
		var layer := node as TileMapLayer
		var used := layer.get_used_rect()
		if used.size == Vector2i.ZERO:
			return null
		var size := Vector2(layer.tile_set.tile_size) if layer.tile_set != null else Vector2.ONE
		var local := Rect2(Vector2(used.position) * size, Vector2(used.size) * size)
		return _global_rect(layer.global_transform, local)
	return null


static func _global_rect(transform: Transform2D, local: Rect2) -> Rect2:
	var rect := Rect2(transform * local.position, Vector2.ZERO)
	for corner: Vector2 in [
		local.end, Vector2(local.end.x, local.position.y), Vector2(local.position.x, local.end.y)
	]:
		rect = rect.expand(transform * corner)
	return rect


static func _mask(player: Node) -> int:
	if player is CollisionObject3D:
		return (player as CollisionObject3D).collision_mask
	if player is CollisionObject2D:
		return (player as CollisionObject2D).collision_mask
	return 0xFFFFFFFF


static func _exclude(player: Node) -> Array[RID]:
	if player is CollisionObject3D:
		return [(player as CollisionObject3D).get_rid()]
	if player is CollisionObject2D:
		return [(player as CollisionObject2D).get_rid()]
	return []


# --- Cells ----------------------------------------------------------------------------------


func _cell_of(at: Vector2) -> Vector2i:
	return Vector2i(floori((at.x - origin.x) / cell), floori((at.y - origin.y) / cell))


func _index(at: Vector2i) -> int:
	if at.x < 0 or at.y < 0 or at.x >= cols or at.y >= rows:
		return -1
	return at.y * cols + at.x


## The centre of cell [param index] as a world position (at the floor's height in 3D, else the
## height of [param at]).
func _world(index: int, at: Vector3) -> Vector3:
	var centre := origin + Vector2(index % cols + 0.5, index / cols + 0.5) * cell
	if mode == "3d":
		var height := _floor[index] if index < _floor.size() and not is_nan(_floor[index]) else at.y
		return Vector3(centre.x, height, centre.y)
	return Vector3(centre.x, centre.y, 0.0)


func _plane(at: Vector3) -> Vector2:
	return Vector2(at.x, at.z) if mode == "3d" else Vector2(at.x, at.y)


static func _where(node: Node) -> Vector3:
	if node is Node3D:
		return (node as Node3D).global_position
	if node is Node2D:
		var at := (node as Node2D).global_position
		return Vector3(at.x, at.y, 0.0)
	return Vector3.ZERO
