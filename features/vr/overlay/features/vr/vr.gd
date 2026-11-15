extends Node
## VR through OpenXR, which is built into Godot, with a desktop fallback.
##
## At start this looks for Godot's OpenXR interface. When the project setting
## `xr/openxr/enabled` found a runtime and a headset, the interface is ready:
## the game renders to the headset and the XR rig (xr_rig.tscn) is carried on
## the local player of every level. Otherwise the game plays on the desktop
## with mouse, keyboard or pad and a notice says so once; nothing fails.
## `-- --vr-simulate` carries the rig without a headset (tests).
##
## A project that starts its own XR session (WebXR in a web export, where
## Godot has no OpenXR) hands it over with `adopt_interface()` once it runs,
## and calls `release_interface()` when it ends.
##
## Player settings (section `vr` in `Settings`, see DEFAULTS):
##   smooth_move  bool   walk with the move stick
##   move_speed   float  metres a second, 1-3
##   snap_turn    int    degrees per turn, 30 or 45
##   teleport     bool   push the turn stick forward to aim, let go to jump
##   vignette     float  0-1, darkens the edges of the view while moving
##   seated       bool   lifts the view for seated play
##   left_handed  bool   swaps the hands: the right stick walks
##
## With save-load, a save keeps which way the rig was turned (extra "vr").
## With the controller feature, its deadzone and rumble settings apply here.

signal mode_changed(mode: StringName)
signal rig_attached(rig: Node3D)

const SECTION := "vr"
const DEFAULTS := {
	"smooth_move": true,
	"move_speed": 2.0,
	"snap_turn": 45,
	"teleport": true,
	"vignette": 0.6,
	"seated": false,
	"left_handed": false,
}
## Modes: a headset is rendering, the rig runs without one (tests), or the
## game plays on the desktop.
const XR := &"xr"
const SIMULATED := &"simulated"
const DESKTOP := &"desktop"
const SIMULATE_ARG := "--vr-simulate"
const NOTICE := "No VR headset found: playing on the desktop."
## How long the notice shows, in the top-right corner clear of the HUD.
const NOTICE_SECONDS := 3.0
## The playtest bot and store-media runs pass this: no notice over their frames.
const QUIET_ARG := "--joinery-quiet-notices"
const RIG_SCENE := "res://features/vr/xr_rig.tscn"
const SAVE_EXTRA := "vr"
const ComfortPage := preload("res://features/vr/comfort_page.gd")
const MenuPanel := preload("res://features/vr/menu_panel.gd")

var mode: StringName = DESKTOP
## Godot's OpenXR interface (null when the engine has none), or the interface
## handed over with adopt_interface() until release_interface().
var interface: XRInterface = null
## The rig carried on the local player, or null.
var rig: Node3D = null
## Why the game is on the desktop ("" in VR).
var reason: String = ""

var _noticed: bool = false
var _panel: Node3D = null
var _pending_yaw: Variant = null


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	Menus.add_settings_page(&"vr", "VR comfort", ComfortPage.build, 50)
	GameEvents.level_started.connect(_on_level_started)
	GameEvents.menu_opened.connect(_on_menu_opened)
	_start()
	_register_save.call_deferred()


## True when the rig is in use (a headset, or simulated for tests).
func is_active() -> bool:
	return mode != DESKTOP


func setting(key: String) -> Variant:
	return Settings.get_value(SECTION, key, DEFAULTS.get(key))


func set_setting(key: String, value: Variant) -> void:
	Settings.set_value(SECTION, key, value)


## The stick deadzone: the controller feature's when it is there.
func deadzone() -> float:
	if get_node_or_null(^"/root/Controller") != null and Settings.has_value("input", "deadzone"):
		return clampf(float(Settings.get_value("input", "deadzone")), 0.05, 0.6)
	return preload("res://features/vr/comfort.gd").STICK_DEADZONE


## The world-space menu panel shown in the headset, or null on the desktop.
func panel() -> Node3D:
	return _panel


## Takes over an XR interface that something else started (a WebXR session,
## for example): the game renders to it, the menu panel is made, the rig is
## carried on the local player when a level is running, and `mode_changed`
## fires once. Whoever started the session keeps it. Handing over the
## interface already in use does nothing.
func adopt_interface(started: XRInterface) -> void:
	if started == null or (mode == XR and interface == started):
		return
	interface = started
	print("VR: headset ready through %s" % started.get_name())
	_switch(XR, "")
	var level: Node = Game.current_level()
	if level != null:
		var player: Node3D = Game.local_player() as Node3D
		attach_rig(player if player != null else level as Node3D)


## Leaves XR after a session that was started elsewhere has ended: back to the
## desktop with the player's own controls and camera, no rig and no menu
## panel, and `mode_changed` fires once. `why` becomes `reason`. Does nothing
## when the game is not in XR.
func release_interface(why: String = "") -> void:
	if mode != XR:
		return
	if is_instance_valid(rig):
		rig.call(&"put_down")
		rig.queue_free()
	rig = null
	interface = null
	# The player has just taken the headset off: no "no headset" notice.
	_noticed = true
	var text: String = why if not why.is_empty() else "the XR session ended"
	print("VR: %s; playing on the desktop" % text)
	_switch(DESKTOP, text)


func _start() -> void:
	interface = XRServer.find_interface("OpenXR")
	if interface != null and interface.is_initialized():
		# The headset paces frames; the window's vsync would slow it down.
		DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
		_match_refresh_rate()
		print("VR: headset ready through %s" % interface.get_name())
		_switch(XR, "")
	elif SIMULATE_ARG in OS.get_cmdline_user_args():
		print("VR: simulated rig, no headset")
		_switch(SIMULATED, "simulated")
	else:
		var why: String = (
			"no OpenXR interface in this engine"
			if interface == null
			else "no OpenXR runtime or headset"
		)
		print("VR: %s; playing on the desktop" % why)
		_switch(DESKTOP, why)


## The one place the mode changes: in XR the game renders to the headset, the
## menu panel lives while the rig is in use, and listeners hear of it once.
func _switch(to: StringName, why: String) -> void:
	var was_xr: bool = mode == XR
	mode = to
	reason = why
	if mode == XR:
		get_viewport().use_xr = true
	elif was_xr:
		get_viewport().use_xr = false
	if is_active() and _panel == null:
		_panel = MenuPanel.new()
		_panel.name = "MenuPanel"
		add_child(_panel)
	elif not is_active() and _panel != null:
		_panel.queue_free()
		_panel = null
	mode_changed.emit(mode)


func _match_refresh_rate() -> void:
	# No OpenXRInterface type here: web builds have no OpenXR module, and the
	# type name alone stops this script, and the VR autoload, from loading.
	if interface == null or not interface.has_method(&"get_display_refresh_rate"):
		return
	var rate: float = float(interface.call(&"get_display_refresh_rate"))
	if rate >= 60.0:
		Engine.physics_ticks_per_second = roundi(rate)


func _notice() -> void:
	if _noticed or is_active():
		return
	_noticed = true
	if QUIET_ARG in OS.get_cmdline_user_args():
		return
	# A core from before corner toasts takes two arguments.
	if Menus.get_method_argument_count(&"toast") >= 3:
		Menus.call(&"toast", NOTICE, NOTICE_SECONDS, true)
	else:
		Menus.call(&"toast", NOTICE, NOTICE_SECONDS)


func _on_menu_opened(_menu: Control) -> void:
	_notice()
	if _panel != null:
		_panel.call(&"show_in_front", rig)


func _on_level_started(level: Node) -> void:
	_notice()
	if not is_active():
		return
	var player: Node3D = Game.local_player() as Node3D
	attach_rig(player if player != null else level as Node3D)


## Carries a new rig on `holder` (the local player, or the level when there
## is no player) and returns it.
func attach_rig(holder: Node3D) -> Node3D:
	if holder == null:
		return null
	if is_instance_valid(rig):
		rig.queue_free()
	var scene: PackedScene = load(RIG_SCENE) as PackedScene
	rig = scene.instantiate() as Node3D
	rig.name = "XRRig"
	holder.add_child(rig)
	rig.call(&"carry", holder if holder.is_in_group(&"player") else null)
	if _pending_yaw != null:
		rig.call(&"set_turn", float(_pending_yaw))
		_pending_yaw = null
	rig_attached.emit(rig)
	return rig


func _register_save() -> void:
	var save_game: Node = get_node_or_null(^"/root/SaveGame")
	if save_game == null or not save_game.has_method(&"register"):
		return
	save_game.call(&"register", SAVE_EXTRA, _capture_save, _restore_save)


func _capture_save() -> Dictionary:
	if not is_instance_valid(rig):
		return {}
	return {"turn": float(rig.call(&"turn"))}


func _restore_save(data: Variant) -> void:
	if not (data is Dictionary) or not (data as Dictionary).has("turn"):
		return
	var turn: float = float((data as Dictionary)["turn"])
	if is_instance_valid(rig) and rig.is_inside_tree():
		rig.call(&"set_turn", turn)
	else:
		_pending_yaw = turn
