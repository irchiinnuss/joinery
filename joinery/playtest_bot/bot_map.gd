extends RefCounted
## The level as the playtest card's map shows it (3D): a render from straight above, like a floor
## plan, made once in the windowed replay as soon as the coverage grid is built. The card lays the
## grid (reached, not reached), the start and the player's path over it. Saved as level.png in the
## run's shots folder, with level.json giving the ground rectangle it shows in world units.
##
## It renders in a SubViewport of its own that shares the game's 3D world, so the game's HUD and
## menus are not in it and the game's own camera is untouched. Headless runs draw nothing, so
## there the card falls back to the grid alone.

## The picture's width in pixels; its height follows the grid's shape.
const WIDTH := 640
## How far above the player's floor the view starts: roofs, ceilings and canopies above that are
## cut away, as in a floor plan.
const CUT_ABOVE := 2.4
## Frames the view renders before it is read.
const SETTLE := 4

## True once the picture is saved, or there is nothing to draw.
var done: bool = false

var _view: SubViewport = null
var _frames: int = 0
var _out: String = ""
var _rect: Rect2 = Rect2()


## Starts the render: [param cover] (bot_cover.gd) gives the grid's ground rectangle and
## [param player] the floor height to cut above. Does nothing (and is done) outside 3D.
func start(root: Window, cover: RefCounted, player: Node, out_dir: String) -> void:
	if _view != null or done:
		return
	if not player is Node3D or str(cover.get(&"mode")) != "3d" or out_dir.is_empty():
		done = true
		return
	var cell := float(cover.get(&"cell"))
	var origin: Vector2 = cover.get(&"origin")
	var span := Vector2(int(cover.get(&"cols")), int(cover.get(&"rows"))) * cell
	if span.x <= 0.0 or span.y <= 0.0:
		done = true
		return
	var height := clampi(roundi(WIDTH * span.y / span.x), 16, 1280)
	_rect = Rect2(origin, Vector2(span.x, span.x * height / float(WIDTH)))
	_out = out_dir
	_view = SubViewport.new()
	_view.name = "JoineryLevelView"
	_view.size = Vector2i(WIDTH, height)
	_view.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	_view.msaa_3d = Viewport.MSAA_4X
	var camera := Camera3D.new()
	camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	camera.keep_aspect = Camera3D.KEEP_WIDTH
	camera.size = span.x
	camera.near = 0.05
	camera.far = 500.0
	var centre := _rect.get_center()
	camera.position = Vector3(centre.x, (player as Node3D).global_position.y + CUT_ABOVE, centre.y)
	# Looking straight down with -z up the picture: rows of the grid run down it, as on the map.
	camera.rotation = Vector3(-PI / 2.0, 0.0, 0.0)
	_view.add_child(camera)
	root.add_child(_view)
	camera.current = true


## One frame: once the view has rendered a few, saves it and frees it.
func step() -> void:
	if done or _view == null:
		return
	_frames += 1
	if _frames < SETTLE:
		return
	done = true
	var image := _view.get_texture().get_image()
	if image != null and not image.is_empty():
		if image.save_png(_out.path_join("level.png")) == OK:
			_save_rect(_out.path_join("level.json"))
	_view.queue_free()
	_view = null


## The ground rectangle the picture shows, in world units: {origin: [x, z], size: [w, h]}.
func _save_rect(path: String) -> void:
	var shown := {
		"origin": [snappedf(_rect.position.x, 0.01), snappedf(_rect.position.y, 0.01)],
		"size": [snappedf(_rect.size.x, 0.01), snappedf(_rect.size.y, 0.01)],
	}
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify(shown))
