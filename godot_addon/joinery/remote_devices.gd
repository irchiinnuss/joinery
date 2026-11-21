@tool
extends VBoxContainer
## Settings → Remote's paired devices and what they did (remote_setting.gd, ADR 0093). Each device
## has what it may do (Follow only, Follow and chat, or Everything, which adds Accept and Discard;
## `joinery remote scopes`) and Revoke. Unpair all asks in place first, Cancel focused. Activity
## folds open to the last remote actions and refusals (`joinery remote log`). Every change goes
## out as [signal command] for the settings page to run.

## Run `joinery` with [param args]; [param kind] says what the answer is for.
signal command(kind: String, args: Array)

const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const ASK := preload("revert_ask.gd")
## What a device may do, as offered: [label, scopes].
const CHOICES := [
	["Follow only", "watch"],
	["Follow and chat", "watch,chat"],
	["Everything", "watch,chat,decide"],
]
## An audit entry's action, in words.
const ACTIONS := {
	"prompt": "Sent a message",
	"chat_task": "Sent to another chat",
	"cancel": "Stopped",
	"cancel_tasks": "Stopped tasks",
	"todo_go": "Pressed Go",
	"todo_cancel": "Cancelled a to-do",
	"decision": "Decided a change",
	"provider_switch": "Answered a limit",
	"art_review": "Read the art direction",
	"open": "Opened a project",
	"pair": "Pairing",
	"revoke": "Revoked",
	"scopes": "Changed what it may do",
	"push": "Notifications",
	"art_approval": "Tried to approve the art direction",
	"auto_accept": "Tried to change automatic Accept",
	"remote_on": "Remote turned on",
	"remote_off": "Remote turned off",
	"helper_on": "Kept available when Joinery is closed",
	"helper_off": "No longer kept available when closed",
	"tailscale_serve": "Put on the tailnet",
	"forget_all": "Unpaired every device",
}
const SHOWN := 20

var list: VBoxContainer
var empty: Label
var forget: Button
var activity: UI.Section
var lines: VBoxContainer
var _ask: Node


func _init() -> void:
	name = "RemoteDevices"
	theme_type_variation = "JoineryFlush"
	list = VBoxContainer.new()
	list.name = "DeviceList"
	add_child(list)
	empty = UI.label("No phone is paired yet.", "JoineryFaint", true)
	empty.name = "NoDevices"
	add_child(_inset(empty))
	forget = UI.quiet("Unpair all")
	forget.name = "UnpairAll"
	forget.accessibility_name = "Unpair every device"
	forget.pressed.connect(_ask_forget)
	var row := HBoxContainer.new()
	row.alignment = BoxContainer.ALIGNMENT_END
	row.add_child(forget)
	add_child(_inset(row))
	activity = UI.section("Activity", true)
	activity.name = "Activity"
	activity.toggled.connect(
		func(open: bool) -> void:
			if open:
				command.emit("log", ["remote", "log", "--json", "--limit", str(SHOWN)])
	)
	lines = VBoxContainer.new()
	lines.name = "Lines"
	lines.theme_type_variation = "JoineryTight"
	activity.body.add_child(lines)
	add_child(_inset(activity))


func _inset(control: Control) -> MarginContainer:
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	inset.add_child(control)
	control.visibility_changed.connect(func() -> void: inset.visible = control.visible)
	inset.visible = control.visible
	return inset


## The paired devices from `joinery remote status` ({id, name, scopes, last_seen, …}).
func set_devices(devices: Variant) -> void:
	for child: Node in list.get_children():
		child.queue_free()
	var shown := 0
	for device: Variant in devices if devices is Array else []:
		if device is Dictionary:
			list.add_child(_row(device))
			shown += 1
	empty.visible = shown == 0
	forget.get_parent().visible = shown > 1
	if activity.open:
		command.emit("log", ["remote", "log", "--json", "--limit", str(SHOWN)])


func _row(device: Dictionary) -> Control:
	var id := str(device.get("id", ""))
	var name_text := str(device.get("name", "Phone"))
	var picker := UI.picker(CHOICES.map(func(choice: Array) -> String: return choice[0]), "sm")
	picker.name = "Scopes"
	picker.accessibility_name = "What %s may do" % name_text
	picker.tooltip_text = "Everything includes Accept and Discard"
	picker.select(_choice(device.get("scopes", [])))
	picker.item_selected.connect(
		func(index: int) -> void:
			command.emit("scopes", ["remote", "scopes", id, CHOICES[index][1], "--json"])
	)
	var revoke := UI.quiet("Revoke")
	revoke.name = "Revoke"
	revoke.accessibility_name = "Revoke %s" % name_text
	revoke.pressed.connect(
		func() -> void: command.emit("revoke", ["remote", "revoke", id, "--json"])
	)
	var controls := HBoxContainer.new()
	controls.theme_type_variation = "JoineryRowTight"
	controls.add_child(picker)
	controls.add_child(revoke)
	var row := UI.setting_row(name_text, controls)
	row.name = "Device"
	var seen := float(device.get("last_seen", 0))
	var says := "Last seen " + _when(seen) if seen > 0 else ""
	if int(device.get("push", 0)) > 0:
		says += (" · " if not says.is_empty() else "") + "Notifications on"
	row.tooltip_text = says
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryTight"
	column.add_child(row)
	if not says.is_empty():
		var caption := UI.label(says, "JoineryFaint", true)
		caption.name = "Seen"
		column.add_child(_inset(caption))
	UI_MOTION.fade_in(column)
	return column


static func _choice(scopes: Variant) -> int:
	var have: Array = scopes if scopes is Array else []
	if have.has("decide"):
		return 2
	return 1 if have.has("chat") else 0


## "10:42" today, else "Mon 10:42"; a date further back.
static func _when(seconds: float) -> String:
	var now := Time.get_unix_time_from_system()
	var stamp := Time.get_datetime_dict_from_unix_time(int(seconds))
	var clock := "%02d:%02d" % [stamp["hour"], stamp["minute"]]
	if now - seconds < 20 * 3600:
		return clock
	if now - seconds < 6 * 86400:
		return (
			"%s %s" % [["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][stamp["weekday"]], clock]
		)
	return "%d-%02d-%02d" % [stamp["year"], stamp["month"], stamp["day"]]


## The last remote actions (`joinery remote log --json`), newest first.
func set_activity(entries: Variant) -> void:
	for child: Node in lines.get_children():
		child.queue_free()
	var found: Array = entries if entries is Array else []
	if found.is_empty():
		lines.add_child(UI.label("Nothing yet.", "JoineryFaint"))
		return
	found = found.duplicate()
	found.reverse()
	for entry: Variant in found.slice(0, SHOWN):
		if entry is Dictionary:
			lines.add_child(UI.label(activity_line(entry), "JoineryCaption", true))


static func activity_line(entry: Dictionary) -> String:
	var action := str(entry.get("action", ""))
	var words := str(ACTIONS.get(action, action.capitalize()))
	if action == "decision" and entry.has("accept"):
		words = "Accepted a change" if bool(entry["accept"]) else "Discarded a change"
	var who := str(entry.get("device_name", ""))
	var line := "%s  %s" % [_when(float(entry.get("ts", 0))), words]
	if not who.is_empty():
		line += " · " + who
	if str(entry.get("result", "")) != "ok":
		line += " · refused" if entry.get("result") == "refused" else " · failed"
		if entry.has("reason"):
			line += ": " + str(entry["reason"])
	return line


func _ask_forget() -> void:
	if is_instance_valid(_ask):
		return
	_ask = ASK.new()
	_ask.call(
		"setup",
		"Unpair every device? Each one needs a new code to come back.",
		"Unpair all",
		"Cancel"
	)
	_ask.connect(
		"answered",
		func(ok: bool) -> void:
			if ok:
				command.emit("forget", ["remote", "forget-all", "--yes", "--json"])
	)
	(forget.get_parent().get_parent() as Node).add_sibling(_ask)
