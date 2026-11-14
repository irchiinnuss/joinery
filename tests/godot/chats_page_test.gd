extends SceneTree
## The Chats list (chats_page.gd), headless: every row keeps the same height, padding and
## one-line title whatever its state. The open chat is only a background highlight; pointing at
## a row shows the pencil in the space the time uses, so nothing moves; no tooltip covers the
## next row.

const PAGE := preload("res://addons/joinery/chats_page.gd")
const NOW := 1_800_000_000
const LONG := "add two coffe mugs with textures on top of table and a saucer under each one"

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


## What a row looks like: its height, where its title starts, how wide it is, its lines.
func _geometry(row: Control) -> Dictionary:
	var title: Label = row.get("title")
	return {
		"height": row.size.y,
		"x": title.global_position.x - row.global_position.x,
		"width": title.size.x,
		"lines": title.get_visible_line_count(),
	}


func _run() -> void:
	root.size = Vector2i(340, 600)
	var page: PAGE = PAGE.new()
	page.size = Vector2(340, 600)
	root.add_child(page)
	var chats: Array[Dictionary] = [
		{"id": "open", "title": LONG, "provider": "codex", "updated": NOW - 86400},
		{"id": "other", "title": LONG, "provider": "codex", "updated": NOW - 86400},
		{"id": "short", "title": "Rope coils", "provider": "codex", "updated": NOW - 3 * 86400},
	]
	page.show_chats(chats, "open", NOW)
	for _frame in 6:
		await process_frame
	var open: Control = page.row_for("open")
	var other: Control = page.row_for("other")
	var short: Control = page.row_for("short")
	var plain := _geometry(other)
	_check(plain["lines"] == 1, "A long title is one line, ellipsized: %s" % [plain])
	var selected := _geometry(open)
	_check(selected == plain, "The open chat looks the same: %s vs %s" % [selected, plain])
	_check(_geometry(short)["height"] == plain["height"], "Short or long, one row height")
	_check(open.get("current") and not other.get("current"), "The open chat is known")
	var open_button: Button = open.get("_open")
	var other_button: Button = other.get("_open")
	_check(
		open_button.get_theme_stylebox("normal") != other_button.get_theme_stylebox("normal"),
		"The open chat is a background highlight"
	)
	# Its text keeps its padding inside the fill, and lines up with the date caption.
	_check(plain["x"] >= 4.0, "Padding before the title: %s" % [plain])
	var today: Label = page.find_child("Earlier", true, false)
	_check(
		is_equal_approx(today.global_position.x, (other.get("title") as Control).global_position.x),
		"The caption and the titles share one left edge"
	)
	_check((open.get("caption") as Label).modulate.a > 0.5, "And keeps its time")
	_check(open_button.tooltip_text == LONG, "A cut title's tooltip is its full text, nothing else")
	var whole: Button = short.get("_open")
	_check(whole.tooltip_text.is_empty(), "A whole title has no tooltip over the next row")
	_check(
		open_button.accessibility_description.contains("Codex"),
		"Who answered is still told: " + open_button.accessibility_description
	)
	# Pointed at: the pencil in the time's place, nothing else moves.
	for row: Control in [other, open]:
		row.call("set_pointed", true)
		await process_frame
		await process_frame
		var pointed := _geometry(row)
		_check(pointed == plain, "Pointing at a row moves nothing: %s vs %s" % [pointed, plain])
		var pencil: Button = row.get("_rename")
		var caption: Label = row.get("caption")
		_check(pencil.modulate.a > 0.5 and caption.modulate.a < 0.5, "The pencil shows")
		var slot := (row.get("_slot") as Control).get_global_rect()
		_check(
			slot.encloses(pencil.get_global_rect()) and slot.encloses(caption.get_global_rect()),
			"In the time's own space: %s, %s" % [pencil.get_global_rect(), slot]
		)
		row.call("set_pointed", false)
		await process_frame
		_check(pencil.modulate.a < 0.5 and caption.modulate.a > 0.5, "And the time is back")
		_check(
			pencil.focus_mode == Control.FOCUS_NONE or pencil.modulate.a > 0.5, "Unseen, unfocused"
		)
	page.queue_free()
	print("chats page: ok" if not _failed else "chats page: FAILED")
	quit(1 if _failed else 0)
