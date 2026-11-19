@tool
extends RefCounted
## Shapes of Joinery's own lines in the chat (chat_history.add_notice):
## - a divider: "New session · summary carried over" as centred faint text between two
##   hairlines, so a fresh session reads as a seam, not as news;
## - an expandable line: a notice whose text has a second part (after a line break) shows its
##   first line only, ellipsized, with a › that opens the rest under it ("Change not applied ·
##   room.tscn uses lamp.glb… ›");
## - a policy stop (Rule Zero): a box of its own, never a plain line, saying what was attempted
##   and offering the next step, agreeing a way that uses the project's own tools. It has no
##   Retry: the same request would be stopped again.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
## How a Rule Zero stop starts (bridge/stdio.py's policy_stop).
const POLICY_STOP := "Stopped by project policy: "
## What the policy stop's next step puts in the message box, for the user to send or change.
const TOOL_WAY := (
	"Let's make it with the project's own tools instead (Blender, Krita, Material Maker, "
	+ "Surge XT or SuperCollider). Say which tool and steps you'd use before you start."
)


## A divider ([param kind] "divider") or a policy stop ("policy"); [param ask] puts words in
## the message box.
static func special(kind: String, text: String, ask: Callable) -> Control:
	return divider(text) if kind == "divider" else policy_stop(text, ask)


## A Rule Zero stop: what was attempted, why it stopped, and one way on.
static func policy_stop(text: String, ask: Callable) -> PanelContainer:
	var box := PanelContainer.new()
	box.name = "PolicyStop"
	box.accessibility_name = text
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	STYLE.on_theme_overrides(
		box,
		func() -> void:
			var danger := box.get_theme_color("danger", "Joinery")
			var panel := StyleBoxFlat.new()
			panel.bg_color = box.get_theme_color("surface", "Joinery").lerp(danger, 0.06)
			panel.border_color = Color(danger, 0.45)
			panel.set_border_width_all(maxi(1, roundi(UI_THEME.unit(box))))
			panel.set_corner_radius_all(UI_THEME.radius(box, "md"))
			panel.set_content_margin_all(float(UI_THEME.space(box, 3)))
			box.add_theme_stylebox_override("panel", panel)
	)
	var column := VBoxContainer.new()
	column.theme_type_variation = "JoineryRow"
	box.add_child(column)
	var head := HBoxContainer.new()
	head.theme_type_variation = "JoineryRowTight"
	column.add_child(head)
	var mark := STYLE.glyph("danger")
	STYLE.set_glyph(mark, "stop", "danger")
	mark.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(mark)
	head.add_child(STYLE.card_title("Stopped by project policy"))
	var reason := text.trim_prefix(POLICY_STOP).strip_edges()
	var said := UI.label(
		(
			(
				"Attempted: %s Assets here are made by working in the project's own tools, never "
				% (reason.left(1).to_upper() + reason.substr(1))
			)
			+ "by a generative service."
		),
		"JoineryCaption",
		true
	)
	said.name = "Attempted"
	column.add_child(said)
	var next := UI.secondary("Agree a tool-based way")
	next.name = "ToolWay"
	next.tooltip_text = "Put a request for a way with the project's tools in the message box"
	next.accessibility_name = next.tooltip_text
	next.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	UI.bind_height(next, "sm")
	UI_MOTION.press_feedback(next)
	next.pressed.connect(func() -> void: ask.call(TOOL_WAY))
	column.add_child(next)
	UI_MOTION.fade_in(box)
	return box


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
	# Level with the notice's first line (STYLE.first_line), however far its words wrap.
	button.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	UI.bind_height(button, "xs")
	button.pressed.connect(pressed)
	row.add_child(button)
