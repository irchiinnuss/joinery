@tool
extends ScrollContainer
## The Chats page, shown in place of the conversation like Settings: every saved chat of this
## project, most recent first, under quiet date captions ("Today", "Earlier"), in the chat's
## centred column. New chat is the compose icon beside the title (not a filled button: this
## view has no accent action). A row is one line: the chat's title (its first request until
## renamed) on one line, ellipsized, and on the right when it was last active ("now", "3h",
## "Nov 4") in faint tabular figures. Every row has the same height and padding in every
## state: the open chat is a background highlight, and the pencil shows in the time's place on
## the row pointed at. Who answered is told to screen readers, and only a cut title has a
## tooltip (its full text), so no tooltip covers the next row needlessly; an agent mark leads
## the row only when the chats use different agents. Choosing a row opens that chat. The
## pencil (or a double click, or F2) renames it in place: Enter or leaving the field saves, Esc
## cancels. The chat Journeyman is working in, open or not, shows a small accent dot in the
## time's place ([method set_working]).

signal closed
signal chosen(chat_id: String)
signal new_chat_requested
signal rename_requested(chat_id: String, title: String)

const UI_THEME := preload("ui_theme.gd")
const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const STORE := preload("chat_store.gd")
const AGENT_ICON := preload("agent_icon.gd")
const _SELF := preload("chats_page.gd")
const MONTHS := ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

var _body: VBoxContainer
var _list: VBoxContainer
var _empty: Label
var _new: Button
## chat id -> its row (a ChatRow).
var _rows: Dictionary = {}


func _ready() -> void:
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	accessibility_name = "Chats"
	UI_THEME.watch(self)
	UI_MOTION.rubber_band(self)
	_body = VBoxContainer.new()
	_body.theme_type_variation = "JoinerySections"
	_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(_body)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRow"
	_body.add_child(row)
	var back := UI.icon_button("back", "Back to chat")
	back.accessibility_name = "Close chats and return to the conversation"
	var escape := InputEventKey.new()
	escape.keycode = KEY_ESCAPE
	back.shortcut = Shortcut.new()
	back.shortcut.events = [escape]
	back.shortcut_in_tooltip = false
	back.pressed.connect(func() -> void: closed.emit())
	row.add_child(back)
	var title := UI.label("Chats", "JoineryTitle")
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(title)
	_new = UI.icon_button("compose", "New chat")
	_new.accessibility_name = "Start a new chat"
	_new.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_new.pressed.connect(func() -> void: new_chat_requested.emit())
	row.add_child(_new)
	_list = VBoxContainer.new()
	_list.theme_type_variation = "JoineryFlush"
	_body.add_child(_list)
	_empty = UI.label(
		"No saved chats yet. Each conversation is saved here as you go.", "JoineryCaption", true
	)
	var inset := MarginContainer.new()
	inset.theme_type_variation = "JoineryInset"
	inset.add_child(_empty)
	_body.add_child(inset)
	STYLE.page_edges(self, _body)


## Show [param chats] (chat_store.gd's list); [param current] is the open chat's id and
## [param working] the one Journeyman works in ("" for none), or every chat at work (an Array
## of ids: chats running beside it, chat_turns.gd). [param now] fixes the clock for tests (-1:
## the real time).
func show_chats(
	chats: Array[Dictionary], current: String, now := -1, working: Variant = ""
) -> void:
	var busy := _ids(working)
	for child: Node in _list.get_children():
		_list.remove_child(child)
		child.queue_free()
	_rows.clear()
	_empty.get_parent().visible = chats.is_empty()
	var rows: Array = []
	var providers := {}
	for chat: Dictionary in chats:
		providers[str(chat.get("provider", ""))] = true
	var clock := now if now >= 0 else int(Time.get_unix_time_from_system())
	var group := ""
	for chat: Dictionary in chats:
		var day := "Today" if _day(int(chat.get("updated", 0))) == _day(clock) else "Earlier"
		if day != group:
			group = day
			var caption := UI.label(day, "JoineryCaption")
			caption.name = day
			_list.add_child(_header(caption, _list.get_child_count() > 0))
		var row := ChatRow.new(chat, chat["id"] == current, clock, providers.size() > 1)
		row.set_working(busy.has(str(chat["id"])))
		row.pressed.connect(func() -> void: chosen.emit(str(chat["id"])))
		row.title_changed.connect(
			func(title: String) -> void: rename_requested.emit(str(chat["id"]), title)
		)
		_list.add_child(row)
		_rows[str(chat["id"])] = row
		rows.append(row)
	UI_MOTION.stagger(rows)


## A date caption with SPACE.5 above it (none above the first) and SPACE.2 below, inset by
## SPACE.2 like the rows' text, so captions and titles share one left edge.
func _header(caption: Label, gap: bool) -> MarginContainer:
	var holder := MarginContainer.new()
	STYLE.on_theme_overrides(
		holder,
		func() -> void:
			var top := UI_THEME.space(holder, 5) if gap else 0
			holder.add_theme_constant_override("margin_top", top)
			holder.add_theme_constant_override("margin_bottom", UI_THEME.space(holder, 2))
			holder.add_theme_constant_override("margin_left", UI_THEME.space(holder, 2))
	)
	holder.add_child(caption)
	return holder


## The calendar day of [param unix] (UTC), for the date captions.
static func _day(unix: int) -> int:
	return floori(unix / 86400.0)


## When a chat was last active, short: "now", "5m", "3h", "4d", then "Nov 4" (and the year
## when it is not this one).
static func short_when(unix: int, now: int) -> String:
	var seconds := maxi(0, now - unix)
	if seconds < 60:
		return "now"
	if seconds < 3600:
		return "%dm" % floori(seconds / 60.0)
	if seconds < 86400:
		return "%dh" % floori(seconds / 3600.0)
	if seconds < 7 * 86400:
		return "%dd" % floori(seconds / 86400.0)
	var date := Time.get_datetime_dict_from_unix_time(unix)
	var label := "%s %d" % [MONTHS[int(date["month"]) - 1], int(date["day"])]
	if int(date["year"]) != int(Time.get_datetime_dict_from_unix_time(now)["year"]):
		label += ", %d" % int(date["year"])
	return label


## The row of [param chat_id], or null.
func row_for(chat_id: String) -> ChatRow:
	return _rows.get(chat_id)


## Mark [param chat_id] as the chat Journeyman works in ("" marks none), or every chat in an
## Array of ids.
func set_working(chat_id: Variant) -> void:
	var busy := _ids(chat_id)
	for id: String in _rows:
		_rows[id].set_working(busy.has(id))


## The ids in [param value]: one id ("" for none) or an Array of them.
static func _ids(value: Variant) -> PackedStringArray:
	var ids := PackedStringArray()
	if value is Array or value is PackedStringArray:
		for each: Variant in value:
			if not str(each).is_empty():
				ids.append(str(each))
	elif not str(value).is_empty():
		ids.append(str(value))
	return ids


## Show or hide the page in place of the conversation, restoring what was there.
func set_open(open: bool) -> void:
	if get_parent() == null:
		visible = open
		return
	STYLE.hold_parts(self, open, "chats")
	if open:
		scroll_vertical = 0


## One saved chat on one line, the same in every state: its title (one line, ellipsized), then
## when ("3h") in a slot at the trailing edge. The open chat is a background highlight; pointed
## at, the pencil takes the time's place in that slot, so nothing on the row moves. The whole
## row opens it; the pencil, F2 or a double click renames.
class ChatRow:
	extends HBoxContainer

	signal pressed
	signal title_changed(title: String)

	var title: Label
	## When it was last active ("now", "3d", "Nov 4").
	var caption: Label
	## True for the open chat: its row is highlighted.
	var current := false
	## True while Journeyman works in this chat: a dot stands in the time's place.
	var working := false
	var _dot: Control
	var _open: Button
	var _field: LineEdit
	var _rename: Button
	## The time's space at the trailing edge, as wide as the time or the pencil, whichever is
	## wider, whether or not the pencil shows: the title's width never changes.
	var _slot: MarginContainer
	var _pointed := false

	func _init(chat: Dictionary, is_current: bool, now: int, marked := false) -> void:
		theme_type_variation = "JoineryRowTight"
		current = is_current
		_open = UI.row_button("", "")
		_open.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		_open.accessibility_name = "Open chat: " + str(chat.get("title", ""))
		_open.pressed.connect(func() -> void: pressed.emit())
		_open.gui_input.connect(_on_open_input)
		var provider := str(chat.get("provider", ""))
		var who := STORE.provider_name(provider) if not provider.is_empty() else ""
		var when := STORE.when(int(chat.get("updated", 0)), now)
		# Who and when are told to screen readers, not in a tooltip over the next row; the only
		# tooltip is a cut title's full text (_tip).
		_open.accessibility_description = " · ".join(_parts(who, when))
		add_child(_open)
		STYLE.on_theme_overrides(_open, _fills)
		var inset := MarginContainer.new()
		inset.theme_type_variation = "JoineryInset"
		inset.mouse_filter = Control.MOUSE_FILTER_IGNORE
		inset.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		_open.add_child(inset)
		var lines := HBoxContainer.new()
		lines.theme_type_variation = "JoineryRow"
		lines.mouse_filter = Control.MOUSE_FILTER_IGNORE
		inset.add_child(lines)
		if marked and AGENT_ICON.FILES.has(provider):
			lines.add_child(_mark(provider))
		title = UI.label(str(chat.get("title", "")))
		title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		title.mouse_filter = Control.MOUSE_FILTER_IGNORE
		title.resized.connect(_tip)
		lines.add_child(title)
		_field = LineEdit.new()
		_field.accessibility_name = "Chat name"
		_field.max_length = 80
		_field.select_all_on_focus = true
		_field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		_field.hide()
		_field.text_submitted.connect(func(_text: String) -> void: _commit())
		_field.focus_exited.connect(_commit)
		_field.gui_input.connect(_on_field_input)
		lines.add_child(_field)
		_slot = MarginContainer.new()
		_slot.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_slot.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		lines.add_child(_slot)
		caption = UI.label(_SELF.short_when(int(chat.get("updated", 0)), now), "JoineryNumeric")
		caption.mouse_filter = Control.MOUSE_FILTER_IGNORE
		caption.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		caption.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		_slot.add_child(caption)
		_dot = UI.status_dot("working")
		_dot.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_dot.size_flags_horizontal = Control.SIZE_SHRINK_END
		_dot.hide()
		_slot.add_child(_dot)
		_rename = Button.new()
		_rename.theme_type_variation = "JoineryIconButton"
		_rename.tooltip_text = "Rename (F2)"
		_rename.accessibility_name = "Rename chat: " + title.text
		_rename.size_flags_horizontal = Control.SIZE_SHRINK_END
		_rename.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		UI.bind_icon(_rename, "rename")
		_rename.pressed.connect(begin_rename)
		_slot.add_child(_rename)
		STYLE.on_theme_overrides(_rename, _small_pencil)
		STYLE.on_theme_overrides(
			inset,
			func() -> void:
				var side := UI_THEME.control_h(inset, "xs")
				# SPACE.2 inside the fill on both sides, in every state (the date captions
				# share the left one, so titles and captions line up).
				inset.add_theme_constant_override("margin_left", UI_THEME.space(inset, 2))
				inset.add_theme_constant_override("margin_right", UI_THEME.space(inset, 2))
				# SPACE.2 above and below the text line, less what the pencil already adds:
				# every row is as tall as a row of text with its padding.
				var font := title.get_theme_font("font")
				var line := font.get_height(title.get_theme_font_size("font_size"))
				var pad := maxi(
					UI_THEME.space(inset, 1), UI_THEME.space(inset, 2) - ceili((side - line) / 2.0)
				)
				inset.add_theme_constant_override("margin_top", pad)
				inset.add_theme_constant_override("margin_bottom", pad)
		)
		# The row is as tall as its line and the inset around it.
		lines.minimum_size_changed.connect(_fit)
		set_pointed(false)
		mouse_filter = Control.MOUSE_FILTER_PASS
		mouse_entered.connect(_hover)
		mouse_exited.connect(_hover)
		_open.mouse_entered.connect(_hover)
		_open.mouse_exited.connect(_hover)
		_fit.call_deferred()

	## The pencil in a CONTROL_H.xs square, its glyph centred there: the icon button's own
	## padding would hold it at md, taller than the line, and the row would grow.
	func _small_pencil() -> void:
		var side := float(UI_THEME.control_h(_rename, "xs"))
		_rename.custom_minimum_size = Vector2(side, side)
		for state: String in ["normal", "hover", "pressed", "hover_pressed", "disabled", "focus"]:
			_rename.remove_theme_stylebox_override(state)
			var source := _rename.get_theme_stylebox(state, _rename.theme_type_variation)
			if source != null:
				var box := source.duplicate() as StyleBox
				box.set_content_margin_all(0.0)
				_rename.add_theme_stylebox_override(state, box)

	## The open chat rests in the pointed-at fill: selection is a fill and nothing else.
	func _fills() -> void:
		_open.remove_theme_stylebox_override("normal")
		if current:
			var hover := _open.get_theme_stylebox("hover", _open.theme_type_variation)
			_open.add_theme_stylebox_override("normal", hover)

	## A 16 px agent mark at the leading edge, in grey, for chats with different agents: it
	## tells them apart without three brand colours in one list.
	func _mark(provider: String) -> TextureRect:
		var mark := TextureRect.new()
		mark.mouse_filter = Control.MOUSE_FILTER_IGNORE
		mark.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
		mark.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		var script: Resource = UI_THEME
		var directory := script.resource_path.get_base_dir()
		STYLE.on_theme(
			mark,
			func() -> void:
				var side := UI_THEME.icon_px(mark, "sm")
				mark.texture = AGENT_ICON.monochrome(AGENT_ICON.texture(provider, side, directory))
				mark.self_modulate = UI_THEME.color(mark, "text_muted")
				mark.custom_minimum_size = Vector2(side, side)
		)
		return mark

	## Working: the accent dot in the time's place, and screen readers hear it.
	func set_working(on: bool) -> void:
		working = on
		_dot.visible = on
		caption.visible = not on
		var told := _open.accessibility_description.trim_suffix(" · Working")
		_open.accessibility_description = told + (" · Working" if on else "")
		_dot.tooltip_text = "Journeyman is working in this chat" if on else ""

	## Pointed at: the pencil fades in over the time, in the same slot; otherwise the time.
	func set_pointed(pointed: bool) -> void:
		_pointed = pointed and not _field.visible
		_rename.mouse_filter = (
			Control.MOUSE_FILTER_STOP if _pointed else Control.MOUSE_FILTER_IGNORE
		)
		_rename.focus_mode = Control.FOCUS_ALL if _pointed else Control.FOCUS_NONE
		var shown := 1.0 if _pointed else 0.0
		if is_inside_tree():
			UI_MOTION.tween_to(self, _rename, "modulate:a", shown)
			UI_MOTION.tween_to(self, caption, "modulate:a", 1.0 - shown)
			UI_MOTION.tween_to(self, _dot, "modulate:a", 1.0 - shown)
		else:
			_rename.modulate.a = shown
			caption.modulate.a = 1.0 - shown
			_dot.modulate.a = 1.0 - shown

	func _hover() -> void:
		if is_inside_tree():
			set_pointed(get_global_rect().has_point(get_global_mouse_position()))

	func _on_open_input(event: InputEvent) -> void:
		var click := event as InputEventMouseButton
		var key := event as InputEventKey
		if click != null and click.double_click and click.button_index == MOUSE_BUTTON_LEFT:
			begin_rename()
		elif key != null and key.pressed and key.keycode == KEY_F2:
			begin_rename()
			_open.accept_event()

	static func _parts(who: String, when: String) -> PackedStringArray:
		var parts := PackedStringArray()
		if not who.is_empty():
			parts.append(who)
		parts.append(when)
		return parts

	## A title cut by the ellipsis has its full text as the tooltip; a whole one has none.
	func _tip() -> void:
		var font := title.get_theme_font("font")
		var size := title.get_theme_font_size("font_size")
		var needs := font.get_string_size(title.text, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x
		_open.tooltip_text = title.text if needs > title.size.x + 0.5 else ""

	func _fit() -> void:
		if is_instance_valid(_open) and _open.get_child_count() > 0:
			var inset := _open.get_child(0) as Control
			_open.custom_minimum_size.y = inset.get_combined_minimum_size().y

	## Put the title in an editable field, selected.
	func begin_rename() -> void:
		_field.text = title.text
		title.hide()
		_field.show()
		set_pointed(false)
		_open.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_field.grab_focus.call_deferred()

	func _commit() -> void:
		if not _field.visible:
			return
		var text := " ".join(_field.text.strip_edges().split(" ", false))
		_end_rename()
		if not text.is_empty() and text != title.text:
			title.text = text
			_tip()
			title_changed.emit(text)

	func _end_rename() -> void:
		_field.hide()
		title.show()
		_open.mouse_filter = Control.MOUSE_FILTER_STOP

	func _on_field_input(event: InputEvent) -> void:
		var key := event as InputEventKey
		if key != null and key.pressed and key.keycode == KEY_ESCAPE:
			_end_rename()
			_field.accept_event()
