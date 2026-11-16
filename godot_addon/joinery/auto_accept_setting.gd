@tool
extends VBoxContainer
## The "Accept changes automatically" switch in Settings → Agent (ADR 0085): on, a change that
## passes every check with nothing to say is kept without asking, and its card offers Undo
## (`[agents] auto_accept = true` in joinery.toml); off, the default, every change waits for
## Accept. Under it, while on, "Hold reviewer warnings" (`[agents] auto_accept_hold`): off, the
## default, only the reviewer's blocking findings keep a change waiting and its warnings and notes
## are shown with the kept change; on, its warnings keep the change waiting too. Read with
## `joinery auto-accept` whenever the settings page shows it; a change applies at once and a
## failed write puts the switch back and says why.

const UI := preload("ui_components.gd")
const COMMAND := preload("command_job.gd")
const TITLE := "Accept changes automatically"
const CAPTION := "Changes that pass every check are kept without asking. Undo stays one click."
const HOLD_TITLE := "Hold reviewer warnings"
const HOLD_CAPTION := (
	"On: a change the reviewer warns about waits for you. Off: only its blocking findings "
	+ "hold a change; warnings and notes are shown with it."
)

## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
var row: UI.SwitchRow
var hold_row: UI.SwitchRow
var _job: RefCounted
var _job_kind := ""


func _init() -> void:
	name = "AutoAcceptSetting"
	theme_type_variation = "JoineryFlush"
	# One line: what it does is the row's tooltip, not a paragraph under it.
	row = UI.switch_row(TITLE, "", false)
	row.tooltip_text = CAPTION
	row.switch_button.accessibility_name = TITLE
	# The whole name, on two lines in a narrow sidebar rather than cut short.
	row.title.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	row.title.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	# Unknown until the project answers; the row says it is checking.
	row.set_checking()
	row.toggled.connect(_on_toggled)
	add_child(row)
	hold_row = UI.switch_row(HOLD_TITLE, "", false)
	hold_row.tooltip_text = HOLD_CAPTION
	hold_row.switch_button.accessibility_name = HOLD_TITLE
	hold_row.title.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	hold_row.title.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	hold_row.toggled.connect(_on_hold_toggled)
	# Only while changes are kept automatically: off, every change waits anyway.
	hold_row.hide()
	add_child(hold_row)
	set_process(false)


func _notification(what: int) -> void:
	if what == NOTIFICATION_VISIBILITY_CHANGED and is_visible_in_tree():
		refresh()
	elif what == NOTIFICATION_EXIT_TREE and _job != null:
		_job.call("cancel")
		_job = null


## Read the project's choice again.
func refresh() -> void:
	if _job_kind.begins_with("save"):
		return
	_run("status", ["auto-accept", "--project", _project(), "--json"])


## Show a `joinery auto-accept` answer ({enabled, hold_on} or {error}).
func apply_status(data: Dictionary) -> void:
	row.set_disabled_reason("")
	if data.has("error"):
		row.reason.text = str(data["error"])
		row.reason.show()
		return
	row.reason.hide()
	var on := bool(data.get("enabled", false))
	row.set_on(on)
	hold_row.set_on(str(data.get("hold_on", "block")) == "warn")
	hold_row.switch_button.disabled = false
	hold_row.visible = on


func _on_toggled(on: bool) -> void:
	row.switch_button.disabled = true
	_run("save", ["auto-accept", "--project", _project(), "--json", "--set", "on" if on else "off"])


func _on_hold_toggled(on: bool) -> void:
	hold_row.switch_button.disabled = true
	_run(
		"save_hold",
		["auto-accept", "--project", _project(), "--json", "--hold-on", "warn" if on else "block"]
	)


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
		data = {"error": "Couldn't read the auto-accept setting. Details are in Output."}
		print("[Joinery] auto-accept\n", result.get("output", ""), "\n", result.get("error", ""))
	if kind == "save" and data.has("error"):
		row.set_on(not row.is_on())
	elif kind == "save_hold" and data.has("error"):
		hold_row.set_on(not hold_row.is_on())
	apply_status(data)


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")
