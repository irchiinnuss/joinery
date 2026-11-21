@tool
extends VBoxContainer
## Journeyman settings → Remote (ADR 0093): follow and direct Journeyman from a phone, over the
## user's own Tailscale. The switch turns Remote on (`joinery remote start`) or off; the line under
## it says where a phone opens it, or what Tailscale still needs. Pair a device shows a QR code and
## its code for 5 minutes; a phone that uses it is paired only once the user answers the question
## here ("Pair “Ana's iPhone”?", Cancel focused). Paired devices are listed with Revoke, which
## takes effect at once. Pairing, like every other setting, stays at the machine.

const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const COMMAND := preload("command_job.gd")
const ASK := preload("revert_ask.gd")
const POLL_SECONDS := 2.0
const QR_SIZE := 192

## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
## Off in tests: nothing runs the CLI; answers come through [method apply_status] and the rest.
var cli_enabled := true
var switch_row: UI.SwitchRow
var where: Label
var pair_button: Button
var pair_box: VBoxContainer
var qr: TextureRect
var code: Label
var pair_note: Label
var devices: VBoxContainer
var _ask: Node
var _job: RefCounted
var _job_kind := ""
var _queued: Array = []
var _pairing_id := ""
var _pairing_until := 0.0
var _asked_request := ""
var _poll := 0.0


func _init() -> void:
	name = "RemoteSetting"
	theme_type_variation = "JoineryFlush"
	switch_row = UI.switch_row("Remote", "Follow and direct Journeyman from your phone")
	switch_row.name = "RemoteSwitch"
	switch_row.switch_button.accessibility_name = "Joinery Remote"
	switch_row.toggled.connect(_on_toggled)
	add_child(switch_row)
	where = UI.label("", "JoineryCaption", true)
	where.name = "Where"
	where.autowrap_mode = TextServer.AUTOWRAP_ARBITRARY
	add_child(_inset(where))
	pair_button = UI.secondary("Pair a device")
	pair_button.name = "Pair"
	pair_button.pressed.connect(_pair)
	UI_MOTION.press_feedback(pair_button)
	add_child(UI.setting_row("Devices", pair_button))
	pair_box = VBoxContainer.new()
	pair_box.name = "Pairing"
	pair_box.theme_type_variation = "JoineryRowTight"
	qr = TextureRect.new()
	qr.name = "QR"
	qr.custom_minimum_size = Vector2(QR_SIZE, QR_SIZE)
	qr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	qr.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	qr.accessibility_name = "Pairing QR code"
	pair_box.add_child(qr)
	code = UI.label("", "JoineryMono")
	code.name = "Code"
	code.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	pair_box.add_child(code)
	pair_note = UI.label("", "JoineryFaint", true)
	pair_note.name = "PairNote"
	pair_note.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	pair_box.add_child(pair_note)
	pair_box.hide()
	add_child(_inset(pair_box))
	devices = VBoxContainer.new()
	devices.name = "DeviceList"
	add_child(devices)
	set_process(false)


func _inset(control: Control) -> MarginContainer:
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	inset.add_child(control)
	control.visibility_changed.connect(func() -> void: inset.visible = control.visible)
	inset.visible = control.visible
	return inset


func _notification(what: int) -> void:
	if what == NOTIFICATION_VISIBILITY_CHANGED and is_visible_in_tree():
		refresh()
	elif what == NOTIFICATION_EXIT_TREE and _job != null:
		_job.call("cancel")
		_job = null


## Read Remote's state again.
func refresh() -> void:
	_run("status", ["remote", "status", "--json"])


## Show a `joinery remote status` answer.
func apply_status(data: Dictionary) -> void:
	var on := bool(data.get("enabled", false))
	switch_row.set_on(on)
	switch_row.set_disabled_reason(str(data.get("error", "")))
	var ts: Dictionary = data.get("tailscale", {}) if data.get("tailscale") is Dictionary else {}
	if not on:
		where.text = "Off. Paired devices stay paired."
	elif not bool(ts.get("running", false)):
		where.text = str(ts.get("message", "Tailscale isn't connected."))
	elif str(data.get("url", "")).begins_with("http://127.0.0.1"):
		where.text = "Put it on your tailnet: " + str(data.get("serve_command", ""))
	else:
		where.text = "On your phone: " + str(data.get("url", ""))
	pair_button.disabled = not on
	pair_button.tooltip_text = "" if on else "Turn Remote on first"
	_show_devices(data.get("devices", []))


func _show_devices(list: Variant) -> void:
	for child: Node in devices.get_children():
		child.queue_free()
	if not list is Array:
		return
	for device: Variant in list:
		if not device is Dictionary:
			continue
		var entry: Dictionary = device
		var revoke := UI.quiet("Revoke")
		revoke.name = "Revoke"
		revoke.accessibility_name = "Revoke %s" % entry.get("name", "")
		var id := str(entry.get("id", ""))
		revoke.pressed.connect(func() -> void: _run("revoke", ["remote", "revoke", id, "--json"]))
		var row := UI.setting_row(str(entry.get("name", "Phone")), revoke)
		row.name = "Device"
		row.tooltip_text = ", ".join(entry.get("scopes", []))
		devices.add_child(row)
		UI_MOTION.fade_in(row)


func _on_toggled(on: bool) -> void:
	switch_row.set_checking()
	_run("toggle", ["remote", "start" if on else "stop", "--json"])
	if not on:
		_end_pairing()


func _pair() -> void:
	_end_pairing()
	var png := OS.get_temp_dir().path_join("joinery-remote-qr-%d.png" % OS.get_process_id())
	pair_note.text = "Making a code…"
	pair_box.show()
	_run("pair", ["remote", "pair", "--json", "--no-wait", "--qr-png", png])


## Show a `joinery remote pair --json --no-wait` answer: the QR code, the code, how long.
func apply_pairing(data: Dictionary) -> void:
	if data.has("error"):
		pair_note.text = str(data["error"])
		return
	_pairing_id = str(data.get("pairing_id", ""))
	_pairing_until = Time.get_unix_time_from_system() + 300.0
	var image := Image.load_from_file(str(data.get("qr", ""))) if data.get("qr") else null
	qr.texture = ImageTexture.create_from_image(image) if image != null else null
	code.text = str(data.get("code", ""))
	pair_note.text = (
		"Scan it with your phone's camera, or open the address and type the code. "
		+ "It works for 5 minutes."
	)
	UI_MOTION.fade_in(pair_box)
	set_process(true)


## Show the pairing requests (`joinery remote requests --json`): ask about the first one.
func apply_requests(data: Dictionary) -> void:
	var waiting: Array = data.get("requests", []) if data.get("requests") is Array else []
	if waiting.is_empty() or is_instance_valid(_ask):
		return
	var request: Dictionary = waiting[0]
	_asked_request = str(request.get("id", ""))
	_ask = ASK.new()
	_ask.call("setup", str(request.get("question", "Pair this device?")), "Pair", "Cancel")
	_ask.connect("answered", _on_answered)
	pair_box.add_child(_ask)


func _on_answered(ok: bool) -> void:
	var id := _asked_request
	_asked_request = ""
	_run("confirm", ["remote", "confirm", id, "--allow" if ok else "--deny", "--json"])
	_end_pairing()


func _end_pairing() -> void:
	_pairing_id = ""
	if is_instance_valid(_ask):
		_ask.queue_free()
	_ask = null
	pair_box.hide()
	qr.texture = null
	set_process(_job != null)


func _run(kind: String, args: Array) -> void:
	if not cli_enabled:
		return
	if _job != null:
		_queued.append([kind, args])
		return
	_job = COMMAND.joinery(cli_prefix, args)
	_job_kind = kind if _job != null else ""
	set_process(true)


func _process(delta: float) -> void:
	if _job != null:
		_job = COMMAND.finish(_job, _finished)
		return
	if not _queued.is_empty():
		var next: Array = _queued.pop_front()
		_run(str(next[0]), next[1])
		return
	if _pairing_id.is_empty():
		set_process(false)
		return
	if Time.get_unix_time_from_system() > _pairing_until:
		_end_pairing()
		where.text = "The code expired. Pair a device makes a new one."
		return
	_poll -= delta
	if _poll <= 0.0 and not is_instance_valid(_ask):
		_poll = POLL_SECONDS
		_run("requests", ["remote", "requests", "--pairing", _pairing_id, "--json"])


func _finished(result: Dictionary) -> void:
	var kind := _job_kind
	_job_kind = ""
	var data := COMMAND.object(str(result.get("output", "")))
	if int(result.get("code", 1)) != 0 and not data.has("error"):
		data = {"error": "Remote didn't answer. Details are in Output."}
		print("[Joinery] remote\n", result.get("output", ""), "\n", result.get("error", ""))
	match kind:
		"status", "toggle":
			apply_status(data)
		"pair":
			apply_pairing(data)
		"requests":
			apply_requests(data)
		"confirm", "revoke":
			if data.has("error"):
				where.text = str(data["error"])
			refresh()
