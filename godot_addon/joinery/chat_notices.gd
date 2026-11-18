@tool
extends RefCounted
## Two shapes of Joinery's own lines in the chat (chat_history.add_notice):
## - a divider: "New session · summary carried over" as centred faint text between two
##   hairlines, so a fresh session reads as a seam, not as news;
## - an expandable line: a notice whose text has a second part (after a line break) shows its
##   first line only, ellipsized, with a › that opens the rest under it ("Change not applied ·
##   room.tscn uses lamp.glb… ›").

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
## How a Rule Zero stop starts (bridge/stdio.py's policy_stop).
const POLICY_STOP := "Stopped by project policy: "


## A centred faint [param text] between two hairlines.
static func divider(text: String) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.name = "Divider"
	row.theme_type_variation = "JoineryRow"
	row.accessibility_name = "Status: " + text
	for side: int in 2:
		var line := HSeparator.new()
		line.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		line.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		line.mouse_filter = Control.MOUSE_FILTER_IGNORE
		STYLE.on_theme_overrides(
			line,
			func() -> void:
				var box := StyleBoxLine.new()
				box.color = line.get_theme_color("border_subtle", "Joinery")
				box.thickness = maxi(1, roundi(UI_THEME.unit(line)))
				line.add_theme_stylebox_override("separator", box)
		)
		row.add_child(line)
		if side == 0:
			var words := UI.label(text, "JoineryFaint")
			words.name = "Text"
			words.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
			words.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
			words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
			row.add_child(words)
	UI_MOTION.fade_in(row)
	return row


## Show [param head] on [param label] (one line, ellipsized; its tooltip the whole) with a ›
## at the end of [param row] that opens [param rest] under it, and closes it again.
static func expandable(row: HBoxContainer, label: Label, head: String, rest: String) -> void:
	label.text = head
	label.autowrap_mode = TextServer.AUTOWRAP_OFF
	label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	label.tooltip_text = head + "\n" + rest
	label.mouse_filter = Control.MOUSE_FILTER_PASS
	var more := UI.icon_button("disclosure_closed", "Details")
	more.toggle_mode = true
	more.accessibility_name = "Show the details"
	more.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(more)
	var detail := UI.label(rest, "JoineryCaption", true)
	detail.name = "Detail"
	detail.hide()
	more.toggled.connect(
		func(open: bool) -> void:
			UI.bind_icon(more, "disclosure_open" if open else "disclosure_closed")
			if detail.get_parent() == null:
				var holder := row.get_parent()
				holder.add_child(detail)
				holder.move_child(detail, row.get_index() + 1)
			if open:
				UI_MOTION.reveal(detail)
			else:
				detail.hide()
	)
	row.tree_exiting.connect(
		func() -> void:
			if is_instance_valid(detail) and detail.get_parent() == null:
				detail.free()
	)


## "Retry" at the end of an error notice's [param row], like the card's own Retry.
static func retry(row: Control, pressed: Callable) -> void:
	var button := UI.link("Retry")
	button.name = "Retry"
	button.tooltip_text = "Send this request again"
	button.accessibility_name = button.tooltip_text
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	UI.bind_height(button, "xs")
	button.pressed.connect(pressed)
	row.add_child(button)
