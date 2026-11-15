@tool
extends Node
## "/cut" from the sidebar. The findings are gathered by `joinery cut --json`, run off the main
## thread with no model; the card (cut_card.gd) sits with the agents at work.
##
## "Write polish list" sends `cut_write` to the chat host (chat_bridge.gd's send_cut), which
## writes design/polish.md and moves the ticked cuts to the brief's Out of scope as one change
## in Review. Nothing is deleted, and nothing changes until the user accepts it there.

const CARD := preload("cut_card.gd")
const UI_MOTION := preload("ui_motion.gd")
const COMMAND_SCRIPT := preload("command_job.gd")
const SLASH := preload("slash_commands.gd")
const NOT_CONNECTED := "Connect Journeyman to write it: the polish list is a change you review."

## The sidebar (journeyman_dock.gd) this serves.
var dock: Control
## The task list (sidebar_tasks.gd): the card sits in it, and its bridge is the chat's.
var tasks: Object
## The cut card, while there is one.
var card: Control
## Runs `joinery cut …`; tests replace it with Callable(args) -> Dictionary (the JSON).
var runner := Callable()
var _job: RefCounted


static func attach(sidebar: Control, list: Object) -> Node:
	var cutter: Node = load("res://addons/joinery/sidebar_cut.gd").new()
	cutter.set("dock", sidebar)
	cutter.set("tasks", list)
	(list as Node).add_child(cutter)
	var slash: Object = sidebar.get("_slash")
	if slash != null and slash.has_signal("cut_requested"):
		slash.connect("cut_requested", cutter.open)
	return cutter


func _init() -> void:
	name = "Cut"
	set_process(false)


## Show the card and gather the findings (again).
func open() -> void:
	_ensure_card()
	gather()


## `joinery cut --project <this project> --json`.
func gather() -> void:
	if not is_instance_valid(card):
		return
	card.call("set_gathering")
	var args := PackedStringArray(
		["run", "--frozen", "--quiet", "--project", SLASH.engine_dir(), "joinery", "cut"]
	)
	args.append_array(["--project", ProjectSettings.globalize_path("res://").rstrip("/"), "--json"])
	if runner.is_valid():
		_take(runner.call(args))
		return
	if _job != null:
		_job.call("cancel")
	_job = COMMAND_SCRIPT.new()
	if _job.call("start", "uv", args) != OK:
		_job = null
		_take({"error": "Couldn't start Joinery."})
		return
	set_process(true)


## Write: one change in Review, made by the chat host.
func write(cuts: Array) -> void:
	var bridge: Object = tasks.get("bridge") if tasks != null else null
	if bridge == null or not bridge.has_method("send_cut") or not bridge.call("send_cut", cuts):
		card.call("restore", NOT_CONNECTED)
		return
	card.call("set_sent", cuts)


func close() -> void:
	var leaving := card
	card = null
	tasks.set("cut_card", null)
	if not is_instance_valid(leaving):
		return
	UI_MOTION.exit(
		leaving,
		null,
		func() -> void:
			leaving.queue_free()
			tasks.call("sync_visible")
	)


func _ensure_card() -> void:
	if is_instance_valid(card):
		return
	card = CARD.new()
	card.connect("write_requested", write)
	card.connect("recheck_requested", gather)
	card.connect("close_pressed", close)
	(tasks as Node).add_child(card)
	tasks.set("cut_card", card)
	(tasks as Control).show()
	UI_MOTION.enter(card)


func _process(_delta: float) -> void:
	if _job == null:
		set_process(false)
		return
	var result: Dictionary = _job.call("poll")
	if result.is_empty():
		return
	_job = null
	set_process(false)
	var output := str(result.get("output", ""))
	if int(result.get("code", 1)) != 0:
		print("[Joinery] cut\n", output, "\n", result.get("error", ""))
	var data := SLASH.parse_json(output)
	if data.is_empty():
		data = {"error": "Joinery didn't answer. Details are in Output."}
	_take(data)


func _take(data: Dictionary) -> void:
	if not is_instance_valid(card):
		return
	if not data.has("rows"):
		card.call(
			"apply_failed",
			str(data.get("error", "Couldn't gather the findings. Details are in Output."))
		)
		return
	card.call("show_report", data)
