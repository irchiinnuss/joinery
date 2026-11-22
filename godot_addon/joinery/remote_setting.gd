@tool
extends VBoxContainer
## Journeyman settings → Remote (ADR 0093): follow and direct Journeyman from a phone, over the
## user's own Tailscale. The switch turns Remote on (`joinery remote start`) or off; the line under
## it says where a phone opens it, or what Tailscale still needs. Pair a device shows a QR code and
## its code for 5 minutes; a phone that uses it is paired only once the user answers the question
## here ("Pair “Ana's iPhone”?", Cancel focused). Under the address, one button does the next
## step: Get Tailscale, Put it on my tailnet (`tailscale serve`, asked first), or Copy address,
## decided by whether `tailscale serve` actually serves Remote (`served` in the status).
## Paired devices, what each may do, Revoke and the activity are remote_devices.gd's. Pairing,
## like every other setting, stays at the machine.

const UI := preload("ui_components.gd")
const UI_MOTION := preload("ui_motion.gd")
const COMMAND := preload("command_job.gd")
const ASK := preload("revert_ask.gd")
const DEVICES := preload("remote_devices.gd")
const GET_TAILSCALE := "https://tailscale.com/download"
const POLL_SECONDS := 2.0
const QR_SIZE := 192

## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
## Off in tests: nothing runs the CLI; answers come through [method apply_status] and the rest.
var cli_enabled := true
var switch_row: UI.SwitchRow
var where: Label
## The next step for the address: "get", "serve" or "copy" (see [member tailnet_mode]).
var tailnet_button: Button
var tailnet_mode := ""
## Keep Remote available when Joinery is closed (`joinery remote install-helper`).
var helper_row: UI.SwitchRow
## Keep this machine awake on power while Remote is on (`joinery remote keep-awake`).
var awake_row: UI.SwitchRow
var pair_button: Button
var pair_box: VBoxContainer
var qr: TextureRect
var code: Label
var pair_note: Label
var devices: DEVICES
var _ask: Node
var _serve_ask: Node
var _address := ""
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
	where.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	add_child(_inset(where))
	tailnet_button = UI.secondary("")
	tailnet_button.name = "Tailnet"
	tailnet_button.pressed.connect(_tailnet_step)
	UI_MOTION.press_feedback(tailnet_button)
	var step := HBoxContainer.new()
	step.add_child(tailnet_button)
	tailnet_button.visibility_changed.connect(func() -> void: step.visible = tailnet_button.visible)
	tailnet_button.hide()
	step.hide()
	add_child(_inset(step))
	helper_row = UI.switch_row("When Joinery is closed", "Your phone can still open your projects")
	helper_row.name = "HelperSwitch"
	helper_row.switch_button.accessibility_name = "Keep Remote available when Joinery is closed"
	helper_row.toggled.connect(
		func(on: bool) -> void:
			helper_row.set_checking()
			_run("toggle", ["remote", "install-helper" if on else "uninstall-helper", "--json"])
	)
	add_child(helper_row)
	awake_row = UI.switch_row("Keep this Mac awake", "While it's plugged in and Remote is on")
	awake_row.name = "AwakeSwitch"
	awake_row.toggled.connect(
		func(on: bool) -> void:
			awake_row.set_checking()
			_run("toggle", ["remote", "keep-awake", "on" if on else "off", "--json"])
	)
	add_child(awake_row)
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
	devices = DEVICES.new()
	devices.command.connect(_run)
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
	_address = str(data.get("url", ""))
	tailnet_mode = ""
	if not on:
		where.text = "Off. Paired devices stay paired."
	elif not bool(ts.get("installed", false)):
		where.text = "Your phone reaches this Mac through Tailscale. Install it here and on your phone."
		tailnet_mode = "get"
	elif not bool(ts.get("running", false)):
		where.text = str(ts.get("message", "Tailscale isn't connected."))
	elif bool(data.get("funnel", false)):
		# Tailscale Funnel would make it public: Remote refuses it, so nothing to copy.
		where.text = (
			"Tailscale Funnel puts Remote on the public internet, and Remote refuses that. "
			+ "Turn Funnel off for it in Tailscale."
		)
	elif not bool(data.get("served", false)):
		# Only an address `tailscale serve` actually serves is shown or put in a QR code.
		where.text = "One step left: put Remote on your tailnet, so your phone can open it."
		tailnet_mode = "serve"
	else:
		where.text = "On your phone: " + _address
		tailnet_mode = "copy"
	tailnet_button.text = (
		{"get": "Get Tailscale", "serve": "Put it on my tailnet", "copy": "Copy address"}
		. get(tailnet_mode, "")
	)
	tailnet_button.visible = not tailnet_mode.is_empty()
	helper_row.visible = on
	awake_row.visible = on
	helper_row.set_on(bool(data.get("helper", false)))
	helper_row.set_disabled_reason("")
	awake_row.set_on(bool(data.get("keep_awake", false)))
	awake_row.set_disabled_reason("")
	# A QR code carries only an address Tailscale serves: pairing waits for it.
	var served := bool(data.get("served", false))
	pair_button.disabled = not on or not served
	pair_button.tooltip_text = (
		"Turn Remote on first" if not on else ("" if served else "Put Remote on your tailnet first")
	)
	devices.set_devices(data.get("devices", []))


## The button under the address: get Tailscale, serve on the tailnet (asked first), or copy.
func _tailnet_step() -> void:
	match tailnet_mode:
		"get":
			OS.shell_open(GET_TAILSCALE)
		"copy":
			DisplayServer.clipboard_set(_address)
			tailnet_button.text = "Copied"
			UI_MOTION.pulse(tailnet_button)
		"serve":
			if is_instance_valid(_serve_ask):
				return
			_serve_ask = ASK.new()
			_serve_ask.call(
				"setup",
				(
					"Run `tailscale serve` for Remote? Only devices signed in to your "
					+ "tailnet can reach it, over HTTPS. You can undo it with "
					+ "`tailscale serve reset`."
				),
				"Put it on",
				"Cancel"
			)
			_serve_ask.connect(
				"answered",
				func(ok: bool) -> void:
					if ok:
						tailnet_button.disabled = true
						tailnet_button.text = "Putting it on…"
						_run("tailscale", ["remote", "tailscale", "--run", "--yes", "--json"])
			)
			tailnet_button.get_parent().get_parent().add_sibling(_serve_ask)


func _on_toggled(on: bool) -> void:
	switch_row.set_checking()
	_run("toggle", ["remote", "start" if on else "stop", "--json"])
	if not on:
		_end_pairing()


func _pair() -> void:
	_end_pairing()
	pair_note.text = "Making a code…"
	pair_box.show()
	# The PNG goes in Remote's private folder, never a shared temp one: it holds the code.
	_run("pair", ["remote", "pair", "--json", "--no-wait", "--qr"])


## Show a `joinery remote pair --json --no-wait` answer: the QR code, the code, how long.
func apply_pairing(data: Dictionary) -> void:
	if data.has("error"):
		pair_note.text = str(data["error"])
		return
	_pairing_id = str(data.get("pairing_id", ""))
	_pairing_until = Time.get_unix_time_from_system() + 300.0
	var picture := str(data.get("qr", ""))
	var image := Image.load_from_file(picture) if not picture.is_empty() else null
	qr.texture = ImageTexture.create_from_image(image) if image != null else null
	if not picture.is_empty():
		# Shown from memory from now on: the file with the code goes at once.
		DirAccess.remove_absolute(picture)
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
		"status":
			apply_status(data)
		"toggle":
			# Each switch answers with only its own part: read the whole state again.
			if data.has("error"):
				where.text = str(data["error"])
			refresh()
		"pair":
			apply_pairing(data)
		"requests":
			apply_requests(data)
		"confirm", "revoke", "scopes", "forget", "tailscale":
			tailnet_button.disabled = false
			if data.has("error"):
				where.text = str(data["error"])
			refresh()
		"log":
			devices.set_activity(data.get("entries", []))
