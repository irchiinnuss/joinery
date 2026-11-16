@tool
extends VBoxContainer
## "Wait for Go" in Settings → Agent: when a to-do Journeyman proposes waits for the user's Go
## before any work starts (`[agents] todo_go` in joinery.toml). Auto, the default, waits for a
## to-do of two steps or more unless changes are kept automatically or the request came from
## `joinery run`; Two steps or more always waits for those; Always waits for every to-do; Never
## starts at once. Read with `joinery todo-go` whenever the settings page shows it; a change
## applies at once and a failed write puts the picker back and says why.

const UI := preload("ui_components.gd")
const COMMAND := preload("command_job.gd")
const TITLE := "Wait for Go"
## The modes, in the order the picker lists them, and their words.
const MODES: Array[String] = ["auto", "multi", "always", "never"]
const LABELS: Array[String] = ["Auto", "Two steps or more", "Always", "Never"]
const HINTS: Array[String] = [
	"A to-do of two steps or more waits for your Go, unless changes are kept automatically.",
	"A to-do of two steps or more always waits for your Go.",
	"Every to-do waits for your Go.",
	"To-dos start at once.",
]

## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
var picker: OptionButton
var row: Control
var reason: Label
var _job: RefCounted
var _job_kind := ""
var _shown := 0


func _init() -> void:
	name = "TodoGoSetting"
	theme_type_variation = "JoineryFlush"
	picker = UI.picker(LABELS)
	for index: int in MODES.size():
		picker.set_item_tooltip(index, HINTS[index])
	picker.accessibility_name = TITLE
	# Unknown until the project answers.
	picker.disabled = true
	picker.item_selected.connect(_on_selected)
	row = UI.setting_row(TITLE, picker)
	add_child(row)
	reason = UI.label("", "JoineryCaption", true)
	reason.hide()
	add_child(reason)
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
	_run("status", ["todo-go", "--project", _project(), "--json"])


## Show a `joinery todo-go` answer ({todo_go} or {error}).
func apply_status(data: Dictionary) -> void:
	picker.disabled = false
	if data.has("error"):
		reason.text = str(data["error"])
		reason.show()
		picker.select(_shown)
		return
	reason.hide()
	_shown = maxi(0, MODES.find(str(data.get("todo_go", "auto"))))
	picker.select(_shown)
	picker.tooltip_text = HINTS[_shown]


func _on_selected(index: int) -> void:
	picker.disabled = true
	_run("save", ["todo-go", "--project", _project(), "--json", "--set", MODES[index]])


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
	_job = null
	_job_kind = ""
	set_process(false)
	var data: Dictionary = {}
	for line: String in str(result.get("output", "")).split("\n", false):
		var parsed: Variant = COMMAND.parse(line)
		if parsed is Dictionary:
			data = parsed
	if int(result.get("code", 1)) != 0 and not data.has("error"):
		data = {"error": "Couldn't read the Wait for Go setting. Details are in Output."}
		print("[Joinery] todo-go\n", result.get("output", ""), "\n", result.get("error", ""))
	apply_status(data)


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")
