@tool
extends Control
## The playtest's coverage map, top-down. In 3D it is the level itself, rendered from straight
## above in the run's replay (bot_map.gd, the report's coverage "picture"): the places the
## player walked are washed in the accent colour, the bot's path is a line over them, and each
## place a player could stand but the bot never reached is a dot in the danger colour (walls,
## hedges and ground outside the level are never marked). Without a picture (2D, or a run
## where nothing could draw) the floor as the bot mapped it is drawn as cells instead, the
## reached ones in a quiet tone. Either way it fills the card's width (empty margins cropped),
## each named area carries its name, the run's start is a ring marked "Start" and each place
## the run was asked to reach is a diamond with its name, filled once reached. Drawn in the
## theme's colours, so it reads in light and dark; agents get the grid as coverage.png.
##
## Heat layers from the last few playtests (joinery/playtest_heat.py) paint on the same map:
## time spent as an accent wash (stronger where the bot stood longer), deaths as filled dots
## and stuck points as open rings, each sized by how often. They differ in shape as well as
## colour. While a layer is on, the unreached floor steps back to a faint tone so the heat
## reads first.

const UI_THEME := preload("ui_theme.gd")
## Width the map assumes before it is laid out, and the tallest it draws, in theme units.
const WIDTH_UNITS := 320.0
const HEIGHT_UNITS := 240.0
## How strong the unreached cells are: marked, without drowning the map in alarm.
const MISSED_ALPHA := 0.72
## The heat layers, in drawing order.
const LAYERS: Array[String] = ["time", "deaths", "stuck"]
## The unreached floor while a heat layer shows: floor only.
const HEAT_MISSED_ALPHA := 0.22
## Time spent: the faintest and strongest wash.
const TIME_ALPHA := Vector2(0.18, 0.85)
## Cells of floor kept round the cropped map.
const MARGIN := 1
## Over the level's picture: the wash on reached ground, and the path's strength.
const WALKED_ALPHA := 0.3
const PATH_ALPHA := 0.75

## The tallest the map may draw, in pixels (the card's room left); 0 for HEIGHT_UNITS.
var max_height := 0.0:
	set(value):
		max_height = value
		_fit()
		queue_redraw()

var _grid: PackedStringArray = PackedStringArray()
var _cols := 0
var _rows := 0
var _start := Vector2i(-1, -1)
## The part of the grid drawn (cells): the floor and the marks, a cell round them.
var _view := Rect2i()
## Named areas: {name, rect: Rect2 in cells}.
var _areas: Array[Dictionary] = []
## Places to reach: {name, at: Vector2 in cells, reached}.
var _marks: Array[Dictionary] = []
## Layer -> {Vector2i: value}.
var _heat: Dictionary = {}
var _on: Dictionary = {}
## The level from above (3D runs), and the part of the grid it shows (cells).
var _picture: Texture2D = null
var _picture_rect := Rect2()
## The bot's path, in cells.
var _path := PackedVector2Array()


func _init() -> void:
	name = "CoverageMap"
	mouse_filter = Control.MOUSE_FILTER_PASS
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Map of the level: reached and not reached"


## Show [param coverage] (the report's "coverage": {grid, cols, rows, start, percent, areas}).
func set_coverage(coverage: Dictionary) -> void:
	_grid = PackedStringArray()
	for line: Variant in coverage.get("grid", []):
		_grid.append(str(line))
	_rows = _grid.size()
	_cols = 0
	for line: String in _grid:
		_cols = maxi(_cols, line.length())
	var start: Variant = coverage.get("start", [-1, -1])
	if start is Array and (start as Array).size() == 2:
		_start = Vector2i(int(start[0]), int(start[1]))
	_areas.clear()
	for area: Variant in coverage.get("areas", []):
		var rect: Variant = (area as Dictionary).get("rect") if area is Dictionary else null
		if rect is Array and (rect as Array).size() == 4:
			var box := Rect2(float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
			_areas.append({"name": str((area as Dictionary).get("name", "")), "rect": box})
	_path = PackedVector2Array()
	for point: Variant in coverage.get("path", []):
		if point is Array and (point as Array).size() == 2:
			_path.append(Vector2(float(point[0]), float(point[1])))
	_load_picture(str(coverage.get("picture", "")), coverage.get("picture_rect"))
	_crop()
	_fit()
	queue_redraw()


## Places the run was asked to reach: [{name, at: [col, row] (cells, fractions kept), reached}].
func set_marks(marks: Array) -> void:
	_marks.clear()
	for mark: Variant in marks:
		if not mark is Dictionary:
			continue
		var at: Variant = (mark as Dictionary).get("at")
		if at is Array and (at as Array).size() == 2:
			(
				_marks
				. append(
					{
						"name": str((mark as Dictionary).get("name", "")),
						"at": Vector2(float(at[0]), float(at[1])),
						"reached": bool((mark as Dictionary).get("reached", false)),
					}
				)
			)
	_crop()
	_fit()
	queue_redraw()


## Whether the level's own picture is under the map (a 3D run that could draw).
func has_picture() -> bool:
	return _picture != null


func _load_picture(path: String, rect: Variant) -> void:
	_picture = null
	if path.is_empty() or not rect is Array or (rect as Array).size() != 4:
		return
	if not FileAccess.file_exists(path):
		return
	var image := Image.load_from_file(path)
	if image == null or image.is_empty():
		return
	_picture = ImageTexture.create_from_image(image)
	_picture_rect = Rect2(float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))


## The names on the map, as drawn: areas, then "Start", then the places to reach.
func label_words() -> PackedStringArray:
	var words := PackedStringArray()
	for area: Dictionary in _areas:
		words.append(spaced(str(area["name"])))
	if _start_shown():
		words.append("Start")
	for mark: Dictionary in _marks:
		words.append(spaced(str(mark["name"])))
	return words


## Seconds spent in each named area by the time layer, most first: [[name, seconds]].
func time_by_area() -> Array:
	var spent := {}
	var cells: Dictionary = _heat.get("time", {})
	for at: Vector2i in cells:
		var name := _area_at(Vector2(at) + Vector2(0.5, 0.5))
		if not name.is_empty():
			spent[name] = float(spent.get(name, 0.0)) + float(cells[at])
	var order: Array = []
	for name: String in spent:
		order.append([name, spent[name]])
	order.sort_custom(func(a: Array, b: Array) -> bool: return float(a[1]) > float(b[1]))
	return order


## "OreYard" -> "Ore Yard", "ore_yard" -> "ore yard": a node name as words.
static func spaced(node_name: String) -> String:
	var words := ""
	for index: int in node_name.length():
		var letter := node_name[index]
		if letter == "_" or letter == "-":
			words += " "
			continue
		var upper := letter != letter.to_lower()
		if index > 0 and upper and node_name[index - 1] == node_name[index - 1].to_lower():
			if node_name[index - 1] not in ["_", "-", " "]:
				words += " "
		words += letter
	return words


## Heat over recent runs ([code]joinery playtest --heatmap --json[/code]): each layer a list of
## [col, row, value] on this map's grid. A heatmap drawn on another grid is ignored.
func set_heat(heat: Dictionary) -> void:
	_heat.clear()
	var grid: Variant = heat.get("grid", [])
	if grid is Array and not (grid as Array).is_empty() and (grid as Array).size() != _rows:
		queue_redraw()
		return
	for layer: String in LAYERS:
		var cells := {}
		for entry: Variant in heat.get(layer, []):
			if entry is Array and (entry as Array).size() >= 3:
				var at := Vector2i(int(entry[0]), int(entry[1]))
				if at.x >= 0 and at.y >= 0 and at.x < _cols and at.y < _rows:
					cells[at] = float(cells.get(at, 0.0)) + float(entry[2])
		_heat[layer] = cells
	queue_redraw()


## Show or hide a heat layer ("time", "deaths" or "stuck").
func set_layer(layer: String, on: bool) -> void:
	_on[layer] = on
	queue_redraw()


func layer_on(layer: String) -> bool:
	return bool(_on.get(layer, false))


## Cells a heat layer marks.
func layer_cells(layer: String) -> int:
	return (_heat.get(layer, {}) as Dictionary).size()


## The layer's total: deaths or stuck points, or seconds spent.
func layer_total(layer: String) -> float:
	var total := 0.0
	for value: Variant in (_heat.get(layer, {}) as Dictionary).values():
		total += float(value)
	return total


## Cells shown: [reached, not reached].
func counts() -> Array[int]:
	var reached := 0
	var missed := 0
	for line: String in _grid:
		reached += line.count("#")
		missed += line.count(".")
	return [reached, missed]


func _notification(what: int) -> void:
	if what in [NOTIFICATION_THEME_CHANGED, NOTIFICATION_ENTER_TREE]:
		_fit()
		queue_redraw()
	elif what == NOTIFICATION_RESIZED:
		_fit()
		queue_redraw()


## The drawn part of the grid: every floor cell, the start and the marks, a cell round them.
func _crop() -> void:
	var low := Vector2i(_cols, _rows)
	var high := Vector2i(-1, -1)
	for row: int in _rows:
		var line := _grid[row]
		for col: int in line.length():
			if line[col] != " ":
				low = Vector2i(mini(low.x, col), mini(low.y, row))
				high = Vector2i(maxi(high.x, col), maxi(high.y, row))
	var extra: Array[Vector2i] = []
	if _start_shown():
		extra.append(_start)
	for mark: Dictionary in _marks:
		extra.append(Vector2i((mark["at"] as Vector2).floor()))
	for at: Vector2i in extra:
		if at.x >= 0 and at.y >= 0 and at.x < _cols and at.y < _rows:
			low = Vector2i(mini(low.x, at.x), mini(low.y, at.y))
			high = Vector2i(maxi(high.x, at.x), maxi(high.y, at.y))
	if high.x < 0:
		_view = Rect2i(0, 0, _cols, _rows)
		return
	low = Vector2i(maxi(low.x - MARGIN, 0), maxi(low.y - MARGIN, 0))
	high = Vector2i(mini(high.x + MARGIN, _cols - 1), mini(high.y + MARGIN, _rows - 1))
	_view = Rect2i(low, high - low + Vector2i.ONE)


func _start_shown() -> bool:
	return _start.x >= 0 and _start.y >= 0 and _start.x < _cols and _start.y < _rows


## As tall as the cropped floor at the width the card gives it, up to HEIGHT_UNITS.
func _fit() -> void:
	if _view.size.x <= 0 or _view.size.y <= 0 or not is_inside_tree():
		custom_minimum_size = Vector2.ZERO
		return
	var unit := UI_THEME.unit(self)
	var width := size.x if size.x > 1.0 else WIDTH_UNITS * unit
	var side := minf(width / _view.size.x, _tallest(unit) / _view.size.y)
	var height := ceilf(side * _view.size.y)
	if not is_equal_approx(custom_minimum_size.y, height):
		custom_minimum_size = Vector2(0, height)


func _tallest(unit: float) -> float:
	var tallest := HEIGHT_UNITS * unit
	return minf(tallest, max_height) if max_height > 0.0 else tallest


## Pixels a cell takes now, and where the cropped grid's corner is drawn (centred across).
func _layout() -> Array:
	var unit := UI_THEME.unit(self)
	var side := minf(size.x / _view.size.x, minf(size.y, _tallest(unit)) / _view.size.y)
	var offset := Vector2(maxf((size.x - side * _view.size.x) / 2.0, 0.0), 0.0)
	return [side, offset]


## Whether a heat layer with anything on it shows.
func heat_shown() -> bool:
	for layer: String in LAYERS:
		if layer_on(layer) and layer_cells(layer) > 0:
			return true
	return false


func _draw() -> void:
	if _view.size.x <= 0 or _view.size.y <= 0:
		return
	var unit := UI_THEME.unit(self)
	var laid := _layout()
	var side := float(laid[0])
	var offset: Vector2 = laid[1]
	if side <= 0.0:
		return
	var heat := heat_shown()
	var reached := UI_THEME.color(self, "text_muted")
	reached.a = 0.32
	var missed := UI_THEME.color(self, "danger")
	missed.a = MISSED_ALPHA
	if heat:
		# The heat reads first: unreached floor is only floor now.
		missed = UI_THEME.color(self, "text_faint")
		missed.a = HEAT_MISSED_ALPHA
	var gap := 1.0 if side >= 4.0 else 0.0
	if _picture != null:
		_draw_level(side, offset, unit, missed, heat)
	else:
		for row: int in range(_view.position.y, _view.end.y):
			var line := _grid[row]
			for col: int in range(_view.position.x, mini(_view.end.x, line.length())):
				var mark := line[col]
				if mark == " ":
					continue
				var box := Rect2(
					_point(Vector2(col, row), side, offset), Vector2.ONE * (side - gap)
				)
				draw_rect(box, missed if mark == "." else reached)
		_draw_path(side, offset, unit)
	if layer_on("time"):
		_draw_time(side, offset, gap)
	if layer_on("deaths"):
		_draw_marks("deaths", side, offset, unit)
	if layer_on("stuck"):
		_draw_marks("stuck", side, offset, unit)
	var taken: Array[Rect2] = []
	if _start_shown():
		var centre := _point(Vector2(_start) + Vector2(0.5, 0.5), side, offset)
		var radius := maxf(side * 0.7, 4.0 * unit)
		draw_circle(centre, radius + maxf(1.0, unit), UI_THEME.color(self, "surface"), true)
		draw_arc(
			centre, radius, 0.0, TAU, 24, UI_THEME.color(self, "text"), maxf(1.5, unit * 2.0), true
		)
		_label("Start", centre + Vector2(0, radius + unit), side, taken, "text", true)
		taken.append(Rect2(centre - Vector2.ONE * radius, Vector2.ONE * radius * 2.0))
	for mark: Dictionary in _marks:
		var centre := _point(mark["at"] as Vector2, side, offset)
		var radius := maxf(side * 0.8, 5.0 * unit)
		_diamond(centre, radius, bool(mark["reached"]), unit)
		_label(
			spaced(str(mark["name"])), centre + Vector2(0, radius + unit), side, taken, "text", true
		)
		taken.append(Rect2(centre - Vector2.ONE * radius, Vector2.ONE * radius * 2.0))
	# Area names last, biggest first, where they don't cover a mark or another name.
	var order := _areas.duplicate()
	order.sort_custom(
		func(a: Dictionary, b: Dictionary) -> bool:
			return (a["rect"] as Rect2).get_area() > (b["rect"] as Rect2).get_area()
	)
	for area: Dictionary in order:
		var rect: Rect2 = area["rect"]
		var centre := _point(rect.get_center(), side, offset)
		_label(spaced(str(area["name"])), centre, side, taken, "text_muted", false)


## The level's picture under the cropped grid, the walked ground washed, the path over it,
## and a dot on each place a player could stand that the bot never reached.
func _draw_level(side: float, offset: Vector2, unit: float, missed: Color, heat: bool) -> void:
	var shown := Rect2(Vector2(_view.position), Vector2(_view.size))
	var part := shown.intersection(_picture_rect)
	if part.has_area():
		var texels := Vector2(_picture.get_size()) / _picture_rect.size
		var source := Rect2((part.position - _picture_rect.position) * texels, part.size * texels)
		var target := Rect2(_point(part.position, side, offset), part.size * side)
		draw_texture_rect_region(_picture, target, source)
	var walked := UI_THEME.color(self, "accent")
	walked.a = 0.0 if heat else WALKED_ALPHA
	var halo := UI_THEME.color(self, "surface")
	var radius := clampf(side * 0.22, 2.0 * unit, 5.0 * unit)
	for row: int in range(_view.position.y, _view.end.y):
		var line := _grid[row]
		for col: int in range(_view.position.x, mini(_view.end.x, line.length())):
			var at := _point(Vector2(col, row), side, offset)
			if line[col] == "#" and walked.a > 0.0:
				draw_rect(Rect2(at, Vector2.ONE * side), walked)
			elif line[col] == ".":
				var centre := at + Vector2.ONE * side / 2.0
				draw_circle(centre, radius + maxf(1.0, unit), halo, true, -1.0, true)
				draw_circle(centre, radius, missed, true, -1.0, true)
	_draw_path(side, offset, unit)


## The bot's way through the level: a line in the accent colour, over a halo so it reads on
## the level's own colours.
func _draw_path(side: float, offset: Vector2, unit: float) -> void:
	if _path.size() < 2:
		return
	# A point every half cell at most: a long run reads as a route, not a scribble.
	var points := PackedVector2Array()
	for point: Vector2 in _path:
		var at := _point(point, side, offset)
		if points.is_empty() or points[-1].distance_to(at) >= side * 0.5:
			points.append(at)
	if points.size() < 2:
		return
	var ink := UI_THEME.color(self, "accent")
	ink.a = PATH_ALPHA
	var halo := UI_THEME.color(self, "surface")
	halo.a = 0.45
	var width := maxf(1.0, unit * 1.25)
	draw_polyline(points, halo, width + maxf(2.0, unit * 2.0), true)
	draw_polyline(points, ink, width, true)


func _point(cell: Vector2, side: float, offset: Vector2) -> Vector2:
	return offset + (cell - Vector2(_view.position)) * side


## A name on the map with a halo in the card's colour, so it reads over the floor and the
## heat. [param below]: [param at] is the top centre (under a mark), else the centre. Skipped
## when it would cover something already drawn or run off the map.
func _label(
	words: String, at: Vector2, side: float, taken: Array[Rect2], role: String, below: bool
) -> void:
	if words.is_empty():
		return
	var font := UI_THEME.medium_font(self) if below else UI_THEME.text_font(self)
	var font_size := UI_THEME.font_size(self, "caption")
	var extent := font.get_string_size(words, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size)
	var corner := at - Vector2(extent.x / 2.0, 0.0 if below else extent.y / 2.0)
	var box := Rect2(corner, extent)
	if box.position.x < 0.0 or box.end.x > size.x or box.position.y < 0.0 or box.end.y > size.y:
		if not below:
			return
		box.position.x = clampf(box.position.x, 0.0, maxf(size.x - extent.x, 0.0))
		box.position.y = clampf(box.position.y, 0.0, maxf(size.y - extent.y, 0.0))
	for other: Rect2 in taken:
		if other.intersects(box.grow(maxf(side * 0.25, 1.0))):
			return
	taken.append(box)
	var baseline := box.position + Vector2(0.0, font.get_ascent(font_size))
	var halo := UI_THEME.color(self, "surface")
	halo.a = 0.85
	var outline := maxi(2, roundi(UI_THEME.unit(self) * 4.0))
	draw_string_outline(
		font, baseline, words, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size, outline, halo
	)
	draw_string(
		font, baseline, words, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size, UI_THEME.color(self, role)
	)


## A place to reach: a diamond in the accent colour, filled once the run got there.
func _diamond(centre: Vector2, radius: float, filled: bool, unit: float) -> void:
	var points := PackedVector2Array(
		[
			centre + Vector2(0, -radius),
			centre + Vector2(radius, 0),
			centre + Vector2(0, radius),
			centre + Vector2(-radius, 0),
		]
	)
	var ink := UI_THEME.color(self, "accent")
	var halo := UI_THEME.color(self, "surface")
	var width := maxf(1.5, unit * 2.0)
	var ring := points.duplicate()
	ring.append(points[0])
	draw_polyline(ring, halo, width + maxf(2.0, unit * 2.0), true)
	draw_colored_polygon(points, ink if filled else halo)
	draw_polyline(ring, ink, width, true)


## The smallest named area holding [param cell] (cells, fractions kept), or "".
func _area_at(cell: Vector2) -> String:
	var best := ""
	var smallest := INF
	for area: Dictionary in _areas:
		var rect: Rect2 = area["rect"]
		if rect.has_point(cell) and rect.get_area() < smallest:
			best = str(area["name"])
			smallest = rect.get_area()
	return best


func _draw_time(side: float, offset: Vector2, gap: float) -> void:
	var cells: Dictionary = _heat.get("time", {})
	var most := 0.0
	for value: Variant in cells.values():
		most = maxf(most, float(value))
	if most <= 0.0:
		return
	var ink := UI_THEME.color(self, "accent")
	for at: Vector2i in cells:
		if not _view.has_point(at):
			continue
		# Square root: a few long waits don't wash out everywhere else.
		var share := sqrt(float(cells[at]) / most)
		ink.a = lerpf(TIME_ALPHA.x, TIME_ALPHA.y, share)
		draw_rect(Rect2(_point(Vector2(at), side, offset), Vector2.ONE * (side - gap)), ink)


## Deaths: filled dots. Stuck: open rings. Bigger where it happened more often.
func _draw_marks(layer: String, side: float, offset: Vector2, unit: float) -> void:
	var cells: Dictionary = _heat.get(layer, {})
	var most := 1.0
	for value: Variant in cells.values():
		most = maxf(most, float(value))
	var smallest := maxf(side * 0.28, 2.5 * unit)
	var largest := maxf(side * 0.62, smallest + unit)
	var ink := UI_THEME.color(self, "danger" if layer == "deaths" else "text")
	var halo := UI_THEME.color(self, "surface")
	for at: Vector2i in cells:
		var centre := _point(Vector2(at) + Vector2(0.5, 0.5), side, offset)
		var radius := lerpf(smallest, largest, sqrt(float(cells[at]) / most))
		if layer == "deaths":
			# A thin halo keeps a dot apart from the wash under it.
			draw_circle(centre, radius + maxf(1.0, unit), halo, true, -1.0, true)
			draw_circle(centre, radius, ink, true, -1.0, true)
		else:
			var width := maxf(1.5, unit * 1.75)
			draw_arc(centre, radius, 0.0, TAU, 24, halo, width + maxf(1.0, unit) * 2.0, true)
			draw_arc(centre, radius, 0.0, TAU, 24, ink, width, true)
