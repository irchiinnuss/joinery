extends RefCounted
## A seeded minute of the top-down blueprint as the playtest report has it: its coverage
## ("#" reached, "." never reached, " " not floor) with its rooms by name, and the places the
## run was asked to reach, for the visual suite.

const GRID := [
	"                                ",
	"  ########....................  ",
	" ################.............  ",
	"  .##...........#.............  ",
	"  ..##..........#.............  ",
	"  ...##..  .....#....  .......  ",
	"  ....##.  .....#....  .......  ",
	"  .....##.......#.............  ",
	" #########..##########........  ",
	"  ..###....###################  ",
	"  ..#####.......#.##........#.  ",
	"  ########..#######........##.  ",
	"  ...##..  .....#....  ...##..  ",
	"  ..##...  .....#....  ####...  ",
	"  ..###############....####...  ",
	"  ..............#.#....##.##..  ",
	"  ..............#.############  ",
	"  ..............#.............  ",
	"                                ",
]


static func coverage() -> Dictionary:
	return {
		"percent": 34.0,
		"visited": 157,
		"navigable": 462,
		"start": [16, 9],
		"origin": [0.0, 0.0],
		"cell": 2.0,
		"grid": GRID,
		"areas":
		[
			{"name": "GreatHall", "rect": [1.0, 1.0, 15.0, 7.0]},
			{"name": "Archive", "rect": [16.0, 1.0, 15.0, 7.0]},
			{"name": "Corridor", "rect": [1.0, 8.0, 30.0, 2.0]},
			{"name": "Forge", "rect": [1.0, 10.0, 15.0, 8.0]},
			{"name": "Vault", "rect": [16.0, 10.0, 15.0, 8.0]},
		],
	}


## The places the run was asked to reach (world x, z; the grid's cells are 2 units).
static func reach() -> Array:
	return [
		{"target": "vaultkey", "node": "VaultKey", "reached": true, "t": 41.0, "at": [11.0, 27.0]},
		{"target": "exitswitch", "node": "ExitSwitch", "reached": false, "at": [57.0, 31.0]},
	]


## The last five runs' heat on that grid (joinery playtest --heatmap --json): deaths by the
## spikes in the east room, a stuck point in the doorway, most time in the corridor.
static func heat() -> Dictionary:
	var time: Array = []
	for row: int in GRID.size():
		var line: String = GRID[row]
		for col: int in line.length():
			if line[col] == "#":
				# Longest along the corridor (row 9), then in the rooms off it.
				time.append([col, row, 40.0 if row == 9 else 6.0 + (col * 7 + row * 3) % 11])
	return {
		"runs": 5,
		"grid": GRID,
		"deaths": [[26, 13, 4], [27, 12, 2], [5, 8, 1]],
		"stuck": [[16, 5, 2], [11, 11, 1]],
		"time": time,
	}


## The findings of that run: one of each kind of row (a serious one opens to its screenshot,
## [param shot]).
static func items(shot: String) -> Array:
	return [
		{
			"severity": "high",
			"kind": "softlock",
			"title": "Softlock",
			"detail": "The player couldn't move for 15 s while the bot held movement keys.",
			"where": "res://game/player/player.gd:88",
			"t": 65.0,
			"seed": 4821,
			"repro": "Replay seed 4821 (2:00 run); watch around 1:05 of play.",
			"shot": shot,
		},
		{
			"severity": "medium",
			"kind": "hitch",
			"title": "Frame hitches",
			"detail": "3 frames took far longer than usual (worst 310 ms; typical 2.10 ms).",
			"t": 4.0,
			"seed": 4821,
		},
		{
			"severity": "low",
			"kind": "warning",
			"title": "Warning",
			"detail": "The coin has no save_id, so saving forgets it.",
			"seed": 4821,
		},
		{
			"severity": "note",
			"kind": "balance",
			"title": "Balance",
			"detail": "The bot died or respawned 9 times in 2:00 without finishing.",
			"seed": 4821,
		},
	]


## The whole report of that run, as the host's event carries it.
static func report(shot: String) -> Dictionary:
	return {
		"counts": {"critical": 0, "high": 1, "medium": 1, "low": 1, "note": 1},
		"items": items(shot),
		# As JSON brings it: a float, shown as a whole number.
		"seed": 662040.0,
		"played_seconds": 120.0,
		"coverage": coverage(),
		"reach": reach(),
	}


## A 3D backyard as a three-minute run reported it (the case the card has to make plain): the
## level rendered from above under the grid, the bot's path, one stutter and nothing else.
## [param picture] is where the stand-in render is saved.
static func yard(picture: String) -> Dictionary:
	var image := Image.create(640, 555, false, Image.FORMAT_RGBA8)
	image.fill(Color("2a2c33"))
	image.fill_rect(Rect2i(85, 85, 512, 425), Color("3f6b3a"))
	image.fill_rect(Rect2i(85, 85, 213, 128), Color("7d7f83"))
	image.fill_rect(Rect2i(298, 85, 128, 128), Color("8d9299"))
	image.fill_rect(Rect2i(426, 85, 171, 128), Color("9aa0a8"))
	image.fill_rect(Rect2i(98, 235, 40, 235), Color("23402a"))
	image.fill_rect(Rect2i(85, 85, 512, 4), Color("c9c9c9"))
	image.fill_rect(Rect2i(85, 506, 512, 4), Color("c9c9c9"))
	image.fill_rect(Rect2i(85, 85, 4, 425), Color("c9c9c9"))
	image.fill_rect(Rect2i(593, 85, 4, 425), Color("c9c9c9"))
	image.save_png(picture)
	# Patio first, along the side path to the lobby corner, then back and down the lawn.
	var path: Array = []
	var stops := [
		[4.5, 3.3],
		[2.7, 2.4],
		[3.6, 4.6],
		[7.0, 2.6],
		[12.1, 3.7],
		[13.5, 5.6],
		[12.3, 7.7],
		[9.4, 7.9],
		[6.2, 6.0],
		[3.2, 5.6],
		[3.3, 9.5],
		[6.0, 11.2],
		[9.5, 10.2],
		[12.6, 9.0],
		[10.8, 6.2],
		[7.3, 5.6]
	]
	for index: int in stops.size() - 1:
		for step: int in 6:
			var a := Vector2(stops[index][0], stops[index][1])
			var b := Vector2(stops[index + 1][0], stops[index + 1][1])
			var at := a.lerp(b, step / 6.0)
			path.append([at.x, at.y])
	return {
		"counts": {"critical": 0, "high": 0, "medium": 1, "low": 1, "note": 0},
		"seed": 702378.0,
		"played_seconds": 180.0,
		"items":
		[
			{
				"severity": "medium",
				"kind": "hitch",
				"title": "Frame hitches",
				"detail": "6 frames took far longer than usual (worst 204 ms; typical 0.67 ms).",
				"t": 40.45,
				"seed": 702378,
				"repro": "Replay seed 702378 (3:00 run); watch around 0:40 of play.",
			},
			{
				"severity": "low",
				"kind": "text",
				"title": "Text doesn't fit",
				"detail": 'The pause menu\'s "Report a bug" runs past its button in German.',
				"seed": 702378,
				"repro": "Replay seed 702378 (3:00 run).",
			},
		],
		"coverage":
		{
			"mode": "3d",
			"percent": 87.8,
			"visited": 101,
			"navigable": 115,
			"start": [4, 3],
			"origin": [-4.0, -4.0],
			"cell": 2.0,
			"grid":
			[
				"               ",
				"               ",
				"  ############ ",
				"  ############ ",
				"  .#.#.###.### ",
				"  .########### ",
				"   ########### ",
				"   #####.##### ",
				"   ########### ",
				"   ########### ",
				"   #######.#.. ",
				"  .#######.... ",
				"               ",
			],
			"areas":
			[
				{"name": "Patio", "rect": [2.0, 2.0, 5.0, 3.0]},
				{"name": "SidePath", "rect": [7.0, 2.0, 3.0, 3.0]},
				{"name": "LobbyCorner", "rect": [10.0, 2.0, 4.0, 3.0]},
				{"name": "Lawn", "rect": [2.0, 5.0, 12.0, 7.0]},
			],
			"path": path,
			"picture": picture,
			"picture_rect": [0.0, 0.0, 15.0, 13.0],
		},
	}
