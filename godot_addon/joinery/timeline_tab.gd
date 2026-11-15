@tool
extends VBoxContainer
## The Timeline page: how the project changed, newest first, as a glanceable history.
##
## Every accepted Journeyman change, and every revert, is one git commit in the project's own
## repository (joinery/timeline.py). The page is one centred column (ui_blocks.page_scroll):
## the title, a filter, then the entries under quiet day captions ("Today", "Yesterday",
## "Monday", "9 Nov"), each one line: what changed and, on the trailing edge, the time (and who,
## once more than one person is on the list). Who, how many files and when sit in its tooltip.
## Entries load 50 at a time. Clicking one opens its files (timeline_entry.gd), where Restore
## is each file's secondary action and stages an ordinary reviewed change in the Review tab.
##
## Named Timeline because Godot's own History dock is the undo history. A project without git
## is a blankslate with "Start timeline"; nothing starts without that click. A scene edit
## accepted live is not on disk until the scene is saved: it waits at the top under "Not saved
## yet" and joins the timeline on that save.
##
## The list comes from `joinery timeline list` (command_job.gd), read when the tab is shown and
## when the editor gets focus back while it is shown; it never polls.
##
## A project shared with teammates (`joinery team connect`, ADR 0071) has Pull and Push beside
## the title, with how many changes each would move; the one worth doing next is the page's one
## accent button. The remote is asked when the tab is shown (at most once a minute). The last
## pull's entries carry a dot. What a pull or push did shows under the title, with Review when
## a choice waits there.

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const BLOCKS := preload("ui_blocks.gd")
const STYLE := preload("sidebar_style.gd")
const COMMAND := preload("command_job.gd")
const REVIEW_TAB := preload("review_tab.gd")
const ENTRY := preload("timeline_entry.gd")
const SELF_PATH := "res://addons/joinery/timeline_tab.gd"
const FILTER_DELAY := 0.25
const OFFER_TITLE := "Keep a timeline"
const OFFER_TEXT := "Every change you accept, kept with git in this project."
const SWITCHED_OFF := "Accepted changes aren't added. Turn it on in Settings."
const PENDING_CAPTION := "Not saved yet"
## An entry nobody has played since (joinery/played.py): the row's word, and its full line.
const NOT_PLAYED := "Not played"
const NOT_PLAYED_SINCE := "Not played since this change"
const MONTHS := ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
const WEEKDAYS := ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
## The remote is asked again when the tab is shown this long after the last time, in seconds.
const FETCH_EVERY := 60.0

## Off in tests: nothing runs the CLI; data comes through apply_listing and the entry view.
var cli_enabled := true
## The CLI program and leading arguments, for tests; empty runs the engine's `uv run joinery`.
var cli_prefix := PackedStringArray()
var _plugin: EditorPlugin
var _list_view: VBoxContainer
var _entry: ENTRY
var _filter: LineEdit
var _filter_timer: Timer
var _content: VBoxContainer
var _loading: VBoxContainer
var _load_error: Label
var _offer: VBoxContainer
var _offer_title: Label
var _offer_text: Label
var _offer_button: Button
var _note: Label
## Live edits accepted in scenes not saved yet, at the top of the list.
var _pending: VBoxContainer
## The day captions and entry rows, in order; [method entry_rows] gives the rows alone.
var _rows: VBoxContainer
var _empty: Label
var _more: Button
## The last `timeline list` answer, without its entries: {state, message, auto_commit}.
var _status: Dictionary = {}
var _entries: Array[Dictionary] = []
var _has_more := false
var _loaded := false
## Scenes with accepted live edits not saved yet: {res path: [summaries]}.
var _unsaved: Dictionary = {}
var _jobs: Dictionary = {}
## Sharing with teammates, from the listing and `team status --fetch`: {connected, ahead,
## behind, conflicts, last_fetch}.
var _team: Dictionary = {}
var _sync: HBoxContainer
var _pull: Button
var _push: Button
var _sync_note: HBoxContainer
var _sync_text: Label
var _sync_review: Button
var _fetched_at := -1.0e9


## Accepted changes nobody has played since, from played_watch.gd: their entries say so.
func show_unplayed(state: Dictionary) -> void:
	var commits: Array = state.get("commits", []) if state.get("commits") is Array else []
	var changed := false
	for entry: Dictionary in _entries:
		var waiting := str(entry.get("commit", "")) in commits
		if bool(entry.get("unplayed", false)) != waiting:
			entry["unplayed"] = waiting
			changed = true
	if changed:
		_render()


## Add the Timeline tab to [param plugin]'s bottom panel, after Review. It removes itself when
## the plugin leaves the tree, so plugin.gd needs only this one call.
static func install(plugin: EditorPlugin) -> Control:
	var tab: Control = load(SELF_PATH).new()
	tab.set("_plugin", plugin)
	# A rail page in Joinery's layout; a bottom panel tab in the classic one.
	var rail: bool = (
		plugin.has_method("add_rail_page")
		and plugin.call("add_rail_page", "timeline", "Timeline", tab, {"editor": "History"})
	)
	if not rail:
		plugin.add_control_to_bottom_panel(tab, "Timeline")
	plugin.scene_saved.connect(tab.scene_saved)
	var edits: Variant = plugin.get("_edits")
	if edits is Object and (edits as Object).has_signal("accepted"):
		(edits as Object).connect("accepted", tab.live_edit_accepted)
	plugin.tree_exiting.connect(
		func() -> void:
			if is_instance_valid(tab):
				if rail:
					plugin.call("remove_rail_page", tab)
				else:
					plugin.remove_control_from_bottom_panel(tab)
				tab.queue_free(),
		CONNECT_ONE_SHOT
	)
	return tab


func _init() -> void:
	name = "Timeline"
	theme_type_variation = "JoineryFlush"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	accessibility_name = "Timeline: how this project changed, newest first"
	add_to_group(&"joinery_unplayed")
	_build_list()
	_entry = ENTRY.new()
	_entry.hide()
	_entry.back.connect(show_list)
	_entry.file_requested.connect(_read_file)
	_entry.restore_requested.connect(_restore)
	_entry.open_requested.connect(_open_file)
	_entry.review_requested.connect(_show_review)
	add_child(_entry)
	_filter_timer = Timer.new()
	_filter_timer.one_shot = true
	_filter_timer.wait_time = FILTER_DELAY
	_filter_timer.timeout.connect(refresh)
	add_child(_filter_timer)
	_render()


func _ready() -> void:
	UI_THEME.watch(self)
	set_process(false)


func _exit_tree() -> void:
	for slot: String in _jobs:
		(_jobs[slot][0] as RefCounted).call("cancel")
	_jobs.clear()


func _notification(what: int) -> void:
	match what:
		NOTIFICATION_VISIBILITY_CHANGED:
			if is_visible_in_tree() and not _entry.visible:
				refresh()
		NOTIFICATION_APPLICATION_FOCUS_IN:
			# A commit made in a terminal shows up when the user comes back to the editor.
			if is_visible_in_tree() and not _entry.visible:
				refresh()


# --- Public ------------------------------------------------------------------------------------


## Read the first page again (`joinery timeline list`), with the filter text.
func refresh() -> void:
	if not cli_enabled:
		return
	_cancel("list")
	var args := ["timeline", "list", "--project", _project(), "--json"]
	if not _filter.text.strip_edges().is_empty():
		args.append_array(["--filter", _filter.text.strip_edges()])
	if not _start("list", args, apply_listing.bind(false)):
		_show_load_error()


## Show a page as `joinery timeline list` prints it: {state, message, auto_commit, entries:
## [{commit, short, subject, author, time, files}], more}. [param append] adds an older page.
func apply_listing(data: Dictionary, append := false) -> void:
	if data.has("error") or not data.has("state"):
		_show_load_error(str(data.get("error", "")))
		return
	_loaded = true
	_load_error.hide()
	_status = {
		"state": str(data.get("state", "")),
		"message": str(data.get("message", "")),
		"auto_commit": bool(data.get("auto_commit", true)),
	}
	var team: Variant = data.get("team", {})
	if team is Dictionary and not append:
		apply_team(team)
	if not append:
		_entries.clear()
	for entry: Variant in data.get("entries", []):
		if entry is Dictionary:
			_entries.append(entry)
	_has_more = bool(data.get("more", false))
	_render(append)


## Show where sharing stands ({connected, ahead, behind, conflicts}); asks the remote when
## it has not been asked for a minute.
func apply_team(team: Dictionary) -> void:
	_team = team
	_render_sync()
	var now := Time.get_ticks_msec() / 1000.0
	if cli_enabled and bool(team.get("connected", false)) and now - _fetched_at > FETCH_EVERY:
		_fetched_at = now
		_start("team", ["team", "status", "--project", _project(), "--fetch", "--json"], _on_team)


## What a pull or push answered: its sentence, or why it failed. [param review] shows Review.
func show_sync_result(text: String, failed := false, review := false) -> void:
	_sync_text.text = text
	_sync_text.theme_type_variation = "JoineryError" if failed else "JoineryCaption"
	_sync_review.visible = review
	_sync_note.visible = not text.is_empty()
	if not failed and not text.is_empty():
		UI_MOTION.pulse(_sync_note)


## Back from an entry to the list.
func show_list() -> void:
	var arriving := not _list_view.visible
	_entry.hide()
	_list_view.show()
	if arriving:
		UI_MOTION.fade_in(_list_view)


## Open [param summary] (a list entry) and read its files.
func open_entry(summary: Dictionary) -> void:
	_entry.set_loading(summary)
	_list_view.hide()
	UI_MOTION.fade_in(_entry)
	if cli_enabled:
		var commit := str(summary.get("commit", ""))
		var args := ["timeline", "show", "--project", _project(), "--commit", commit, "--json"]
		_start("show", args, _on_shown)


## The entry view, for tests and the visual suite.
func entry_view() -> ENTRY:
	return _entry


## The entry rows on the list, newest first, without their day captions.
func entry_rows() -> Array[Control]:
	var rows: Array[Control] = []
	for child: Node in _rows.get_children():
		if child.has_meta("commit"):
			rows.append(child as Control)
	return rows


## The day caption for [param stamp] seen at [param now] (both Unix seconds, shown in local
## time): "Today", "Yesterday", the weekday within a week, then "9 Nov" (with the year when it
## is not this one).
static func day_title(stamp: float, now: float) -> String:
	var bias := int(Time.get_time_zone_from_system().get("bias", 0)) * 60
	var day := floori((stamp + bias) / 86400.0)
	var today := floori((now + bias) / 86400.0)
	var date := Time.get_datetime_dict_from_unix_time(int(stamp) + bias)
	match today - day:
		0:
			return "Today"
		1:
			return "Yesterday"
	if today - day < 7 and today >= day:
		return WEEKDAYS[int(date["weekday"])]
	var title := "%d %s" % [int(date["day"]), MONTHS[int(date["month"]) - 1]]
	if int(date["year"]) != int(Time.get_datetime_dict_from_unix_time(int(now) + bias)["year"]):
		title += " %d" % int(date["year"])
	return title


## The local clock time of [param stamp]: "14:05".
static func clock_text(stamp: float) -> String:
	var bias := int(Time.get_time_zone_from_system().get("bias", 0)) * 60
	var when := Time.get_datetime_dict_from_unix_time(int(stamp) + bias)
	return "%02d:%02d" % [int(when["hour"]), int(when["minute"])]


## A live scene edit was accepted (scene_edits.gd): it reaches the timeline on save.
func live_edit_accepted(scene_path: String, summary: String) -> void:
	var summaries: Array = _unsaved.get_or_add(scene_path, [])
	summaries.append(summary)
	_render()


## The editor saved [param path]: commit the live edits accepted in it.
func scene_saved(path: String) -> void:
	# A preview made in an empty editor has no file yet: its first save names it.
	var key := path if _unsaved.has(path) else ""
	if not _unsaved.has(key):
		return
	var summaries: Array = _unsaved[key]
	_unsaved.erase(key)
	_render()
	if not cli_enabled or not path.begins_with("res://"):
		return
	var args := [
		"timeline",
		"record",
		"--project",
		_project(),
		"--path",
		path.trim_prefix("res://"),
		"--message",
		"\n".join(summaries),
		"--json",
	]
	_start("record:" + path, args, func(_result: Dictionary) -> void: refresh())


## "Pull 2" / "Push": a sync button's words for [param count] changes to move.
static func sync_text(word: String, count: int) -> String:
	return word if count <= 0 else "%s %d" % [word, count]


## "Ada Lovelace · 3 files · 2 h ago" for a list entry.
static func meta_text(entry: Dictionary, now: float) -> String:
	var parts: Array[String] = []
	var author := str(entry.get("author", ""))
	if not author.is_empty():
		parts.append(author)
	var files := int(entry.get("files", 0))
	parts.append("%d file%s" % [files, "" if files == 1 else "s"])
	var when := REVIEW_TAB.relative_time(float(entry.get("time", 0)), now)
	if not when.is_empty():
		parts.append(when)
	return " · ".join(parts)


# --- Building ----------------------------------------------------------------------------------


func _build_list() -> void:
	_list_view = VBoxContainer.new()
	_list_view.theme_type_variation = "JoineryFlush"
	_list_view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(_list_view)
	var scroll := BLOCKS.page_scroll()
	scroll.accessibility_name = "Timeline entries"
	_list_view.add_child(scroll)
	_content = scroll.get_meta("body")
	var head := BLOCKS.page_title("Timeline")
	_content.add_child(head)
	_build_sync(head)
	_build_sync_note()
	var bar := HBoxContainer.new()
	bar.name = "FilterBar"
	bar.theme_type_variation = "JoineryRow"
	_content.add_child(_spaced(bar, 3))
	_filter = LineEdit.new()
	_filter.name = "Filter"
	_filter.placeholder_text = "Filter by change, file or person"
	_filter.accessibility_name = "Filter the timeline"
	_filter.clear_button_enabled = true
	_filter.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	UI.bind_height(_filter, "md")
	_filter.text_changed.connect(func(_text: String) -> void: _filter_timer.start())
	_filter.text_submitted.connect(
		func(_text: String) -> void:
			_filter_timer.stop()
			refresh()
	)
	bar.add_child(_filter)
	_loading = BLOCKS.skeleton(3)
	_loading.name = "Loading"
	_content.add_child(_spaced(_loading, 5))
	_load_error = UI.label("", "JoineryError", true)
	_load_error.name = "LoadError"
	_load_error.hide()
	_content.add_child(_spaced(_load_error, 4))
	_build_offer()
	_note = UI.label("", "JoineryCaption", true)
	_note.name = "Note"
	_note.hide()
	_content.add_child(_spaced(_note, 4))
	_pending = VBoxContainer.new()
	_pending.name = "Pending"
	_pending.theme_type_variation = "JoineryFlush"
	_pending.hide()
	_content.add_child(_pending)
	_rows = VBoxContainer.new()
	_rows.name = "Rows"
	_rows.theme_type_variation = "JoineryFlush"
	_content.add_child(_rows)
	_empty = UI.label("", "JoineryCaption", true)
	_empty.name = "Empty"
	_empty.hide()
	_content.add_child(_spaced(_empty, 5))
	_more = UI.quiet_link("Show older")
	_more.name = "More"
	_more.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	STYLE.hug_left(_more)
	_more.hide()
	_more.pressed.connect(_load_more)
	_content.add_child(_spaced(_more, 3))


func _spaced(control: Control, step: int) -> MarginContainer:
	return BLOCKS.spaced(control, step)


## No timeline yet: one blankslate with one button that starts it.
func _build_offer() -> void:
	_offer_button = UI.primary("Start timeline")
	_offer_button.name = "Start"
	_offer_button.tooltip_text = "Sets up git in this project and makes the first entry"
	_offer_button.pressed.connect(_start_timeline)
	UI_MOTION.press_feedback(_offer_button)
	_offer = BLOCKS.blankslate("reload", OFFER_TITLE, _offer_button, null, OFFER_TEXT)
	_offer.name = "Offer"
	_offer.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	_offer_title = _offer.find_child("Heading", true, false)
	_offer_text = _offer.find_child("Line", true, false)
	_offer.hide()
	_content.add_child(_spaced(_offer, 6))


## Pull and Push beside the title, shown while the project is shared.
func _build_sync(head: HBoxContainer) -> void:
	_sync = HBoxContainer.new()
	_sync.name = "Sync"
	_sync.theme_type_variation = "JoineryRow"
	_sync.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_sync.hide()
	head.add_child(_sync)
	_pull = UI.secondary("Pull")
	_pull.name = "Pull"
	_pull.tooltip_text = "Bring in your teammates' changes"
	_pull.pressed.connect(sync.bind("pull"))
	UI_MOTION.press_feedback(_pull)
	_sync.add_child(_pull)
	_push = UI.secondary("Push")
	_push.name = "Push"
	_push.tooltip_text = "Share the changes you accepted with your teammates"
	_push.pressed.connect(sync.bind("push"))
	UI_MOTION.press_feedback(_push)
	_sync.add_child(_push)


## What the last pull or push did, under the title.
func _build_sync_note() -> void:
	_sync_note = HBoxContainer.new()
	_sync_note.name = "SyncNote"
	_sync_note.theme_type_variation = "JoineryRow"
	_sync_note.hide()
	_content.add_child(_spaced(_sync_note, 2))
	_sync_text = UI.label("", "JoineryCaption", true)
	_sync_text.name = "Text"
	_sync_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_sync_note.add_child(_sync_text)
	_sync_review = UI.quiet_link("Review")
	_sync_review.name = "Review"
	_sync_review.tooltip_text = "Open the Review tab"
	_sync_review.hide()
	_sync_review.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_sync_review.pressed.connect(_show_review)
	_sync_note.add_child(_sync_review)


# --- Rendering ---------------------------------------------------------------------------------


## Pull and Push say what they would move; the one worth doing next is the primary button.
func _render_sync() -> void:
	if _sync == null:
		return
	var ready := str(_status.get("state", "")) == "ready"
	_sync.visible = ready and bool(_team.get("connected", false))
	var behind := int(_team.get("behind", 0))
	var ahead := int(_team.get("ahead", 0))
	var waiting := int(_team.get("conflicts", 0)) > 0
	var busy := str(_team.get("busy", ""))
	_pull.text = "Pulling…" if busy == "pull" else sync_text("Pull", behind)
	_push.text = "Pushing…" if busy == "push" else sync_text("Push", ahead)
	_pull.disabled = not busy.is_empty()
	_push.disabled = not busy.is_empty()
	_pull.theme_type_variation = (
		"JoineryPrimaryButton" if behind > 0 else "JoinerySecondaryButton"
	)
	_push.theme_type_variation = (
		"JoineryPrimaryButton"
		if ahead > 0 and behind == 0 and not waiting
		else "JoinerySecondaryButton"
	)
	_pull.accessibility_name = "Pull %d changes from your teammates" % behind
	_push.accessibility_name = "Push %d changes to your teammates" % ahead
	if waiting:
		_push.tooltip_text = "Choose between your version and your teammate's in Review first"
	else:
		_push.tooltip_text = "Share the changes you accepted with your teammates"


func _render(appended := false) -> void:
	if _rows == null:
		return
	var state := str(_status.get("state", ""))
	var ready := state == "ready"
	var filtered := not _filter.text.strip_edges().is_empty()
	_loading.visible = not _loaded
	_filter.get_parent().visible = ready and (filtered or not _entries.is_empty())
	_render_offer(state)
	_note.text = SWITCHED_OFF if ready and not bool(_status.get("auto_commit", true)) else ""
	_note.visible = not _note.text.is_empty()
	_render_pending()
	_render_sync()
	var shown := entry_rows().size()
	var first_new := shown if appended else 0
	if not appended:
		for child: Node in _rows.get_children():
			_rows.remove_child(child)
			child.queue_free()
	var now := Time.get_unix_time_from_system()
	var authors := {}
	for entry: Dictionary in _entries:
		authors[str(entry.get("author", ""))] = true
	var group := ""
	if appended and first_new > 0:
		group = day_title(float(_entries[first_new - 1].get("time", 0)), now)
	var added: Array = []
	for index: int in range(first_new, _entries.size()):
		var entry := _entries[index]
		var day := day_title(float(entry.get("time", 0)), now)
		if day != group:
			group = day
			_rows.add_child(BLOCKS.group_caption(day))
		var row := _row(entry, now, authors.size() > 1)
		_rows.add_child(row)
		added.append(row)
	# Rows arriving on an empty list or as an older page settle in; a refresh does not move.
	if appended or shown == 0:
		UI_MOTION.stagger(added)
	_empty.visible = ready and _entries.is_empty()
	_empty.text = (
		"Nothing matches “%s”." % _filter.text.strip_edges()
		if filtered
		else "No changes yet. Accepted changes show here."
	)
	_more.visible = ready and _has_more


func _render_offer(state: String) -> void:
	_offer.visible = _loaded and state in ["none", "empty", "no-git", "nested"]
	_offer_button.visible = state in ["none", "empty"]
	_offer_button.disabled = _jobs.has("start")
	match state:
		"no-git":
			_offer_title.text = "Git isn't installed"
			_offer_text.text = str(_status.get("message", ""))
		"nested":
			_offer_title.text = "Part of a bigger repository"
			_offer_text.text = str(_status.get("message", ""))
		_:
			_offer_title.text = OFFER_TITLE
			_offer_text.text = OFFER_TEXT
	_offer_text.visible = not _offer_text.text.is_empty()


## Live edits accepted but not saved: muted rows under "Not saved yet", above the entries.
func _render_pending() -> void:
	for child: Node in _pending.get_children():
		_pending.remove_child(child)
		child.queue_free()
	_pending.visible = not _unsaved.is_empty()
	if _unsaved.is_empty():
		return
	_pending.add_child(BLOCKS.group_caption(PENDING_CAPTION))
	for scene: String in _unsaved:
		for summary: Variant in _unsaved[scene]:
			var line := HBoxContainer.new()
			line.theme_type_variation = "JoineryRow"
			UI.bind_height(line, "md", false)
			var words := UI.label(str(summary), "JoineryMuted")
			words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
			line.add_child(words)
			var when := UI.label("On save", "JoineryNumeric")
			when.size_flags_vertical = Control.SIZE_SHRINK_CENTER
			line.tooltip_text = "Joins the timeline when %s is saved" % scene.get_file()
			line.add_child(when)
			_pending.add_child(line)


## One entry on one line: what changed, then (with several people on the list) who, then the
## time. The whole row opens it; who, how many files and when are in its tooltip.
func _row(entry: Dictionary, now: float, named: bool) -> HBoxContainer:
	var line := HBoxContainer.new()
	line.theme_type_variation = "JoineryRow"
	line.set_meta("commit", str(entry.get("commit", "")))
	UI.bind_height(line, "md", false)
	var subject := str(entry.get("subject", "")).strip_edges()
	if subject.is_empty():
		subject = "Project changes"
	var open := UI.row_button(subject, "")
	open.name = "Open"
	open.tooltip_text = subject + "\n" + meta_text(entry, now)
	open.accessibility_name = subject + ", show its files"
	open.pressed.connect(open_entry.bind(entry))
	STYLE.hug_left(open)
	UI_MOTION.press_feedback(open)
	line.add_child(open)
	if bool(entry.get("pulled", false)):
		# Arrived with the last pull: a dot before who and when, said in words for readers.
		var dot := UI.status_dot("working")
		dot.name = "Pulled"
		dot.tooltip_text = "New from your last pull"
		line.add_child(dot)
		open.accessibility_name = subject + ", new from your last pull, show its files"
	if bool(entry.get("unplayed", false)):
		# Accepted here, changes the game, and nobody has run it since (joinery/played.py).
		var unplayed := UI.label(NOT_PLAYED, "JoineryCaption")
		unplayed.name = "Unplayed"
		unplayed.tooltip_text = NOT_PLAYED_SINCE
		unplayed.mouse_filter = Control.MOUSE_FILTER_PASS
		unplayed.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		line.add_child(unplayed)
		open.accessibility_name = subject + ", " + NOT_PLAYED_SINCE.to_lower() + ", show its files"
	var author := str(entry.get("author", ""))
	if named and not author.is_empty():
		var who := UI.label(author, "JoineryCaption")
		who.name = "Author"
		who.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		line.add_child(who)
	var meta := UI.label(clock_text(float(entry.get("time", 0))), "JoineryNumeric")
	meta.name = "Meta"
	meta.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	meta.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	line.add_child(meta)
	return line


func _show_load_error(why := "") -> void:
	_loaded = true
	_load_error.text = _why({"error": why}, "Couldn't read the timeline.")
	_render()
	_load_error.show()


# --- Actions -----------------------------------------------------------------------------------


## Pull or push ([param action]): the button says so at once, the list follows the result.
func sync(action: String) -> void:
	if not cli_enabled or _jobs.has("sync"):
		return
	_team["busy"] = action
	_render_sync()
	var args := ["team", action, "--project", _project(), "--json"]
	if not _start("sync", args, _on_synced.bind(action)):
		_team.erase("busy")
		_render_sync()
		show_sync_result("Couldn't start Joinery. Is uv installed?", true)


func _on_synced(data: Dictionary, action: String) -> void:
	_team.erase("busy")
	if data.has("error"):
		_render_sync()
		show_sync_result(_why(data, "Couldn't %s." % action), true)
		return
	var waiting := not str(data.get("review_id", "")).is_empty()
	waiting = waiting or not (data.get("documents", []) as Array).is_empty()
	waiting = waiting or not (data.get("reviews", {}).get("conflicting", []) as Array).is_empty()
	show_sync_result(str(data.get("message", "")), false, waiting)
	_fetched_at = -1.0e9
	refresh()
	if waiting:
		var review: Variant = _plugin.get("_review_tab") if _plugin != null else null
		if review is Object and (review as Object).has_method("refresh"):
			(review as Object).call("refresh")


func _on_team(data: Dictionary) -> void:
	if data.has("error") or not data.has("connected"):
		return
	var conflicts: Variant = data.get("conflicts", [])
	apply_team(
		{
			"connected": bool(data.get("connected", false)),
			"ahead": int(data.get("ahead", 0)),
			"behind": int(data.get("behind", 0)),
			"conflicts": (conflicts as Array).size() if conflicts is Array else 0,
		}
	)
	if data.has("fetch_error"):
		show_sync_result(str(data["fetch_error"]), true)


func _load_more() -> void:
	if not cli_enabled or _jobs.has("list"):
		return
	var args := [
		"timeline", "list", "--project", _project(), "--json", "--skip", str(_entries.size())
	]
	if not _filter.text.strip_edges().is_empty():
		args.append_array(["--filter", _filter.text.strip_edges()])
	_start("list", args, apply_listing.bind(true))


func _start_timeline() -> void:
	if not cli_enabled or _jobs.has("start"):
		return
	if _start("start", ["timeline", "start", "--project", _project(), "--json"], _on_started):
		_render()


func _on_started(data: Dictionary) -> void:
	if data.has("error"):
		_show_load_error(_why(data, "Couldn't start the timeline."))
		return
	refresh()
	UI_MOTION.pulse(_content)


func _on_shown(data: Dictionary) -> void:
	if data.has("error"):
		var why := _why(data, "Couldn't read this entry.")
		_entry.show_entry({"subject": why, "files": []})
		return
	_entry.show_entry(data)


## The readable reason in a failed result, or [param fallback] pointing at Output.
static func _why(data: Dictionary, fallback: String) -> String:
	var text := str(data.get("error", ""))
	return text if not text.is_empty() else fallback + " Details are in Output."


func _read_file(commit: String, path: String) -> void:
	if not cli_enabled:
		return
	var args := [
		"timeline", "show", "--project", _project(), "--commit", commit, "--path", path, "--json"
	]
	_start("file:" + path, args, _on_file.bind(path))


func _on_file(data: Dictionary, path: String) -> void:
	if data.has("error"):
		_entry.file_failed(path, _why(data, "Couldn't read this file."))
	else:
		_entry.apply_file(data)


## Restore stages a reviewed change: nothing on disk changes until it is accepted in Review.
func _restore(commit: String, path: String) -> void:
	if not cli_enabled or _jobs.has("restore:" + path):
		return
	_entry.set_restore(path, "busy")
	var args := [
		"timeline", "restore", "--project", _project(), "--commit", commit, "--path", path, "--json"
	]
	if not _start("restore:" + path, args, _on_restored.bind(path)):
		_entry.set_restore(path, "failed", "Couldn't start Joinery. Is uv installed?")


func _on_restored(data: Dictionary, path: String) -> void:
	if data.has("error"):
		_entry.set_restore(path, "failed", _why(data, "Couldn't restore it."))
		return
	_entry.set_restore(path, "done")
	var review: Variant = _plugin.get("_review_tab") if _plugin != null else null
	if review is Object and (review as Object).has_method("refresh"):
		(review as Object).call("refresh")


func _show_review() -> void:
	var review: Variant = _plugin.get("_review_tab") if _plugin != null else null
	if review is Control:
		_plugin.make_bottom_panel_item_visible(review as Control)


func _open_file(path: String) -> void:
	var resource := "res://" + path
	if not FileAccess.file_exists(resource) or not Engine.is_editor_hint():
		return
	match path.get_extension().to_lower():
		"tscn", "scn":
			EditorInterface.open_scene_from_path(resource)
		"gd", "gdshader", "cs", "tres", "res", "md", "txt", "cfg", "json", "toml":
			var loaded := load(resource)
			if loaded != null:
				EditorInterface.edit_resource(loaded)
			else:
				EditorInterface.select_file(resource)
		_:
			EditorInterface.select_file(resource)


# --- CLI ---------------------------------------------------------------------------------------


func _project() -> String:
	return ProjectSettings.globalize_path("res://").rstrip("/")


func _start(slot: String, args: Array, callback: Callable) -> bool:
	_cancel(slot)
	var job: RefCounted = COMMAND.joinery(cli_prefix, args)
	if job == null:
		return false
	_jobs[slot] = [job, callback]
	set_process(true)
	return true


func _cancel(slot: String) -> void:
	if _jobs.has(slot):
		(_jobs[slot][0] as RefCounted).call("cancel")
		_jobs.erase(slot)


func _process(_delta: float) -> void:
	for slot: String in _jobs.keys():
		if not _jobs.has(slot):
			continue
		var result: Dictionary = (_jobs[slot][0] as RefCounted).call("poll")
		if result.is_empty():
			continue
		var callback: Callable = _jobs[slot][1]
		_jobs.erase(slot)
		var output := str(result.get("output", ""))
		var data := _parse(output)
		if int(result.get("code", 1)) != 0 and not data.has("error"):
			print("[Joinery] timeline\n", output, "\n", result.get("error", ""))
			data = {"error": ""}
		callback.call(data)
	if _jobs.is_empty():
		set_process(false)


static func _parse(output: String) -> Dictionary:
	var lines := output.strip_edges().split("\n", false)
	lines.reverse()
	for line: String in lines:
		var parsed: Variant = JSON.parse_string(line)
		if parsed is Dictionary:
			return parsed
	return {}
