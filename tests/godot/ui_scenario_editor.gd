@tool
extends EditorPlugin
## The UI scenarios (ui_scenarios.gd) inside a real editor: the Joinery plugin's own sidebar,
## chat bridge and wiring (review flow, Review tab, decisions), with the scripted chat host in
## place of the provider. Installed by run_ui_scenarios.py --editor into a disposable testbed
## copy beside ui_checks.gd, ui_scenarios.gd and playtest_fixture.gd. JOINERY_VISUAL_THEME names
## the seeded theme. Ends with "ui scenarios editor: ok" or "… FAILED".

var _plugin: EditorPlugin
var _dock: Control


func _enter_tree() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	_run.call_deferred()


func _run() -> void:
	await get_tree().create_timer(3.0).timeout
	for node: Node in get_tree().root.find_children("*", "EditorPlugin", true, false):
		if node.get_script() == load("res://addons/joinery/plugin.gd"):
			_plugin = node as EditorPlugin
	if _plugin == null:
		print("ui scenarios editor: FAILED (no Joinery plugin)")
		get_tree().quit(1)
		return
	_dock = _plugin.get("_dock")
	EditorInterface.set_main_screen_editor("3D")
	var lib: RefCounted = (
		load(get_script().resource_path.get_base_dir().path_join("ui_scenarios.gd")).new(get_tree())
	)
	var theme := OS.get_environment("JOINERY_VISUAL_THEME")
	lib.set("theme_name", "editor-" + (theme if not theme.is_empty() else "dark"))
	lib.set("width", int(_dock.size.x))
	lib.set("build", _fresh_sidebar)
	var ok: bool = await lib.call("run")
	var chat: Node = _plugin.get("_chat")
	if chat != null:
		chat.call("close")
	await get_tree().process_frame
	print("ui scenarios editor: ", "ok" if ok else "FAILED")
	get_tree().quit(0 if ok else 1)


## The plugin's own sidebar, cleared for the next scenario: a new chat, nothing on screen.
func _fresh_sidebar() -> Array:
	var chat: Node = _plugin.get("_chat")
	if chat != null:
		chat.call("close")
	var sessions: Node = _dock.get("_chats")
	var queue: Object = (_dock.get("_transcript") as Node).get("queue")
	for card: Variant in queue.call("cards") if queue.has_method("cards") else []:
		queue.call("remove", card)
	sessions.call("new_chat")
	_dock.call("hide_edit_review")
	# A change left waiting by the last scenario is dropped in the plugin too (a user can't
	# send while one waits: new messages queue), so it never shapes the next scenario.
	_plugin.set("_file_review", {})
	_plugin.set("_deciding", false)
	_dock.set("_turn_log", null)
	_dock.set("_active_card", null)
	(_dock.get("_transcript") as Node).call("clear")
	return [_dock, chat]
