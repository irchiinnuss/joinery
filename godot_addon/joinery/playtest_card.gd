@tool
extends PanelContainer
## The playtest card (ADR 0038), above the footer with the other agents at work. It says in
## plain words what ran: while the bot plays, "A bot is playing Yard Day… 0:42 / 3:00" with its
## own Stop; once the run is over, what the bot did ("A bot played Yard Day for 3:00, looking
## for crashes, errors and places it can't reach. It changed nothing."), what it found ("Found
## 2 things to look at"; with more findings than show at once, one count per kind in plain
## words, "1 bug · 2 to check · 3 minor"), and each finding as a row that opens to why it
## matters, what happened, the place in the code, the seed and a screenshot, with "Fix this" to
## put a plain request in the composer (only the user sends it). Under the findings, how much of
## the level the bot reached ("Reached 88% of the walkable level") over a top-down map: in 3D
## the level itself seen from above with the bot's path and the places it never got to
## (playtest_map.gd). Under the map, one toggle per heat layer from the last few playtests
## (joinery/playtest_heat.py): Deaths, Stuck and Time spent paint on the same map. The rows are
## never in a scroll: the map takes the room that is left, so nothing is ever drawn cut off
## under the counts. The playtester's written report goes to the transcript (sidebar_tasks.gd).

signal stop_pressed
signal close_pressed
## "Fix this" on a finding: [param request] is the request to pre-fill.
signal fix_requested(request: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const BLOCKS := preload("ui_blocks.gd")
const STYLE := preload("sidebar_style.gd")
const INDICATOR := preload("activity_indicator.gd")
const MAP := preload("playtest_map.gd")
const SEVERITIES: Array[String] = ["critical", "high", "medium", "low", "note"]
## Severities whose row leads with the finding's kind ("Softlock · 0:05"); the rest lead with
## what was found, on one line.
const HEADLINED: Array[String] = ["critical", "high"]
## Each severity in plain words: [one, many].
const SEVERITY_WORDS := {
	"critical": ["serious problem", "serious problems"],
	"high": ["bug", "bugs"],
	"medium": ["to check", "to check"],
	"low": ["minor", "minor"],
	"note": ["hint", "hints"],
}
## What a finding of each kind means for a player, said when its row opens.
const WHY := {
	"crash": "The game closed with an error. A player would lose what they were doing.",
	"freeze": "The game stopped answering. A player would have to force it to close.",
	"boot": "The game didn't start, so nothing else could be checked.",
	"script_error": "A script stopped with an error, so what it was doing didn't happen.",
	"shader_error": "A shader didn't compile, so something draws wrong or not at all.",
	"softlock": "The player got stuck with no way on. A player would have to restart.",
	"fell": "The player fell out of the level and wasn't brought back.",
	"physics": "The player was flung or ended up somewhere impossible.",
	"save": "Saving or loading lost something a player expects to keep.",
	"quit": "The game closed by itself while it was being played.",
	"error": "Godot reported an error while the game ran. A player may or may not notice.",
	"pause": "Pausing didn't stop the game.",
	"hitch": "A few frames took far longer than the rest: the game stutters there.",
	"memory": "Memory or nodes kept growing. A long session may slow down or run out.",
	"warning": "Godot warned about something in the game. Often harmless; worth a look.",
	"text": "Some text doesn't fit where it is shown, in at least one language.",
	"balance": "A hint from how the bot played, not a bug. A person may play differently.",
	"coverage": "Places the bot never got to: they may be hard to find or to reach.",
	"joinery": "A problem in Joinery's own playtest, not in your game.",
}
## What the bot looks for, as the card says it.
const LOOKS_FOR := "looking for crashes, errors and places it can't reach"
## The map never squeezes below this many theme units, nor takes more than this share of the
## window with the rest of the card.
const MAP_MIN_UNITS := 140.0
const MAX_SHARE := 0.4
## Areas named in the time-spent line.
const TIME_AREAS := 3
## Severity -> status dot state: bugs in the danger colour, the rest quiet.
const DOTS := {
	"critical": "attention",
	"high": "attention",
	"medium": "working",
	"low": "off",
	"note": "off",
}
const STEPS := {
	"starting": "Starting the playtest…",
	"playing": "A bot is playing %s…",
	"writing": "Writing up what it found…",
}
## Findings shown before "Show all".
const FIRST_ITEMS := 3
## Heat layer -> [toggle words, what it shows]; deaths and stuck start on when they have any.
const LAYERS := {
	"deaths": ["Deaths", "Where the bot died or was respawned"],
	"stuck": ["Stuck", "Where the bot got stuck: softlocks and falls out of the world"],
	"time": ["Time spent", "Where the bot spent its time: the stronger, the longer"],
}

var playtest_id := ""
## starting, playing, writing, done, failed or stopped.
var state := "starting"
var report: Dictionary = {}
var title_label: Label
var stop: Button
var close: Button
var _dots: Control
var _status: TextureRect
var _step: Label
var _timer: Label
var _about: Label
var _counts: HFlowContainer
var _items: VBoxContainer
var _body: VBoxContainer
var _more: Button
var _coverage: VBoxContainer
var _coverage_text: Label
var _map: Control
var _layers: HFlowContainer
var _layer_buttons: Dictionary = {}
var _runs_text: Label
## The legend's "not reached" square and words: they follow the map's unreached tone, and
## show only when some floor was not reached.
var _missed_key: ColorRect
## The legend's path key, shown when the map draws the bot's path.
var _path_key: HBoxContainer
var _missed_words: Label
## Where the time went, by area: shown while the time layer is on.
var _time_text: Label
var _seconds := 0.0


func _init(identifier := "") -> void:
	playtest_id = identifier
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	accessibility_name = "Playtest"
	STYLE.task_card(self)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	add_child(column)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	column.add_child(head)
	title_label = STYLE.card_title("Playtest")
	title_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(title_label)
	_timer = UI.label("", "JoineryNumeric")
	_timer.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_timer.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_timer.accessibility_name = "Time played"
	_timer.hide()
	head.add_child(_timer)
	stop = _small_icon("stop", "Stop the playtest")
	# Framed like every other Stop (the message box's, a task's), so it reads as a button.
	stop.theme_type_variation = "JoineryStopButton"
	stop.pressed.connect(_on_stop)
	head.add_child(stop)
	close = _small_icon("close", "Dismiss the playtest")
	close.pressed.connect(func() -> void: close_pressed.emit())
	close.hide()
	head.add_child(close)
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRowTight"
	UI.bind_height(line, "xs", false)
	column.add_child(line)
	_dots = INDICATOR.new()
	_dots.set("running", true)
	line.add_child(_dots)
	_status = UI.status_icon("done")
	_status.hide()
	line.add_child(_status)
	_step = UI.label(STEPS["starting"], "JoineryCaption")
	_step.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_step.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_step.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	line.add_child(_step)
	# What ran and what it means, in a sentence that wraps: never squeezed into the status line.
	_about = UI.label("", "JoineryCaption", true)
	_about.accessibility_name = "What the playtest did"
	_about.hide()
	column.add_child(_about)
	# The counts wrap in a narrow sidebar rather than push the card past its edge.
	_counts = HFlowContainer.new()
	STYLE.on_theme_overrides(
		_counts,
		func() -> void:
			_counts.add_theme_constant_override("h_separation", UI_THEME.space(_counts, 2))
			_counts.add_theme_constant_override("v_separation", 0)
	)
	_counts.accessibility_name = "Findings by kind"
	_counts.hide()
	column.add_child(_counts)
	_body = VBoxContainer.new()
	_body.theme_type_variation = "JoineryTight"
	_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_body.hide()
	column.add_child(_body)
	_coverage = VBoxContainer.new()
	_coverage.theme_type_variation = "JoineryTight"
	_coverage.accessibility_name = "How much of the level was reached"
	_coverage.hide()
	_body.add_child(_coverage)
	var legend := HFlowContainer.new()
	STYLE.on_theme_overrides(
		legend,
		func() -> void:
			legend.add_theme_constant_override("h_separation", UI_THEME.space(legend, 1))
			legend.add_theme_constant_override("v_separation", 0)
	)
	_coverage.add_child(legend)
	_coverage_text = UI.label("", "JoineryCaption")
	_coverage_text.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_coverage_text.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	legend.add_child(_coverage_text)
	# Each key sits with the words it explains, and wraps with them.
	var missed := HBoxContainer.new()
	missed.theme_type_variation = "JoineryRowTight"
	legend.add_child(missed)
	_missed_key = _swatch("danger", MAP.MISSED_ALPHA)
	_missed_key.theme_changed.connect(_sync_key)
	# Over the level's picture the unreached places are dots: so is their key.
	_missed_key.draw.connect(
		func() -> void:
			if _missed_key.has_meta("dot"):
				var side := minf(_missed_key.size.x, _missed_key.size.y) / 2.0
				var ink: Color = _missed_key.get_meta("dot")
				_missed_key.draw_circle(_missed_key.size / 2.0, side, ink, true, -1.0, true)
	)
	missed.add_child(_missed_key)
	_missed_words = UI.label("not reached", "JoineryCaption")
	_missed_words.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_missed_words.tooltip_text = "Places a player could stand that the bot never got to"
	missed.add_child(_missed_words)
	_path_key = HBoxContainer.new()
	_path_key.theme_type_variation = "JoineryRowTight"
	_path_key.hide()
	legend.add_child(_path_key)
	var line_key := _swatch("accent", MAP.PATH_ALPHA)
	STYLE.on_theme(
		line_key,
		func() -> void:
			var unit := UI_THEME.unit(line_key)
			line_key.custom_minimum_size = Vector2(12.0 * unit, maxf(2.0, 2.0 * unit))
	)
	_path_key.add_child(line_key)
	_path_key.add_child(UI.label("the bot's path", "JoineryCaption"))
	_map = MAP.new()
	_coverage.add_child(_map)
	column.minimum_size_changed.connect(_fit_map)
	# The layer toggles and "last N playtests" flow onto a second line in a narrow sidebar.
	_layers = HFlowContainer.new()
	STYLE.on_theme_overrides(
		_layers,
		func() -> void:
			_layers.add_theme_constant_override("h_separation", UI_THEME.space(_layers, 2))
			_layers.add_theme_constant_override("v_separation", UI_THEME.space(_layers, 1))
	)
	_layers.accessibility_name = "Heat layers on the map"
	_layers.hide()
	_coverage.add_child(_layers)
	for layer: String in LAYERS:
		var toggle := _layer_toggle(layer)
		_layer_buttons[layer] = toggle
		_layers.add_child(toggle)
	_runs_text = UI.label("", "JoineryCaption")
	_runs_text.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_runs_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_runs_text.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	# Whole, on a line of its own when the toggles leave no room.
	_runs_text.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	_layers.add_child(_runs_text)
	_time_text = UI.label("", "JoineryCaption", true)
	_time_text.accessibility_name = "Where the time went"
	_time_text.hide()
	_coverage.add_child(_time_text)
	_items = VBoxContainer.new()
	_items.theme_type_variation = "JoineryTight"
	_items.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	# What was found reads first; the map of where the bot went follows it.
	_body.add_child(_items)
	_body.move_child(_items, 0)
	_more = UI.quiet_link("")
	UI.bind_height(_more, "xs")
	STYLE.hug_left(_more)
	_more.pressed.connect(_show_all)
	_more.hide()
	_body.add_child(_more)
	_body.move_child(_more, 1)


## The host's playtest event: {state, elapsed, seconds, report, message}.
func apply(event: Dictionary) -> void:
	var next := str(event.get("state", state))
	_seconds = float(event.get("seconds", _seconds))
	if next == "playing":
		_set_elapsed(float(event.get("elapsed", 0.0)))
	if event.get("report") is Dictionary and report.is_empty():
		report = event["report"]
		_show_report()
	if next == state:
		return
	state = next
	match state:
		"starting", "writing":
			_step.text = STEPS[state]
			if state == "writing":
				_timer.hide()
		"playing":
			_step.text = STEPS["playing"] % game_name()
			_about.text = "It plays with the game's own controls and changes nothing."
			_about.show()
		"done":
			_settle("done" if _total() == 0 else "", found_text())
			_about.text = about_text()
			_about.show()
			UI_MOTION.pulse(self)
		"failed":
			_settle("failed", str(event.get("message", "The playtest didn't run.")))
		"stopped":
			_settle("stopped", "Stopped")


## Still running: its Stop is live.
func working() -> bool:
	return state in ["starting", "playing", "writing"]


func step_text() -> String:
	return _step.text


## The sentence under the status line: what the bot did and that it changed nothing.
func about_text() -> String:
	return (
		"A bot played %s for %s, %s. It changed nothing."
		% [game_name(), _clock(_played()), LOOKS_FOR]
	)


## What was found, at a glance: "Nothing went wrong", "Found 1 bug", "Found 2 things to check".
func found_text() -> String:
	var found := _total()
	if found == 0:
		return "Nothing went wrong"
	var serious := _serious()
	if serious > 0:
		return "Found %d %s" % [serious, "bug" if serious == 1 else "bugs"]
	return "Found %d %s to look at" % [found, "thing" if found == 1 else "things"]


## The game's name as its project names it, else "the game".
static func game_name() -> String:
	var name := str(ProjectSettings.get_setting("application/config/name", "")).strip_edges()
	return name if not name.is_empty() else "the game"


## Optimistic: "Stopping…" at once; the host confirms with "stopped".
func mark_stopping() -> bool:
	if not working() or stop.disabled:
		return false
	stop.disabled = true
	_step.text = "Stopping…"
	return true


## The rows showing findings (the visible ones).
func item_rows() -> Array[Control]:
	var rows: Array[Control] = []
	for child: Node in _items.get_children():
		if child is Control and (child as Control).visible:
			rows.append(child as Control)
	return rows


## The coverage line as shown ("" without one): "Explored 34% of the level".
func coverage_text() -> String:
	return _coverage_text.text if _coverage.visible else ""


## The heat toggles as shown ("Deaths 4", "Stuck 1", "Time spent 2:40"); [] without a heatmap.
func layer_words() -> PackedStringArray:
	var words := PackedStringArray()
	if not _layers.visible:
		return words
	for layer: String in _layer_buttons:
		var toggle := _layer_buttons[layer] as Button
		if toggle.visible:
			words.append(toggle.text)
	return words


## Turn heat [param layer] on or off, as its toggle does.
func show_layer(layer: String, on: bool) -> void:
	var toggle := _layer_buttons.get(layer) as Button
	if toggle != null:
		toggle.button_pressed = on


## Words of the counts row ("1 high", "2 low", …).
func count_words() -> PackedStringArray:
	var words := PackedStringArray()
	for child: Node in _counts.get_children():
		var label := child.get_node_or_null(^"Text") as Label
		if label != null:
			words.append(label.text)
	return words


## Whether the "not reached" key shows (some floor was not reached).
func missed_key_shown() -> bool:
	return _missed_words.visible


## The time-spent line as shown ("" when hidden): "Most time: Ore Yard 1:42, Gatehouse 0:31".
func time_text() -> String:
	return _time_text.text if _time_text.visible else ""


## A seed as the run used it: a whole number ("662040"), whatever JSON made of it.
static func seed_text(value: Variant) -> String:
	if value is float or value is int:
		return str(int(value))
	return str(value) if value != null else ""


## What a finding's row says on its one line: "Softlock · 0:05" for a serious one; for the
## rest the kind and what was found ("Warning: 20 ObjectDB instances were leaked at exit").
static func row_text(item: Dictionary) -> String:
	var title := str(item.get("title", "Finding"))
	var detail := str(item.get("detail", "")).strip_edges()
	var text := title
	if not str(item.get("severity", "low")) in HEADLINED and not detail.is_empty():
		var first := detail.split("\n", false)[0] if detail.contains("\n") else detail
		text = "%s: %s" % [title, first.trim_suffix(".")]
	if item.get("t") != null:
		text += " · " + _clock(float(item["t"]))
	return text


## The request "Fix this" pre-fills for [param item]: plain words about the game, never a
## command, and never asking for another playtest (only the user starts one).
static func fix_request(item: Dictionary) -> String:
	var text := "Fix a problem the test bot found while playing"
	if item.get("t") != null:
		text += ", about %s in" % _clock(float(item["t"]))
	text += ": %s." % str(item.get("title", "a problem"))
	var detail := str(item.get("detail", "")).strip_edges()
	if not detail.is_empty():
		text += " " + detail
	if not str(item.get("where", "")).is_empty():
		text += " It happens in %s." % str(item["where"])
	return text


func _set_elapsed(seconds: float) -> void:
	_timer.text = (
		"%s / %s" % [_clock(seconds), _clock(_seconds)] if _seconds > 0 else _clock(seconds)
	)
	_timer.tooltip_text = "Game time played"
	if not _timer.visible:
		UI_MOTION.fade_in(_timer)


func _show_report() -> void:
	for child: Node in _counts.get_children():
		child.queue_free()
	var counts: Dictionary = report.get("counts", {})
	for severity: String in SEVERITIES:
		var count := int(counts.get(severity, 0))
		if count == 0:
			continue
		var plain: Array = SEVERITY_WORDS[severity]
		var entry := UI.status("%d %s" % [count, plain[0 if count == 1 else 1]], DOTS[severity])
		entry.tooltip_text = "%d %s" % [count, severity]
		var words := entry.get_node(^"Text") as Label
		words.theme_type_variation = "JoineryNumeric"
		_counts.add_child(entry)
	_counts.visible = (
		_counts.get_child_count() > 1 and (report.get("items", []) as Array).size() > FIRST_ITEMS
	)
	if _counts.visible:
		UI_MOTION.fade_in(_counts)
	_show_coverage(report.get("coverage", {}) as Dictionary)
	var items: Array = report.get("items", [])
	var rows: Array = []
	for index: int in items.size():
		var row := _item_row(items[index] as Dictionary)
		row.visible = index < FIRST_ITEMS
		_items.add_child(row)
		if row.visible:
			rows.append(row)
	_body.visible = not items.is_empty() or _coverage.visible
	_fit_map.call_deferred()
	if items.size() > FIRST_ITEMS:
		_more.text = "Show all %d" % items.size()
		_more.show()
	UI_MOTION.stagger(rows)


## How much of the level the bot reached, over the map of what it missed.
func _show_coverage(coverage: Dictionary) -> void:
	var grid: Variant = coverage.get("grid", [])
	if coverage.is_empty() or not grid is Array or (grid as Array).is_empty():
		_coverage.hide()
		return
	var percent := float(coverage.get("percent", 0.0))
	_coverage_text.text = "Reached %s%% of the walkable level" % _percent(percent)
	_coverage_text.tooltip_text = (
		(
			"The bot stood in %d of the %d places on the level's floor where a player fits. "
			% [int(coverage.get("visited", 0)), int(coverage.get("navigable", 0))]
		)
		+ "Walls, props and ground outside the level don't count."
	)
	_map.call("set_coverage", coverage)
	_map.call("set_marks", _reach_marks(coverage))
	var missed := int((_map.call("counts") as Array)[1]) > 0
	_missed_key.visible = missed
	_path_key.visible = not (coverage.get("path", []) as Array).is_empty()
	_missed_words.visible = missed
	_show_heat(report.get("heatmap", {}) as Dictionary)
	_coverage.show()
	UI_MOTION.fade_in(_coverage)


## The places the run was asked to reach, on the map's grid: [{name, at, reached}].
func _reach_marks(coverage: Dictionary) -> Array:
	var marks: Array = []
	var origin: Variant = coverage.get("origin")
	var cell := float(coverage.get("cell", 0.0))
	if not origin is Array or (origin as Array).size() != 2 or cell <= 0.0:
		return marks
	for place: Variant in report.get("reach", []):
		if not place is Dictionary or not (place as Dictionary).get("at") is Array:
			continue
		var at: Array = (place as Dictionary)["at"]
		if at.size() != 2:
			continue
		var name := str((place as Dictionary).get("node", ""))
		if name.is_empty():
			name = str((place as Dictionary).get("target", ""))
		(
			marks
			. append(
				{
					"name": name,
					"at":
					[
						(float(at[0]) - float(origin[0])) / cell,
						(float(at[1]) - float(origin[1])) / cell,
					],
					"reached": bool((place as Dictionary).get("reached", false)),
				}
			)
		)
	return marks


## The recent runs' heat on the map: a toggle per layer that has anything to show.
func _show_heat(heat: Dictionary) -> void:
	_map.call("set_heat", heat)
	var any := false
	for layer: String in _layer_buttons:
		var toggle := _layer_buttons[layer] as Button
		var cells := int(_map.call("layer_cells", layer))
		toggle.visible = cells > 0
		any = any or toggle.visible
		var total := float(_map.call("layer_total", layer))
		var amount := _clock(total) if layer == "time" else str(int(total))
		toggle.text = "%s %s" % [str(LAYERS[layer][0]), amount]
		toggle.set_pressed_no_signal(cells > 0 and layer != "time")
		_map.call("set_layer", layer, toggle.button_pressed)
	var runs := int(heat.get("runs", 0))
	_runs_text.text = (
		"last %d playtests" % runs if runs > 1 else ("this playtest" if runs == 1 else "")
	)
	_runs_text.tooltip_text = "Deaths, stuck points and time spent, added up over these runs"
	_layers.visible = any
	_sync_time()
	_sync_key()


## Where the time went while the time layer is on: the areas it was spent in, most first.
func _sync_time() -> void:
	var on := bool(_map.call("layer_on", "time")) and int(_map.call("layer_cells", "time")) > 0
	var parts := PackedStringArray()
	for entry: Array in (_map.call("time_by_area") as Array).slice(0, TIME_AREAS):
		parts.append("%s %s" % [MAP.spaced(str(entry[0])), _clock(float(entry[1]))])
	_time_text.text = "Most time: " + ", ".join(parts) if not parts.is_empty() else ""
	_time_text.visible = on and not parts.is_empty()


## The "not reached" square in the tone the map draws it in now.
func _sync_key() -> void:
	if _map == null or not _missed_key.is_inside_tree():
		return
	var heat := bool(_map.call("heat_shown"))
	var colour := UI_THEME.color(_missed_key, "text_faint" if heat else "danger")
	colour.a = MAP.HEAT_MISSED_ALPHA if heat else MAP.MISSED_ALPHA
	if bool(_map.call("has_picture")):
		_missed_key.set_meta("dot", colour)
		_missed_key.color = Color(colour, 0.0)
	else:
		_missed_key.remove_meta("dot")
		_missed_key.color = colour
	_missed_key.queue_redraw()


func _layer_toggle(layer: String) -> Button:
	var toggle := Button.new()
	toggle.text = str(LAYERS[layer][0])
	toggle.tooltip_text = str(LAYERS[layer][1])
	toggle.accessibility_name = str(LAYERS[layer][1])
	toggle.theme_type_variation = "JoinerySegment"
	toggle.toggle_mode = true
	toggle.focus_mode = Control.FOCUS_ALL
	toggle.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI.bind_height(toggle, "sm", false)
	UI_MOTION.press_feedback(toggle)
	toggle.toggled.connect(
		func(on: bool) -> void:
			_map.call("set_layer", layer, on)
			_sync_time()
			_sync_key()
	)
	toggle.hide()
	return toggle


static func _percent(value: float) -> String:
	return (
		str(roundi(value))
		if value >= 10.0 or is_equal_approx(value, roundf(value))
		else "%.1f" % value
	)


## A small square of the map's colour [param role], for the legend.
func _swatch(role: String, alpha: float) -> ColorRect:
	var swatch := ColorRect.new()
	swatch.mouse_filter = Control.MOUSE_FILTER_IGNORE
	swatch.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	STYLE.on_theme(
		swatch,
		func() -> void:
			var side := float(UI_THEME.dot_px(swatch))
			swatch.custom_minimum_size = Vector2(side, side)
			var colour := UI_THEME.color(swatch, role)
			colour.a = alpha
			swatch.color = colour
	)
	return swatch


func _item_row(item: Dictionary) -> VBoxContainer:
	var row := VBoxContainer.new()
	row.theme_type_variation = "JoineryTight"
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	row.add_child(head)
	# Glyph and colour, so severity reads without colour too.
	head.add_child(BLOCKS.severity_glyph(str(item.get("severity", "low"))))
	var title := row_text(item)
	var toggle := UI.disclosure(title)
	toggle.accessibility_name = "%s, %s" % [title, str(item.get("severity", ""))]
	toggle.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	toggle.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	toggle.tooltip_text = str(item.get("detail", ""))
	head.add_child(toggle)
	var body := VBoxContainer.new()
	body.theme_type_variation = "JoineryTight"
	body.hide()
	row.add_child(body)
	var why := str(WHY.get(str(item.get("kind", "")), ""))
	if not why.is_empty():
		body.add_child(UI.label(why, "JoineryCaption", true))
	body.add_child(UI.label(str(item.get("detail", "")), "JoineryCaption", true))
	var meta := PackedStringArray()
	if not str(item.get("where", "")).is_empty():
		meta.append(str(item["where"]))
	meta.append("seed %s" % seed_text(item.get("seed", report.get("seed"))))
	body.add_child(UI.label(" · ".join(meta), "JoineryNumeric", true))
	var picture := _thumbnail(str(item.get("shot", "")))
	if picture != null:
		body.add_child(picture)
	# Information no change to the game fixes (a run too short for the level) offers no fix.
	if bool(item.get("fixable", true)):
		var fix := UI.secondary("Fix this")
		STYLE.on_card(fix)
		fix.tooltip_text = "Puts a request in the message box; nothing is sent until you send it"
		fix.accessibility_name = "Ask Journeyman to fix: " + title
		fix.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
		UI.bind_height(fix, "sm")
		UI_MOTION.press_feedback(fix)
		fix.pressed.connect(func() -> void: fix_requested.emit(fix_request(item)))
		body.add_child(fix)
	toggle.toggled.connect(
		func(open: bool) -> void:
			body.visible = open
			if open:
				UI_MOTION.reveal(body)
	)
	return row


## A screenshot of the moment, kept small: context, not an asset.
func _thumbnail(path: String) -> TextureRect:
	if path.is_empty() or not FileAccess.file_exists(path):
		return null
	var image := Image.load_from_file(path)
	if image == null or image.is_empty():
		return null
	var picture := TextureRect.new()
	picture.texture = ImageTexture.create_from_image(image)
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT
	picture.accessibility_name = "Screenshot of the moment"
	var ratio := float(image.get_height()) / maxf(float(image.get_width()), 1.0)
	STYLE.on_theme(
		picture,
		func() -> void:
			var width := UI_THEME.unit(picture) * 160.0
			picture.custom_minimum_size = Vector2(width, width * ratio)
	)
	picture.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	return picture


## The map takes what is left of the card's share of the window once the findings are shown
## in full (never under MAP_MIN_UNITS): the card stays in its share without a scroll, so no
## row is ever drawn cut off under the counts.
func _fit_map() -> void:
	if _map == null or not is_inside_tree() or not _coverage.visible:
		return
	var unit := UI_THEME.unit(self)
	var budget := get_viewport_rect().size.y * MAX_SHARE
	var others := get_combined_minimum_size().y - _map.get_combined_minimum_size().y
	var room := maxf(budget - others, MAP_MIN_UNITS * unit)
	if not is_equal_approx(float(_map.get("max_height")), room):
		_map.set("max_height", room)


func _notification(what: int) -> void:
	if what == NOTIFICATION_ENTER_TREE:
		_fit_map.call_deferred()


## "Show all", while some findings are folded away: the sidebar keeps it in sight when the
## cards scroll (sidebar_tasks.gd).
func pinned_actions() -> Control:
	return _more if _more.visible else null


func _show_all() -> void:
	var shown: Array = []
	for child: Node in _items.get_children():
		if child is Control and not (child as Control).visible:
			(child as Control).show()
			shown.append(child)
	_more.hide()
	UI_MOTION.stagger(shown)


func _settle(kind: String, words: String) -> void:
	_dots.set("running", false)
	_dots.hide()
	_timer.hide()
	stop.hide()
	close.show()
	# "" is a plain fact (how many of each severity): no icon claims it is good or bad.
	if not kind.is_empty():
		UI.set_status_icon(_status, kind)
	_status.visible = not kind.is_empty()
	_step.text = words
	_step.tooltip_text = words
	if kind in ["failed", "stopped"]:
		_about.hide()


func _on_stop() -> void:
	if mark_stopping():
		stop_pressed.emit()


func _serious() -> int:
	var counts: Dictionary = report.get("counts", {})
	return int(counts.get("critical", 0)) + int(counts.get("high", 0))


func _total() -> int:
	var total := 0
	for value: Variant in (report.get("counts", {}) as Dictionary).values():
		total += int(value)
	return total


func _played() -> float:
	return float(report.get("played_seconds", _seconds))


func _small_icon(icon_name: String, tooltip: String) -> Button:
	var button := UI.icon_button(icon_name, tooltip)
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI_MOTION.press_feedback(button)
	STYLE.on_theme_overrides(
		button,
		func() -> void:
			var side := float(UI_THEME.control_h(button, "sm"))
			button.custom_minimum_size = Vector2(side, side)
	)
	return button


static func _clock(seconds: float) -> String:
	return UI_MOTION.format_clock(seconds)
