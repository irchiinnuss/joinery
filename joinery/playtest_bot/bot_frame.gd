extends RefCounted
## Where the camera looks while the bot plays for store media (--media).
##
## A playtest bot steers by strafing toward its goal and taps look keys at random, so
## its view spends most of the run on walls, floors and ceilings: fine for testing,
## useless as a screenshot. Here the view is aimed like a player would aim it:
##
## - at the nearest goal or enemy the camera can actually see (a ray from the camera
##   reaches it), preferring what is already ahead, so the view does not whip around;
## - with nothing in sight, along the most open direction: rays fan out level with the
##   eyes and the longest free one wins, so the view looks down a room, never into a wall;
## - always close to level: pitch stays between PITCH_LOW and PITCH_HIGH, never at the
##   ceiling or the floor.
##
## It turns through the player's own look(delta) (the blueprints' controllers: yaw on the
## body, pitch on the head) and otherwise turns the body and the camera's parent itself,
## a little each frame. Picks depend only on the game's state, so a replay of the same
## seed frames the same moments.

const ENEMY_GROUPS: Array[StringName] = [&"enemy", &"enemies", &"mob", &"monster", &"boss"]
const GOAL_GROUPS: Array[StringName] = [
	&"goal",
	&"objective",
	&"pickup",
	&"collectible",
	&"key",
	&"exit",
	&"door",
	&"npc",
	&"interactable",
]
const SIGHT := 35.0
## HUD parts listed with a picture, at most; and the share of the screen past which a control
## is a backdrop (a fade, a full-screen container), not a HUD part.
const HUD_LIMIT := 64
const HUD_BACKDROP := 0.5
const RETARGET := 45
const FAN := 16
const PITCH_LOW := -0.22
const PITCH_HIGH := 0.12
## The most the view turns per physics frame (radians): about 150 degrees a second.
const TURN := 0.045
## Every this many frames the view is checked for something right in front of it.
const BLOCK_EVERY := 15
## Nearer than this (metres) along the centre of the view, the picture is a wall or a
## prop up close, not the game: those frames are listed so no screenshot is taken there.
const BLOCKED := 2.0
## An enemy this close to the centre of the view (radians), and this far away (metres),
## makes a well framed picture: the frames are listed so the cover is one of them.
const FRAMED_ANGLE := 0.2
const FRAMED_NEAR := 3.0
const FRAMED_FAR := 20.0

var tree: SceneTree
## Frames (every BLOCK_EVERY) whose view was blocked up close, for the report.
var blocked: Array[int] = []
## Frames (every BLOCK_EVERY) with an enemy near the centre of the view.
var framed: Array[int] = []
var _target: Node3D = null
var _open_yaw: float = NAN
var _next_pick: int = 0


func _init(scene_tree: SceneTree) -> void:
	tree = scene_tree


## One physics frame: pick what to look at now and then, and turn toward it.
func step(frame: int, player: Node) -> void:
	var body := player as Node3D
	var camera := tree.root.get_viewport().get_camera_3d()
	if body == null or camera == null or not body.is_inside_tree():
		return
	if frame % BLOCK_EVERY == 0:
		if _blocked(camera, body):
			blocked.append(frame)
		elif _framed(camera, body):
			framed.append(frame)
	if frame >= _next_pick or (_target != null and not _usable(_target)):
		_pick(camera, body)
		_next_pick = frame + RETARGET
	var eye := camera.global_position
	var yaw: float
	var pitch := -0.06
	if _target != null and _usable(_target):
		var aim := _aim_point(_target) - eye
		yaw = atan2(-aim.x, -aim.z)
		pitch = atan2(aim.y, Vector2(aim.x, aim.z).length())
	elif not is_nan(_open_yaw):
		yaw = _open_yaw
	else:
		return
	_turn(body, camera, yaw, clampf(pitch, PITCH_LOW, PITCH_HIGH))


## The best visible target, else the most open direction.
func _pick(camera: Camera3D, body: Node3D) -> void:
	var eye := camera.global_position
	var forward := -camera.global_basis.z
	var best: Node3D = null
	var best_score := -INF
	for node: Node3D in _candidates(body):
		var aim := _aim_point(node) - eye
		var distance := aim.length()
		if distance < 1.0 or distance > SIGHT or not _visible(eye, node, body):
			continue
		var ahead := forward.dot(aim / distance)
		var score := ahead * 2.0 - distance / SIGHT + (1.5 if _is_enemy(node) else 0.0)
		if score > best_score:
			best_score = score
			best = node
	_target = best
	_open_yaw = NAN if best != null else _open_direction(camera, body)


func _candidates(body: Node3D) -> Array[Node3D]:
	var found: Array[Node3D] = []
	for group: StringName in ENEMY_GROUPS + GOAL_GROUPS:
		for node: Node in tree.get_nodes_in_group(group):
			var spatial := node as Node3D
			if spatial != null and spatial != body and not body.is_ancestor_of(spatial):
				if spatial.is_visible_in_tree() and not found.has(spatial):
					found.append(spatial)
	return found


func _is_enemy(node: Node) -> bool:
	for group: StringName in ENEMY_GROUPS:
		if node.is_in_group(group):
			return true
	return false


func _usable(node: Node3D) -> bool:
	return is_instance_valid(node) and node.is_inside_tree() and node.is_visible_in_tree()


## A point on the thing worth looking at: a little above its origin (most are
## placed at their feet).
func _aim_point(node: Node3D) -> Vector3:
	return node.global_position + Vector3.UP * 0.6


## Whether nothing solid stands between the eye and [param node].
func _visible(eye: Vector3, node: Node3D, body: Node3D) -> bool:
	var space := body.get_world_3d().direct_space_state
	var target := _aim_point(node)
	var query := PhysicsRayQueryParameters3D.create(eye, target)
	query.exclude = _rids(body)
	var hit := space.intersect_ray(query)
	if hit.is_empty():
		return true
	var collider: Object = hit.get("collider")
	if collider is Node and (collider == node or node.is_ancestor_of(collider as Node)):
		return true
	if collider is Node and (collider as Node).is_ancestor_of(node):
		return true
	return (hit["position"] as Vector3).distance_to(target) < 0.8


## The yaw whose level ray runs furthest before it hits something.
func _open_direction(camera: Camera3D, body: Node3D) -> float:
	var space := body.get_world_3d().direct_space_state
	var eye := camera.global_position
	var current := body.global_rotation.y
	var best := current
	var best_length := -1.0
	for index in FAN:
		var yaw := current + TAU * index / FAN
		var direction := Vector3(-sin(yaw), 0.0, -cos(yaw))
		var query := PhysicsRayQueryParameters3D.create(eye, eye + direction * SIGHT)
		query.exclude = _rids(body)
		var hit := space.intersect_ray(query)
		var length := SIGHT if hit.is_empty() else eye.distance_to(hit["position"] as Vector3)
		# Ties go to the direction closest to the current view.
		length -= absf(wrapf(yaw - current, -PI, PI)) * 0.05
		if length > best_length:
			best_length = length
			best = yaw
	return wrapf(best, -PI, PI)


## Whether something solid stands closer than BLOCKED along the centre of the view.
func _blocked(camera: Camera3D, body: Node3D) -> bool:
	var eye := camera.global_position
	var query := PhysicsRayQueryParameters3D.create(eye, eye - camera.global_basis.z * BLOCKED)
	query.exclude = _rids(body)
	return not body.get_world_3d().direct_space_state.intersect_ray(query).is_empty()


## Lists [param frame] as no picture (a menu covers the view), once per BLOCK_EVERY.
func cover_up(frame: int) -> void:
	if frame % BLOCK_EVERY == 0 and not blocked.has(frame):
		blocked.append(frame)


## Whether the current enemy target sits near the centre of the view, neither too near nor
## too far, with nothing in front of it.
func _framed(camera: Camera3D, body: Node3D) -> bool:
	if _target == null or not _usable(_target) or not _is_enemy(_target):
		return false
	var eye := camera.global_position
	var aim := _aim_point(_target) - eye
	var distance := aim.length()
	if distance < FRAMED_NEAR or distance > FRAMED_FAR:
		return false
	var forward := -camera.global_basis.z
	return forward.angle_to(aim) < FRAMED_ANGLE and _visible(eye, _target, body)


## Where the HUD is on screen, for a cover that keeps its title off it: each visible control
## that draws something (text, a picture, a filled panel or bar), as [x, y, w, h] in shares
## of the window. Controls in their own viewports are left out.
static func hud_rects(viewport: Viewport) -> Array:
	var found: Array = []
	var screen := viewport.get_visible_rect().size
	if screen.x <= 0.0 or screen.y <= 0.0:
		return found
	var stack: Array[Node] = [viewport]
	while not stack.is_empty() and found.size() < HUD_LIMIT:
		var node: Node = stack.pop_back()
		if node is CanvasItem and not (node as CanvasItem).is_visible_in_tree():
			continue
		if node is Control and _draws(node as Control):
			var control := node as Control
			var xform := control.get_global_transform_with_canvas()
			var rect := Rect2(xform.origin, control.size * xform.get_scale())
			rect = rect.intersection(Rect2(Vector2.ZERO, screen))
			var share := rect.get_area() / (screen.x * screen.y)
			if rect.has_area() and share < HUD_BACKDROP:
				(
					found
					. append(
						[
							snappedf(rect.position.x / screen.x, 0.001),
							snappedf(rect.position.y / screen.y, 0.001),
							snappedf(rect.size.x / screen.x, 0.001),
							snappedf(rect.size.y / screen.y, 0.001),
						]
					)
				)
		for child: Node in node.get_children():
			if not child is SubViewport:
				stack.append(child)
	return found


static func _draws(control: Control) -> bool:
	if control is Label or control is Button:
		return not str(control.get(&"text")).strip_edges().is_empty()
	if control is RichTextLabel:
		return not (control as RichTextLabel).get_parsed_text().strip_edges().is_empty()
	if control is TextureRect:
		return (control as TextureRect).texture != null
	if control is ColorRect:
		return (control as ColorRect).color.a > 0.05
	if control is Range:
		return true
	if control is Panel or control is PanelContainer:
		var box := control.get_theme_stylebox(&"panel")
		return box != null and not box is StyleBoxEmpty
	return false


func _rids(body: Node3D) -> Array[RID]:
	var rids: Array[RID] = []
	if body is CollisionObject3D:
		rids.append((body as CollisionObject3D).get_rid())
	return rids


func _turn(body: Node3D, camera: Camera3D, yaw: float, pitch: float) -> void:
	var yaw_step := clampf(wrapf(yaw - body.global_rotation.y, -PI, PI) * 0.18, -TURN, TURN)
	var head := camera.get_parent() as Node3D
	var tilt := head if head != null and head != body else camera
	var pitch_step := clampf((pitch - tilt.rotation.x) * 0.18, -TURN, TURN)
	if body.has_method(&"look"):
		body.call(&"look", Vector2(yaw_step, pitch_step))
		return
	body.rotation.y = wrapf(body.rotation.y + yaw_step, -PI, PI)
	tilt.rotation.x = clampf(tilt.rotation.x + pitch_step, PITCH_LOW, PITCH_HIGH)
