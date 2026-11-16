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
			"title": "Softlock",
			"detail": "The player couldn't move for 15 s while the bot held movement keys.",
			"where": "res://game/player/player.gd:88",
			"t": 65.0,
			"seed": 4821,
			"repro": "Run `joinery playtest --project . --seed 4821 --seconds 120`.",
			"shot": shot,
		},
		{
			"severity": "medium",
			"title": "Frame hitches",
			"detail": "3 frames took far longer than usual (worst 310 ms; typical 2.10 ms).",
			"t": 4.0,
			"seed": 4821,
		},
		{
			"severity": "low",
			"title": "Warning",
			"detail": "20 ObjectDB instances were leaked at exit.",
			"seed": 4821,
		},
		{
			"severity": "note",
			"title": "Balance",
			"detail": "The bot died or respawned 9 times in 2:00 without finishing.",
			"seed": 4821,
		},
	]
