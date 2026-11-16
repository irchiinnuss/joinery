@tool
extends RefCounted
## The game brief and the look's layout, for the sidebar (journeyman_dock.gd): "/brief" opens
## design/brief.md read-only on the sidebar's page, with each feature's mark from
## `joinery brief status`; a draft of the look in the first layout is brought to the new one
## without asking, once per version.

const ART_DIALOG := preload("art_review_dialog.gd")
const BRIEF_PATH := "res://design/brief.md"
const PAGE_META := "joinery_art_page"

var _dock: Control
var _slash: Node
## Look versions already brought to the new layout, by sha256.
var _upgraded := {}


func _init(dock: Control, slash: Node) -> void:
	_dock = dock
	_slash = slash
	_slash.connect("bible_upgraded", _on_bible_upgraded)
	_slash.connect("brief_status_ready", _on_brief_status)


## "/brief": the game brief, read-only. Without one, offer to write it from the setup answers.
func open() -> void:
	var transcript: Object = _dock.get("_transcript")
	if not FileAccess.file_exists(BRIEF_PATH):
		var row: Array[Control] = []
		var write := func() -> void:
			transcript.call("remove_offer", row[0])
			_slash.call("write_brief")
		row.append(
			transcript.call(
				"add_offer", "This project has no game brief yet.", [["Write the brief", write]]
			)
		)
		return
	var text := FileAccess.get_file_as_string(BRIEF_PATH)
	ART_DIALOG.show_document(_dock, "Game brief", text, "Change the game brief: ")
	var page := _page()
	if page != null:
		var check := Callable(_slash, "brief_status")
		if not page.is_connected("status_check_requested", check):
			page.connect("status_check_requested", check)
		# The marks come from what is already known; Check now gathers fresh evidence.
		_slash.call("brief_status", false)


## Bring a draft of the look in the first layout to the new one (the art page does this on
## open). Tried once per [param version]; a failure leaves the draft as it is.
func upgrade(version: String) -> void:
	if not _upgraded.has(version):
		_upgraded[version] = true
		_slash.call("upgrade_bible")


## The sidebar's page when it shows the brief's features, else null.
func _page() -> Control:
	var page: Variant = _dock.get_meta(PAGE_META) if _dock.has_meta(PAGE_META) else null
	if page is Control and (page as Control).has_method("show_feature_status"):
		return page
	return null


func _on_brief_status(report: Dictionary) -> void:
	var page := _page()
	if page != null and page.visible:
		page.call("show_feature_status", report)


func _on_bible_upgraded(ok: bool, _message: String) -> void:
	var page: Variant = _dock.get_meta(PAGE_META) if _dock.has_meta(PAGE_META) else null
	if ok and page is Control and (page as Control).visible:
		_dock.emit_signal("art_review_requested")
