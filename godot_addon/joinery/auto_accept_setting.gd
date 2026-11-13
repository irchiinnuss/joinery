@tool
extends VBoxContainer
## The "Accept changes automatically" switch in Settings → Replies (ADR 0085): on, a change that
## passes every check with nothing to say is kept without asking, and its card offers Undo
## (`[agents] auto_accept = true` in joinery.toml); off, the default, every change waits for
## Accept. Read with `joinery auto-accept` whenever the settings page shows it; a change applies
## at once and a failed write puts the switch back and says why.

const UI := preload("ui_components.gd")
const COMMAND := preload("command_job.gd")
const TITLE := "Accept changes automatically"
const CAPTION := "Changes that pass every check are kept without asking. Undo stays one click."

## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
var row: UI.SwitchRow
var _job: RefCounted
var _job_kind := ""


func _init() -> void:
	name = "AutoAcceptSetting"
	theme_type_variation = "JoineryFlush"
	row = UI.switch_row(TITLE, CAPTION, false)
	row.switch_button.accessibility_name = TITLE
	# The whole name, on two lines in a narrow sidebar rather than cut short.
	row.title.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	row.title.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	# Unknown until the project answers; the row says it is checking.
	row.set_checking()
	row.toggled.connect(_on_toggled)
	add_child(row)
	set_process(false)


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
	_run("status", ["auto-accept", "--project", _project(), "--json"])


## Show a `joinery auto-accept` answer ({enabled} or {error}).
func apply_status(data: Dictionary) -> void:
	row.set_disabled_reason("")
	if data.has("error"):
		row.reason.text = str(data["error"])
		row.reason.show()
		return
	row.reason.hide()
	row.set_on(bool(data.get("enabled", false)))


func _on_toggled(on: bool) -> void:
	row.switch_button.disabled = true
	_run("save", ["auto-accept", "--project", _project(), "--json", "--set", "on" if on else "off"])


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
		var parsed: Variant = JSON.parse_string(line)
		if parsed is Dictionary:
			data = parsed
	if int(result.get("code", 1)) != 0 and not data.has("error"):
		data = {"error": "Couldn't read the auto-accept setting. Details are in Output."}
		print("[Joinery] auto-accept\n", result.get("output", ""), "\n", result.get("error", ""))
	if kind == "save" and data.has("error"):
		row.set_on(not row.is_on())
	apply_status(data)


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")
