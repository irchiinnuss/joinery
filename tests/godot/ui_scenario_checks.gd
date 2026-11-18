extends RefCounted
## Checks of the sidebar that take more than a line or two, for ui_scenario_cases.gd: each takes
## the runner ([param s], ui_scenarios.gd) and records its checks there.


## A long reply's blocks: text wraps inside the column, each code block is a box of its own that
## keeps its lines, scrolls sideways when wide, is mono without ligatures, and lets the wheel
## scroll the conversation.
static func reply_parts(s: Variant, scroll: ScrollContainer) -> void:
	# Wrapped, never past the column: the path is one unbroken word.
	var messages: Array = s.transcript().get("_messages")
	var column := scroll.get_global_rect()
	var parts: Array = messages[-1]["parts"]
	var inside := true
	var wraps := true
	var code: Array[RichTextLabel] = []
	for part: Control in parts:
		inside = inside and part.get_global_rect().end.x <= column.end.x + 1.0
		if part.has_meta("joinery_code"):
			code.append(part.get_meta("joinery_code"))
		elif part is RichTextLabel:
			var text := part as RichTextLabel
			wraps = wraps and text.get_content_width() <= text.size.x + 1.0
	s.check(inside, "The reply and its code stay inside the conversation")
	s.check(wraps, "The long path wraps")
	s.check(code.size() == 4, "Each code block is a box of its own (%d)" % code.size())
	if code.is_empty():
		return
	var lines := code[0].get_line_count()
	s.check(lines == 3, "Code keeps its lines: never wrapped mid-signature (%d lines)" % lines)
	var box := code[0].get_parent() as ScrollContainer
	var wider := code[0].get_combined_minimum_size().x > box.size.x
	s.check(
		not wider or box.get_h_scroll_bar().visible,
		"Code wider than the conversation scrolls sideways"
	)
	var mono := code[0].get_theme_font("normal_font") as FontVariation
	var liga := TextServerManager.get_primary_interface().name_to_tag("liga")
	s.check(
		mono != null and int(mono.opentype_features.get(liga, 1)) == 0,
		"Code is mono with ligatures off: -> stays as typed"
	)
	# The wheel over a code block still scrolls the conversation.
	var target: Control = code[1]
	scroll.ensure_control_visible(target)
	await s.frames(2)
	var before := scroll.scroll_vertical
	var wheel := InputEventMouseButton.new()
	wheel.button_index = MOUSE_BUTTON_WHEEL_DOWN
	wheel.pressed = true
	wheel.factor = 1.0
	wheel.position = target.get_global_rect().get_center()
	wheel.global_position = wheel.position
	s.tree.root.push_input(wheel)
	await s.frames(3)
	s.check(
		scroll.scroll_vertical > before,
		"The wheel over code scrolls the conversation (%d -> %d)" % [before, scroll.scroll_vertical]
	)


## A Rule Zero stop: a notice of its own, not a reply, saying what was attempted, with a next
## step that waits in the message box rather than being sent.
static func policy_stop(s: Variant) -> void:
	var stop: Control = s.transcript().find_child("PolicyStop", true, false)
	s.check(stop != null and stop.is_visible_in_tree(), "The stop is a notice of its own")
	s.check(not s.reply_shown().contains("project policy"), "The stop isn't shown as a reply")
	var said: String = s.visible_text()
	s.check(said.contains("generative service was called"), "It says what was attempted")
	var next: Button = s.transcript().find_child("ToolWay", true, false)
	s.check(next != null and s.on_screen(next), "It offers to agree a tool-based way")
	await s.shot("stopped")
	if next != null:
		var cards := (s.transcript().get("_cards") as Array).size()
		await s.click(next)
		s.check(s.prompt().text.contains("own tools"), "The next step waits in the message box")
		s.check((s.transcript().get("_cards") as Array).size() == cards, "…and is not sent unasked")
		s.prompt().text = ""
