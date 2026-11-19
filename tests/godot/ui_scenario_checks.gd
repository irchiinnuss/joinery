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


## The change card's quiet buttons: Preview and Try other ways look alike, on one row.
static func quiet_actions(s: Variant) -> void:
	var row: Node = s.transcript().find_child("QuietActions", true, false)
	var preview: Button = row.find_child("Preview", true, false) if row != null else null
	var ways: Button = row.get_child(-1) if row != null else null
	s.check(
		(
			preview != null
			and ways != null
			and preview.theme_type_variation == ways.theme_type_variation
			and absf(preview.get_global_rect().position.y - ways.get_global_rect().position.y) < 1.0
		),
		"Preview and Try other ways are alike quiet buttons, side by side"
	)


## After an accepted change: Revert asks first, in place under the line, with Cancel focused,
## and Escape answers Cancel; right after the revert the toast offers Undo, which puts the
## change back. Joinery's own revert is stood in for: the scripted change has no files.
static func revert_undo(s: Variant) -> void:
	var card: Control = s.card()
	var line: Control = card.get("_line")
	var revert: Button = line.get("revert")
	s.check(card.get("state") == "accepted" and revert.is_visible_in_tree(), "Revert is offered")
	var transcript: Object = s.transcript()
	for name: String in ["revert_requested", "unrevert_requested"]:
		for link: Dictionary in transcript.get_signal_connection_list(name):
			transcript.disconnect(name, link["callable"])
	var asked: Array[String] = []
	transcript.connect("revert_requested", func(id: String) -> void: asked.append(id))
	transcript.connect(
		"unrevert_requested", func(id: String, _o: bool) -> void: asked.append("undo " + id)
	)
	(transcript as ScrollContainer).ensure_control_visible(revert)
	await s.frames(2)
	await s.click(revert)
	await s.frames(3)
	var ask: Control = line.call("asking")
	s.check(ask != null and s.on_screen(ask), "Revert asks first, in place under the line")
	var words := str(ask.get("text").text) if ask != null else ""
	s.check(words.ends_with("You can undo this."), "It says what goes back: " + words)
	s.check(ask != null and ask.get("cancel").has_focus(), "Focus starts on Cancel")
	s.check(asked.is_empty(), "Nothing is reverted before the answer")
	await s.shot("revert-ask")
	s.key(KEY_ESCAPE)
	await s.frames(3)
	s.check(line.call("asking") == null and asked.is_empty(), "Escape answers Cancel")
	await s.click(revert)
	await s.frames(3)
	ask = line.call("asking")
	if ask != null:
		await s.click(ask.get("action"))
	s.check(asked.size() == 1, "Revert in the question reverts the change")
	var id := str(card.get("transaction_id"))
	var reverts: Object = s.dock.get("_reverts")
	reverts.call("on_reverted", id, true, "")
	await s.frames(3)
	var toast: Control = reverts.get("toast")
	s.check(s.on_screen(toast.get("undo")), "Right after the revert, the toast offers Undo")
	s.check((line.get("undo_revert") as Control).is_visible_in_tree(), "The card: Undo revert")
	await s.shot("reverted-undo")
	await s.click(toast.get("undo"))
	s.check(asked.back() == "undo " + id, "Undo puts that change back")
	s.check(not toast.visible, "The toast goes once used")
	reverts.call("_on_unreverted", id, true, "", [], PackedStringArray())
	await s.frames(3)
	s.check(card.get("state") == "accepted" and revert.is_visible_in_tree(), "Restored")
	await s.shot("restored")
