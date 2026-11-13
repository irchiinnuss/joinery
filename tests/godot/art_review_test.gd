extends SceneTree
## The look in a window: the five decided sections, the references board with its images,
## bible-check offers that fill the composer, a first-format draft brought to the new layout
## without asking, and "Approve look"; and the same look as a sidebar page (palette strip,
## reference grid, feel and tags, empty slots, the sticky footer), where only a click on Approve
## approves.

const DOCK_PATH := "res://addons/joinery/journeyman_dock.gd"
const DIALOG_PATH := "res://addons/joinery/art_review_dialog.gd"
const PAGE_PATH := "res://addons/joinery/art_page.gd"
const MOTION_PATH := "res://addons/joinery/ui_motion.gd"
const PHOTO := "art/reference/quarry.png"
const BIBLE := """# Art Bible — Quarry

## Vision
Calm, a little lonely, handmade.

## Visual style
- Overall look: stylised PBR, chunky bevels, soft rim light

## Palette
Warm wood, sea blue accent.

## Audio
Soft synths, wooden foley.

## Camera
> e.g. Perspective, FOV 70
Perspective, FOV 60, 5 m behind.

## Never
- Neon, photo-real faces, gore
"""

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _event(version: int) -> Dictionary:
	return {
		"kind": "art_review",
		"text": BIBLE,
		"sha256": "abc",
		"approved": false,
		"version": version,
		"references":
		[
			{
				"id": "R1",
				"reference": "my quarry photo",
				"kind": "photo",
				"take": "warm stone against a cold sky",
				"avoid": "the haze",
				"file": PHOTO
			},
			{
				"id": "R2",
				"reference": "Hob",
				"kind": "game",
				"take": "chunky silhouettes",
				"avoid": "its saturated greens",
				"file": ""
			},
		],
		"palette":
		[
			{"role": "Primary", "hex": "#8a5a3b", "use": "walls and wood"},
			{"role": "Accent", "hex": "#2e86ab", "use": "interactables only"},
			{"role": "Shadow tint", "hex": "#2b2d42", "use": ""},
		],
		"gaps": [],
		"suggestions":
		[
			"Before approving the bible: the palette section is not set yet. " + "Ask about it.",
			"This bible uses the first format. Offer the upgrade: it keeps every line.",
			"When adding sound: the audio section is open. Ask about it.",
		],
	}


func _run() -> void:
	load(MOTION_PATH).set_enabled(false)
	root.size = Vector2i(1100, 900)
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path("res://art/reference"))
	var picture := Image.create(64, 48, false, Image.FORMAT_RGBA8)
	picture.fill(Color(0.8, 0.6, 0.4))
	picture.save_png(ProjectSettings.globalize_path("res://" + PHOTO))
	var chat := load(DOCK_PATH).new() as Control
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	chat.set_chat_state("connected", "")
	var dialog_script: Script = load(DIALOG_PATH)
	var shown := str(
		(
			dialog_script
			. call(
				"to_bbcode",
				"# Brief\n<!-- How to write\nin this file -->\n## Pitch\nA calm <!-- no hype --> game.\n"
			)
		)
	)
	_check(not shown.contains("<!--") and not shown.contains("How to write"), "Comments are hidden")
	_check(shown.contains("[b]Pitch[/b]") and shown.contains("A calm  game."), "Text around stays")
	var approved := [0]
	var approve := func() -> void: approved[0] += 1

	# A first-format bible: the upgrade offer, the board, the offers, the decided sections.
	var dialog: ConfirmationDialog = dialog_script.open(
		chat, _event(1), Vector2i(900, 700), approve
	)
	await process_frame
	_check(dialog.get_ok_button().text == "Approve look", "The button reads Approve look")
	var board := dialog.find_child("Board", true, false)
	_check(board != null and board.get_child_count() == 2, "The references board lists both")
	if board != null and board.get_child_count() == 2:
		var photo := board.get_child(0)
		var image := photo.find_child("Picture", true, false) as TextureRect
		_check(image != null and image.texture != null, "A reference image shows on the board")
		_check(image.texture.get_width() == 64, "The board shows the file from art/reference")
		var labels: Array[String] = []
		for label: Node in photo.find_children("*", "Label", true, false):
			labels.append((label as Label).text)
		_check(labels.has("R1 · my quarry photo"), "Each image has its caption")
		_check(
			labels.has("Take: warm stone against a cold sky\nNot: the haze"),
			"The caption says what to take and what to leave"
		)
		var game := board.get_child(1).find_child("Picture", true, false) as TextureRect
		_check(game.texture == null, "A reference without a picture keeps its place")
	var rules := dialog.find_child("Rules", true, false)
	var feel := rules.find_child("Feel", true, false) as RichTextLabel
	_check(feel != null and feel.text.contains("Calm, a little lonely"), "Feel shows the vision")
	var never := rules.find_child("Never", true, false) as RichTextLabel
	_check(never != null and never.text.contains("Neon"), "Never shows the hard bans")
	var technical := dialog.find_child("Technical", true, false)
	_check(technical != null and not technical.body.visible, "Technical rules start folded")
	_check(technical.header.text == "Checked rules", "The technical rules are the checked rules")
	_check(dialog.find_child("Upgrade", true, false) == null, "No update offer: it just happens")
	_check(chat._slash._jobs.has("upgrade"), "A first-format draft is upgraded without asking")
	var deadline := Time.get_ticks_msec() + 30000
	while chat._slash._jobs.has("upgrade") and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(not chat._slash._jobs.has("upgrade"), "The update finishes")
	# This project has no bible file, so the update fails, quietly.
	_check(
		not Array(chat._transcript.notice_texts()).any(
			func(text: String) -> bool: return text.contains("update")
		),
		"A failed silent update says nothing in the chat"
	)
	var offers := dialog.find_child("Offers", true, false)
	var rows: Array = offers.get_children().filter(
		func(row: Node) -> bool: return row.has_meta("offer")
	)
	_check(rows.size() == 2, "Bible checks are one-line offers (the upgrade has its own)")
	_check(
		(
			str(rows[0].get_meta("offer"))
			== "Before approving the bible: the palette section is not set yet."
		),
		"An offer is one plain line"
	)
	(rows[0].find_child("Ask", true, false) as Button).pressed.emit()
	await process_frame
	_check(
		chat._prompt.text.contains("the palette section is not set yet"),
		"An offer fills the composer"
	)
	_check(not is_instance_valid(dialog) or dialog.is_queued_for_deletion(), "The dialog closes")
	_check(approved[0] == 0, "Nothing is approved by an offer")
	chat._prompt.clear()
	await process_frame

	# The same version is not upgraded twice.
	dialog = dialog_script.open(chat, _event(1), Vector2i(900, 700), approve)
	_check(not chat._slash._jobs.has("upgrade"), "A version is upgraded once")
	dialog.hide()
	dialog.queue_free()
	await process_frame

	# Given only the text, the dialog picks up the bridge's event when the sidebar hears it.
	chat.art_review_event = {}
	dialog = dialog_script.open(chat, BIBLE, Vector2i(900, 700), approve)
	_check(dialog.find_child("Board", true, false) == null, "No board before the event")
	chat.art_review_event = _event(2)
	await process_frame
	await process_frame
	_check(dialog.find_child("Board", true, false) != null, "The board arrives with the event")
	dialog.hide()
	dialog.queue_free()
	await process_frame

	# A current bible: no upgrade offer; the string form finds the bridge's event.
	chat.art_review_event = _event(2)
	dialog = dialog_script.open(chat, BIBLE, Vector2i(900, 700), approve)
	await process_frame
	_check(dialog.find_child("Upgrade", true, false) == null, "A current bible offers no update")
	_check(dialog.find_child("Board", true, false) != null, "The board comes from the event")
	dialog.get_ok_button().pressed.emit()
	await process_frame
	_check(approved[0] == 1, "Approve look approves")

	# An approved bible says so and can't be approved twice.
	var done := _event(2)
	done["approved"] = true
	dialog = dialog_script.open(chat, done, Vector2i(900, 700), approve)
	await process_frame
	_check(dialog.get_ok_button().disabled, "An approved bible can't be approved again")
	dialog.hide()
	dialog.queue_free()
	await process_frame
	await _page(chat)

	chat.queue_free()
	await process_frame
	print("art review: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)


## The sidebar page: opened by the sidebar's art request, it waits for the bridge (a skeleton
## after a second), shows the look, and approves only from a click on Approve.
func _page(chat: Control) -> void:
	var pages: Script = load(PAGE_PATH)
	var bare := Control.new()
	_check(not pages.present(bare, _event(2), func() -> void: pass), "No sidebar, no page")
	bare.free()
	var page: Control = pages.attach(chat)
	_check(page != null and pages.attach(chat) == page, "The sidebar has one look page")
	var transcript: Control = chat._transcript
	var approved := [0]
	var approve := func() -> void: approved[0] += 1
	chat.art_review_requested.emit()
	await process_frame
	_check(page.visible and not transcript.visible, "/art opens the page in place of the chat")
	var skeleton := page.find_child("Skeleton", true, false) as Control
	_check(not skeleton.visible, "Under a second, the page shows nothing while it reads")
	_check(
		page.find_children("*", "Label", true, false).all(
			func(label: Node) -> bool: return not (label as Label).text.contains("Reading")
		),
		"No reading sentence"
	)
	await create_timer(1.2).timeout
	_check(skeleton.is_visible_in_tree(), "After a second, a skeleton shaped like the page")
	_check(not page.find_child("Foot", true, false).visible, "Nothing to approve while reading")
	var event := _event(2)
	event["sha256"] = "page"
	pages.present(chat, event, approve)
	await process_frame
	await process_frame
	_check(not page.find_child("Waiting", true, false).visible, "The look replaces the wait")
	_check(
		chat.find_children("*", "ConfirmationDialog", true, false).is_empty(),
		"No window opens over the sidebar"
	)
	var title := page.find_child("Title", true, false) as Label
	_check(title.text == "The look", "Titled The look")
	var texts: Array[String] = []
	for label: Node in page.find_children("*", "Label", true, false):
		if (label as Label).is_visible_in_tree():
			texts.append((label as Label).text)
	for gone: String in [
		"Draft, not approved yet", "To do first", "These rules use an older layout", "References"
	]:
		_check(not texts.has(gone), "Gone: " + gone)
	_check(page.find_child("Upgrade", true, false) == null, "No update offer on the page")
	var palette := page.find_child("Palette", true, false)
	_check(palette != null, "The palette leads, as colour")
	var swatches: Control = palette.get_node("Swatches")
	_check((swatches.get("roles") as Array).size() == 3, "One swatch per role")
	_check(swatches.call("_get_tooltip", Vector2(1, 1)).contains("#8a5a3b"), "Hover shows the hex")
	var grid := page.find_child("References", true, false) as GridContainer
	_check(grid != null and grid.get_child_count() == 3, "Two references and Add reference")
	var tile := grid.get_child(0)
	var picture := tile.find_child("Picture", true, false) as TextureRect
	_check(picture != null and picture.texture != null, "The photo shows as a picture")
	_check(
		(tile.find_child("Take", true, false) as Label).text == "warm stone against a cold sky",
		"A tile's caption is its take"
	)
	_check(str(tile.tooltip_text).contains("Not: the haze"), "What to leave is in the tooltip")
	_check(grid.get_child(1).find_child("StandIn", true, false) != null, "A game is an icon tile")
	_check(grid.get_child(2).name == "AddReference", "Add reference is the last tile")
	_check(
		(page.find_child("FeelLine", true, false) as Label).text.begins_with("Calm"),
		"The feel is one sentence"
	)
	var tags := page.find_child("Feel", true, false).find_child("Tags", true, false)
	_check(tags != null and tags.get_child_count() >= 3, "The look's phrases are tags")
	var never := page.find_child("Never", true, false).find_child("Tags", true, false)
	_check(never != null and never.get_child_count() == 3, "Never is three chips")
	_check(page.find_child("SoundText", true, false) != null, "A decided sound shows its words")
	var technical: Node = page.find_child("Technical", true, false)
	_check(not technical.get("body").visible, "Checked rules start folded")
	_check(technical.get("header").text == "Checked rules", "Technical rules are Checked rules")
	var state := page.find_child("State", true, false) as Label
	_check(state.text == "Draft", "The footer says Draft")
	var accents := page.find_children("*", "Button", true, false).filter(
		func(button: Node) -> bool:
			return (
				(button as Button).is_visible_in_tree()
				and button.theme_type_variation in ["JoineryPrimaryButton", "JoineryLinkButton"]
			)
	)
	_check(accents.size() == 1, "One accent action on the page")
	var approve_button := page.find_child("Approve", true, false) as Button
	_check(
		approve_button.text == "Approve look" and not approve_button.disabled,
		"Approve look is ready"
	)
	_check(approved[0] == 0, "Opening the page approves nothing")
	# A tile opens full width; Back returns to the look.
	tile.gui_input.emit(_click())
	await process_frame
	var expanded := page.find_child("Expanded", true, false)
	_check(expanded != null, "A tile opens full width")
	var lines: Array[String] = []
	for label: Node in expanded.find_children("*", "Label", true, false):
		lines.append((label as Label).text)
	_check(
		lines.has("Not: the haze") and lines.has("Take: warm stone against a cold sky"),
		"Take and Not"
	)
	(page.find_child("Back", true, false) as Button).pressed.emit()
	await process_frame
	_check(page.visible and page.find_child("Palette", true, false) != null, "Back to the look")
	# A section's Change shows on hover in a wide sidebar.
	await process_frame
	var change_link := page.find_child("FeelHeading", true, false).find_child("Change", true, false)
	_check((change_link as Control).self_modulate.a == 0.0, "Change waits for the pointer")
	# A section's Change asks Journeyman.
	var change := page.find_child("NeverHeading", true, false).find_child("Change", true, false)
	(change as Button).pressed.emit()
	await process_frame
	_check(not page.visible and transcript.visible, "Asking goes back to the chat")
	_check(chat._prompt.text.contains("never"), "Asking fills the message box")
	_check(approved[0] == 0, "Asking approves nothing")
	chat._prompt.clear()

	# A look mostly not decided: dashed swatches, slots in place, Approve held for the palette.
	var open := {
		"text": "# Bible\n\n## Vision\nCalm.\n",
		"version": 2,
		"sha256": "x",
		"gaps": [{"section": "palette", "heading": "Palette", "required": true}],
	}
	pages.present(chat, open, approve)
	await process_frame
	_check(page.find_child("PaletteEmpty", true, false) != null, "Dashed swatches for no palette")
	_check(page.find_child("Choose", true, false) != null, "Choose colours with Journeyman")
	_check(page.find_child("SoundOpen", true, false) != null, "Sound: Not decided yet")
	_check(page.find_child("NeverOpen", true, false) != null, "Never: Not decided yet")
	_check(state.text == "Choose colours first", "The footer says what comes first")
	_check(approve_button.disabled, "Approve waits for the palette")
	# Only optional sections open: Approve stays enabled.
	open["gaps"] = [{"section": "audio", "heading": "Audio", "required": false}]
	pages.present(chat, open, approve)
	await process_frame
	_check(not approve_button.disabled and state.text == "Draft", "Optional gaps don't hold it")
	# Nothing decided at all: one blankslate.
	pages.present(chat, {"text": "# Bible\n", "version": 2, "sha256": "y"}, approve)
	await process_frame
	var slate := page.find_child("Blankslate", true, false)
	_check(slate != null, "Nothing decided: a blankslate")
	_check(
		(slate.find_child("Heading", true, false) as Label).text == "No look yet",
		"Headed No look yet"
	)
	_check(not page.find_child("Foot", true, false).visible, "Nothing to approve yet")
	pages.present(chat, open, approve)
	await process_frame

	# Approve: once, from the click, then back to the chat.
	approve_button.pressed.emit()
	await process_frame
	_check(approved[0] == 1, "Approve look approves")
	_check(not page.visible and transcript.visible, "Approving goes back to the chat")
	approve_button.pressed.emit()
	_check(approved[0] == 1, "A closed page can't approve again")
	chat.art_review_requested.emit()
	await process_frame
	approve_button.pressed.emit()
	_check(approved[0] == 1, "Nothing is approved while the page is reading")

	# Approved: the day, no Approve, and the way to make the project match.
	var done := _event(2)
	done["approved"] = true
	done["approved_on"] = "2026-11-12"
	pages.present(chat, done, approve)
	await process_frame
	_check(state.text == "Approved · 12 Nov", "The footer says when it was approved")
	_check(not approve_button.visible, "An approved look can't be approved again")
	_check(page.find_child("Match", true, false).visible, "Approved offers Make the project match")
	(page.find_child("Back", true, false) as Button).pressed.emit()
	await process_frame
	_check(not page.visible and transcript.visible, "Back returns to the chat")
	_check(approved[0] == 1, "Closing approves nothing")

	# The game brief on the same page, read-only, with one way to ask for a change.
	var dialogs: Script = load(DIALOG_PATH)
	dialogs.show_document(chat, "Game brief", "# Quarry\n\n## Pitch\nA calm dig.\n", "Change it: ")
	await process_frame
	_check(page.visible and not transcript.visible, "/brief opens the page in place of the chat")
	_check(
		(page.find_child("Title", true, false) as Label).text == "Game brief", "Titled Game brief"
	)
	_check(
		chat.find_children("*", "AcceptDialog", true, false).is_empty(), "No window for the brief"
	)
	_check(not page.find_child("Foot", true, false).visible, "The brief has nothing to approve")
	var document := page.find_child("Document", true, false)
	_check(document != null, "The brief shows as one document")
	(document.find_child("AskChange", true, false) as Button).pressed.emit()
	await process_frame
	_check(chat._prompt.text.begins_with("Change it: "), "Asking starts the change message")
	_check(not page.visible and transcript.visible, "Asking goes back to the chat")
	chat._prompt.clear()
	chat.art_review_requested.emit()
	await process_frame
	_check(
		(page.find_child("Title", true, false) as Label).text == "The look",
		"The look gets its own title back"
	)
	page.call("set_open", false)


func _click() -> InputEventMouseButton:
	var click := InputEventMouseButton.new()
	click.button_index = MOUSE_BUTTON_LEFT
	click.pressed = true
	return click
