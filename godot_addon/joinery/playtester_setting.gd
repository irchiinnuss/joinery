@tool
extends VBoxContainer
## The "Playtester" switch in Journeyman settings (ADR 0038): on (the default), a bot plays the
## game when you ask, with /playtest or a request that asks for a playtest; Journeyman never
## starts one by itself. Off (`[agents] playtester = false` in joinery.toml), /playtest and the
## playtest card are hidden and nothing plays the game. Read with `joinery playtester` whenever
## the settings page shows it; a change applies at once and a failed write puts the switch back
## and says why. [method enabled] is what the sidebar asks before offering /playtest.

const UI := preload("ui_components.gd")
const COMMAND := preload("command_job.gd")
const CAPTION := "A bot plays the game only when you ask (/playtest). Off hides it."

## joinery.toml as last read: its modified time and whether it turns the playtester off.
static var _stamp := -1
static var _on := true

## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
var row: UI.SwitchRow
var _job: RefCounted
var _job_kind := ""


func _init() -> void:
	name = "PlaytesterSetting"
	theme_type_variation = "JoineryFlush"
	row = UI.switch_row("Playtester", CAPTION, true)
	row.switch_button.accessibility_name = "Playtester"
	row.set_checking()
	row.toggled.connect(_on_toggled)
	add_child(row)
	set_process(false)


## Whether the project has the playtester on: `[agents] playtester` in res://joinery.toml is not
## false. Read again only when the file changes, so the slash list can ask on every key.
static func enabled() -> bool:
	var path := ProjectSettings.globalize_path("res://joinery.toml")
	if not FileAccess.file_exists(path):
		return true
	var stamp := int(FileAccess.get_modified_time(path))
	if stamp != _stamp:
		_stamp = stamp
		_on = not says_off(FileAccess.get_file_as_string(path))
	return _on


## Whether joinery.toml's text sets `playtester = false` in its `[agents]` table.
static func says_off(text: String) -> bool:
	var agents := false
	for raw: String in text.split("\n"):
		var line := raw.strip_edges()
		if line.begins_with("["):
			agents = line.begins_with("[agents]")
			continue
		var parts := line.split("=", true, 1)
		if agents and parts.size() == 2 and parts[0].strip_edges() == "playtester":
			return parts[1].split("#")[0].strip_edges() == "false"
	return false


func _notification(what: int) -> void:
	if what == NOTIFICATION_VISIBILITY_CHANGED and is_visible_in_tree():
		refresh()
	elif what == NOTIFICATION_EXIT_TREE and _job != null:
		_job.call("cancel")
		_job = null


## Read the project's choice again.
func refresh() -> void:
	if _job_kind == "save":
		return
	_run("status", ["playtester", "--project", _project(), "--json"])


## Show a `joinery playtester` answer ({playtester} or {error}).
func apply_status(data: Dictionary) -> void:
	row.set_disabled_reason("")
	if data.has("error"):
		row.reason.text = str(data["error"])
		row.reason.show()
		return
	row.reason.hide()
	row.set_on(bool(data.get("playtester", true)))


func _on_toggled(on: bool) -> void:
	row.switch_button.disabled = true
	_run("save", ["playtester", "--project", _project(), "--json", "--set", "on" if on else "off"])


func _run(kind: String, args: Array) -> void:
	if _job != null:
		_job.call("cancel")
	_job = COMMAND.joinery(cli_prefix, args)
	_job_kind = kind if _job != null else ""
	set_process(_job != null)


func _process(_delta: float) -> void:
	if _job == null:
		set_process(false)
		return
	var result: Dictionary = _job.call("poll")
	if result.is_empty():
		return
	var kind := _job_kind
	_job = null
	_job_kind = ""
	set_process(false)
	var data: Dictionary = {}
	for line: String in str(result.get("output", "")).split("\n", false):
		var parsed: Variant = COMMAND.parse(line)
		if parsed is Dictionary:
			data = parsed
	if int(result.get("code", 1)) != 0 and not data.has("error"):
		data = {"error": "Couldn't read the playtester setting. Details are in Output."}
		print("[Joinery] playtester\n", result.get("output", ""), "\n", result.get("error", ""))
	if kind == "save":
		if data.has("error"):
			row.set_on(not row.is_on())
		# The sidebar reads the file again: /playtest shows or hides at once.
		_stamp = -1
	apply_status(data)


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")
