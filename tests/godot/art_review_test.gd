extends SceneTree
## The art direction dialog: the five decided sections, the references board with its images,
## bible-check offers that fill the composer, the upgrade offer for a first-format bible, and
## "Approve these rules"; and the same rules as a sidebar page, where only a click on Approve
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
	_check(
		dialog.get_ok_button().text == "Approve these rules", "The button reads Approve these rules"
	)
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
	_check(
		technical.header.text == "Technical rules (preset for you)",
		"The technical rules say they were preset"
	)
	var upgrade := dialog.find_child("Upgrade", true, false)
	_check(
		upgrade != null and upgrade.is_visible_in_tree(), "A first-format bible offers the update"
	)
	var update := upgrade.find_child("Update", true, false) as Button
	_check(update.text == "Update to the new layout", "The update says what it does")
	var upgrade_texts: Array[String] = []
	for label: Node in upgrade.find_children("*", "Label", true, false):
		upgrade_texts.append((label as Label).text)
	_check(
		upgrade_texts.any(func(text: String) -> bool: return text.contains("approve it again")),
		"The update says approval is needed again"
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

	# The update runs `joinery bible --upgrade` through a job.
	dialog = dialog_script.open(chat, _event(1), Vector2i(900, 700), approve)
	await process_frame
	update = dialog.find_child("Update", true, false) as Button
	update.pressed.emit()
	_check(chat._slash._jobs.has("upgrade"), "Update runs the CLI through a job")
	var deadline := Time.get_ticks_msec() + 30000
	while chat._slash._jobs.has("upgrade") and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(not chat._slash._jobs.has("upgrade"), "The update finishes")
	# This project has no bible file, so the update says why it couldn't run.
	_check(
		chat._transcript.notice_texts()[-1].begins_with("Couldn't update the art direction."),
		"A failed update says so in the chat"
	)

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
	_check(approved[0] == 1, "Approve these rules approves")

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


## The sidebar page: opened by the sidebar's art request, it waits for the bridge, shows the
## rules, and approves only from a click on Approve.
func _page(chat: Control) -> void:
	var pages: Script = load(PAGE_PATH)
	var bare := Control.new()
	_check(not pages.present(bare, _event(2), func() -> void: pass), "No sidebar, no page")
	bare.free()
	var page: Control = pages.attach(chat)
	_check(page != null and pages.attach(chat) == page, "The sidebar has one art direction page")
	var transcript: Control = chat._transcript
	var approved := [0]
	var approve := func() -> void: approved[0] += 1
	chat.art_review_requested.emit()
	await process_frame
	_check(page.visible and not transcript.visible, "/art opens the page in place of the chat")
	_check(page.find_child("Waiting", true, false).visible, "The page says it is reading")
	_check(not page.find_child("Foot", true, false).visible, "Nothing to approve while reading")
	pages.present(chat, _event(1), approve)
	await process_frame
	_check(not page.find_child("Waiting", true, false).visible, "The rules replace the wait")
	_check(
		chat.find_children("*", "ConfirmationDialog", true, false).is_empty(),
		"No window opens over the sidebar"
	)
	var titles := page.find_children("*", "Label", true, false).filter(
		func(label: Node) -> bool: return (label as Label).text == "Art direction"
	)
	_check(titles.size() == 1, "The title shows once")
	var state := page.find_child("State", true, false) as Label
	_check(state.text == "Draft, not approved yet", "The page says the rules are a draft")
	var rules := page.find_child("Rules", true, false)
	_check(
		(rules.find_child("Feel", true, false) as RichTextLabel).text.contains("Calm"),
		"Feel shows the vision"
	)
	_check(rules.find_child("StillOpen", true, false) == null, "Nothing is open in a full bible")
	_check(
		page.find_child("Upgrade", true, false) != null, "A first-format bible offers the update"
	)
	var offers := page.find_child("Offers", true, false)
	var asks := offers.find_children("Ask", "Button", true, false)
	_check(asks.size() == 2, "Each check is one line with its own Ask")
	_check(
		asks.all(func(ask: Node) -> bool: return ask.theme_type_variation == "JoineryQuietLink"),
		"Ask is a quiet link, so Approve is the one accent action"
	)
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
		approve_button.text == "Approve these rules" and not approve_button.disabled,
		"Approve these rules is ready"
	)
	var technical: Node = page.find_child("Technical", true, false)
	_check(not technical.get("body").visible, "Technical rules start folded")
	_check(approved[0] == 0, "Opening the page approves nothing")
	(asks[0] as Button).pressed.emit()
	await process_frame
	_check(not page.visible and transcript.visible, "Asking goes back to the chat")
	_check(chat._prompt.text.contains("palette section"), "Asking fills the message box")
	_check(approved[0] == 0, "Asking approves nothing")
	chat._prompt.clear()

	# A bible mostly not written yet: one "Still open" line with one Ask.
	var open := {"text": "# Bible\n\n## Vision\nCalm.\n", "version": 2, "sha256": "x"}
	pages.present(chat, open, approve)
	await process_frame
	var line := page.find_child("OpenLine", true, false) as Label
	_check(
		line != null and line.text == "Still open: Look, Palette, Sound, Never",
		"The open sections share one line"
	)
	_check(page.find_children("AskOpen", "Button", true, false).size() == 1, "One Ask for them")
	_check(page.find_child("Upgrade", true, false) == null, "A current bible offers no update")

	# Approve: once, from the click, then back to the chat.
	approve_button.pressed.emit()
	await process_frame
	_check(approved[0] == 1, "Approve these rules approves")
	_check(not page.visible and transcript.visible, "Approving goes back to the chat")
	approve_button.pressed.emit()
	_check(approved[0] == 1, "A closed page can't approve again")
	chat.art_review_requested.emit()
	await process_frame
	approve_button.pressed.emit()
	_check(approved[0] == 1, "Nothing is approved while the page is reading")

	# Approved rules: no Approve, and the way to make the project match.
	var done := _event(2)
	done["approved"] = true
	pages.present(chat, done, approve)
	await process_frame
	_check(state.text == "Approved", "The page says the rules are approved")
	_check(not approve_button.visible, "Approved rules can't be approved again")
	_check(page.find_child("Match", true, false).visible, "Approved rules offer Make it match")
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
	_check(document != null, "The brief shows in one card")
	(document.find_child("AskChange", true, false) as Button).pressed.emit()
	await process_frame
	_check(chat._prompt.text.begins_with("Change it: "), "Asking starts the change message")
	_check(not page.visible and transcript.visible, "Asking goes back to the chat")
	chat._prompt.clear()
	chat.art_review_requested.emit()
	await process_frame
	_check(
		(page.find_child("Title", true, false) as Label).text == "Art direction",
		"The art direction gets its own title back"
	)
	page.call("set_open", false)
