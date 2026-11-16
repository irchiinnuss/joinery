@tool
extends RefCounted
## Open a project file named in the chat (a link on the work trail, sidebar_trail_link.gd)
## where it belongs in the editor: a scene opens as the edited scene, a script in the script
## editor, a native source (.blend, .kra, .scd…) in the Assets view under the tool that made it
## (the same path as a message's chip: "Open in Blender" there), and any other imported asset
## is selected in the FileSystem dock and shown in the Inspector. A file only in a change that
## waits for review is shown in the Review panel's preview, as the change card's Preview does.

const SCENES := ["tscn", "scn"]
const SCRIPTS := ["gd", "cs"]
## Native sources: their own tool opens them, from the Assets view.
const SOURCES := ["blend", "kra", "ora", "xcf", "psd", "aup3", "aup", "scd", "sc", "ptex"]
const NOT_YET := "It opens once the change is ready to review."

## Tests: called with (how, path) instead of the editor; how is "scene", "script", "source",
## "asset" or "review".
static var recorder := Callable()


## How [param path] opens: "scene", "script", "source" or "asset".
static func how(path: String) -> String:
	var extension := path.get_extension().to_lower()
	if extension in SCENES:
		return "scene"
	if extension in SCRIPTS:
		return "script"
	if extension in SOURCES:
		return "source"
	return "asset"


## Open [param path] (res://) for [param sidebar] (journeyman_dock.gd).
static func open(path: String, sidebar: Control) -> void:
	if not path.begins_with("res://"):
		return
	if not FileAccess.file_exists(path):
		# Only in a change waiting for review: its item in the Review panel's preview.
		var review: Variant = sidebar.get("_review")
		if bool(sidebar.get("_review_pending")) and review is Object:
			if not _record("review", path):
				(review as Object).call("preview", path.trim_prefix("res://"))
		else:
			sidebar.call("show_status", NOT_YET, 4.0)
		return
	var kind := how(path)
	if _record(kind, path) or not Engine.is_editor_hint():
		return
	match kind:
		"scene":
			EditorInterface.open_scene_from_path(path)
		"script":
			var script := load(path) as Script
			if script != null:
				EditorInterface.set_main_screen_editor("Script")
				EditorInterface.edit_script(script)
		"source":
			sidebar.emit_signal("target_opened", path)
		_:
			EditorInterface.select_file(path)
			var resource := load(path)
			if resource != null:
				EditorInterface.edit_resource(resource)


static func _record(kind: String, path: String) -> bool:
	if not recorder.is_valid():
		return false
	recorder.call(kind, path)
	return true
