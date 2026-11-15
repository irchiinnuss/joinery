extends RefCounted
## The visual suite's check cards: Make it match, the design check, accessibility and
## performance, and the sound pass. [param suite] is visual_sidebar_test.gd, whose `_shot` and
## `_check` these use.

const FEATURE_BRIEF := """# Game brief — Lamplight

## 1. Pitch
A keeper fixes the harbour lights before the storm.

## 18. Features
- Lamps light up (test: res://tests/test_lamp.gd)
- The keeper reaches the lighthouse (reach: lighthouse)
- The harbour has a crane (reach: crane)
- Fishing for fun

## 19. Keep
- Jump height 1.2 m

## 20. Avoid
- Guns (gun, rifle, pistol)
"""


## Make it match: the scores and the top findings, then its fixes running as tasks.
static func match_shots(suite: Variant, light: bool, width: int, chat: Control) -> void:
	var tasks: Node = chat.get("_tasks")
	var fixture: GDScript = load(
		(suite.get_script() as Script).resource_path.get_base_dir().path_join("match_fixture.gd")
	)
	var matcher: Node = tasks.get_node("Match")
	matcher.set(
		"audit_runner", func(_args: PackedStringArray) -> Dictionary: return fixture.call("audit")
	)
	matcher.call("open")
	var card: Control = tasks.get("match_card")
	suite._check(card != null, "The match card shows")
	await suite._shot("match", light, width, chat)
	var rows: Array = fixture.call("run_rows")
	card.call("apply_run", {"state": "fixing", "tasks": rows})
	var apps := {"palette": ["krita"], "lighting": ["godot"], "sound": ["supercollider"]}
	for row: Dictionary in rows:
		var event := {"kind": "task", "task_id": row["task_id"], "title": row["title"]}
		event.merge({"state": "working", "apps": apps[row["area"]], "origin": "match"})
		tasks.call("on_event", event)
		if row["state"] == "in_review":
			event.merge({"state": "in_review", "transaction_id": "fixture"}, true)
			tasks.call("on_event", event)
	await suite._shot("match-fixing", light, width, chat)
	for row: Dictionary in rows:
		tasks.call("_remove", row["task_id"])
	card.free()
	tasks.set("match_card", null)
	tasks.call("sync_visible")


## The design check: its findings at a glance, the agent reading, and a sound game.
static func design(suite: Variant, light: bool, width: int, chat: Control) -> void:
	var tasks: Node = chat.get("_tasks")
	var fixture: GDScript = load(
		(suite.get_script() as Script).resource_path.get_base_dir().path_join("design_fixture.gd")
	)
	var designer: Node = tasks.get_node("Design")
	designer.set(
		"check_runner", func(_args: PackedStringArray) -> Dictionary: return fixture.call("report")
	)
	designer.call("open")
	var card: Control = tasks.get("design_card")
	suite._check(card != null, "The design card shows")
	await suite._shot("design", light, width, chat)
	card.call("show_report", fixture.call("report", true), false)
	card.call("set_reading")
	await suite._shot("design-reading", light, width, chat)
	card.call("show_report", fixture.call("clean"), true)
	await suite._shot("design-clean", light, width, chat)
	card.free()
	tasks.set("design_card", null)
	tasks.call("sync_visible")


## /tidy: what the game never uses at a glance, every row, the removal sent to Review, and a
## project with nothing unused.
static func tidy(suite: Variant, light: bool, width: int, chat: Control) -> void:
	var tasks: Node = chat.get("_tasks")
	var fixture: GDScript = load(
		(suite.get_script() as Script).resource_path.get_base_dir().path_join("tidy_fixture.gd")
	)
	var tidier: Node = tasks.get_node("Tidy")
	tidier.set(
		"runner", func(_args: PackedStringArray) -> Dictionary: return fixture.call("report")
	)
	tidier.call("open")
	var card: Control = tasks.get("tidy_card")
	suite._check(card != null, "The tidy card shows")
	await suite._shot("tidy", light, width, chat)
	card.call("_show_all")
	var first: Control = (card.call("finding_rows") as Array)[0]
	for button: Node in first.find_children("", "Button", true, false):
		if (button as Button).toggle_mode:
			(button as Button).button_pressed = true
	await suite._shot("tidy-all", light, width, chat)
	card.call("set_sent", ["a1", "a2", "a3", "a4", "a5", "a6", "a7"])
	await suite._shot("tidy-sent", light, width, chat)
	card.call("show_report", fixture.call("clean"))
	await suite._shot("tidy-clean", light, width, chat)
	card.free()
	tasks.set("tidy_card", null)
	tasks.call("sync_visible")


## The accessibility and performance checks: each card at a glance, the fixes as tasks.
static func check_cards(suite: Variant, light: bool, width: int, chat: Control) -> void:
	var tasks: Node = chat.get("_tasks")
	var fixture: GDScript = load(
		(suite.get_script() as Script).resource_path.get_base_dir().path_join("checks_fixture.gd")
	)
	for check: String in ["access", "perf"]:
		var checker: Node = tasks.get_node("Access" if check == "access" else "Perf")
		checker.set(
			"audit_runner", func(_args: PackedStringArray) -> Dictionary: return fixture.call(check)
		)
		checker.call("open")
		var card: Control = tasks.get("%s_card" % check)
		suite._check(card != null, "The %s card shows" % check)
		await suite._shot(check, light, width, chat)
		if check == "access":
			(card.get("guidelines") as Control).call("set_open", true)
			await suite._shot("access-guidelines", light, width, chat)
			var rows: Array = fixture.call("run_rows")
			card.call("apply_run", {"state": "fixing", "tasks": rows})
			for row: Dictionary in rows:
				var event := {"kind": "task", "task_id": row["task_id"], "title": row["title"]}
				event.merge({"state": "working", "apps": ["godot"], "origin": "user"})
				tasks.call("on_event", event)
			await suite._shot("access-fixing", light, width, chat)
			for row: Dictionary in rows:
				tasks.call("_remove", row["task_id"])
		card.free()
		tasks.set("%s_card" % check, null)
		tasks.call("sync_visible")


## The sound pass: what the level is missing, its rows following the sounds being made.
static func sound_pass(suite: Variant, light: bool, width: int, chat: Control) -> void:
	var tasks: Node = chat.get("_tasks")
	var sounder: Node = tasks.get_node("SoundPass")
	sounder.call("_open")
	var card: Control = tasks.get("sound_card")
	suite._check(card != null, "The sound pass card shows")
	var fixture: GDScript = load(
		(suite.get_script() as Script).resource_path.get_base_dir().path_join(
			"sound_pass_fixture.gd"
		)
	)
	card.call("apply", fixture.call("making"))
	await suite._shot("sound-pass", light, width, chat)
	card.free()
	tasks.set("sound_card", null)
	tasks.call("sync_visible")


## The brief's features marked from evidence on the /brief page; /cut's polish list, cuts and
## ship estimate, and once written; and a change in review that crosses a Keep line.
static func brief_and_cut(suite: Variant, light: bool, width: int, chat: Control) -> void:
	var dialogs: Script = load("res://addons/joinery/art_review_dialog.gd")
	dialogs.call("show_document", chat, "Game brief", FEATURE_BRIEF, "Change the game brief: ")
	var page: Control = chat.get_meta("joinery_art_page")
	var marks: GDScript = load(
		(suite.get_script() as Script).resource_path.get_base_dir().path_join(
			"brief_status_test.gd"
		)
	)
	page.call("show_feature_status", marks.get_script_constant_map()["REPORT"])
	await suite._shot("brief-status", light, width, chat)
	page.call("set_open", false)

	var tasks: Node = chat.get("_tasks")
	var cards: GDScript = load(
		(suite.get_script() as Script).resource_path.get_base_dir().path_join("cut_card_test.gd")
	)
	var cutter: Node = tasks.get_node("Cut")
	cutter.set("runner", func(_args: PackedStringArray) -> Dictionary: return cards.call("report"))
	cutter.call("open")
	var card: Control = tasks.get("cut_card")
	suite._check(card != null, "The cut card shows")
	await suite._shot("cut", light, width, chat)
	var scroll := card.get("_scroll") as ScrollContainer
	scroll.scroll_vertical = int(scroll.get_v_scroll_bar().max_value)
	await suite._shot("cut-cuts", light, width, chat)
	card.call("set_sent", ["c1", "c2"])
	await suite._shot("cut-sent", light, width, chat)
	card.free()
	tasks.set("cut_card", null)
	tasks.call("sync_visible")

	# A change that moves a pinned Keep value: the review names the line.
	var host := Control.new()
	host.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	chat.get_parent().add_child(host)
	var ground := ColorRect.new()
	ground.color = Color("f7f6f3") if light else Color("202123")
	ground.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	host.add_child(ground)
	var panel: Control = load("res://addons/joinery/review_panel.gd").new()
	panel.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	host.add_child(panel)
	(
		panel
		. call(
			"show_file_review",
			"Make the jump higher",
			(
				"--- player/player.gd\n+++ player/player.gd\n@@ -3 +3 @@\n"
				+ "-@export var jump_height := 1.2\n+@export var jump_height := 1.8"
			),
			[],
			[{"path": "player/player.gd", "change": "modified"}],
		)
	)
	(
		panel
		. call(
			"show_glance",
			{
				"facts": ["jump higher"],
				"changes": [{"path": "player/player.gd", "change": "modified"}],
				"checks": {"ran": ["Godot import", "tests"], "skipped": []},
				"review_notes":
				[
					{
						"severity": "warn",
						"rule": "brief",
						"file": "player/player.gd",
						"text":
						(
							"Crosses the brief's Keep line “Jump height 1.2 m”: it moves "
							+ "jump_height from 1.2 to 1.8 in player/player.gd."
						),
					},
				],
			}
		)
	)
	await suite._shot("review-keep-avoid", light, width, host)
	host.free()
