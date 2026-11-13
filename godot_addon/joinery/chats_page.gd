@tool
extends ScrollContainer
## The Chats page, shown in place of the conversation like Settings: every saved chat of this
## project, most recent first, under quiet date captions ("Today", "Earlier"), in the chat's
## centred column. New chat is the compose icon beside the title (not a filled button: this
## view has no accent action). A row is one line: the chat's title (its first request until
## renamed) and, on the right, when it was last active ("now", "3h", "Nov 4") in faint
## tabular figures; the open chat has an accent dot ("This chat"). Who answered is in the
## row's tooltip, and an agent mark leads the row only when the chats use different agents.
## Choosing a row opens that chat. The pencil (or a double click) renames it in place: Enter
## or leaving the field saves, Esc cancels.

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


## Show [param chats] (chat_store.gd's list); [param current] is the open chat's id.
## [param now] fixes the clock for tests (-1: the real time).
func show_chats(chats: Array[Dictionary], current: String, now := -1) -> void:
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
		row.pressed.connect(func() -> void: chosen.emit(str(chat["id"])))
		row.title_changed.connect(
			func(title: String) -> void: rename_requested.emit(str(chat["id"]), title)
		)
		_list.add_child(row)
		_rows[str(chat["id"])] = row
		rows.append(row)
	UI_MOTION.stagger(rows)


## A date caption with SPACE.5 above it (none above the first) and SPACE.2 below.
func _header(caption: Label, gap: bool) -> MarginContainer:
	var holder := MarginContainer.new()
	STYLE.on_theme_overrides(
		holder,
		func() -> void:
			var top := UI_THEME.space(holder, 5) if gap else 0
			holder.add_theme_constant_override("margin_top", top)
			holder.add_theme_constant_override("margin_bottom", UI_THEME.space(holder, 2))
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


## Show or hide the page in place of the conversation, restoring what was there.
func set_open(open: bool) -> void:
	var content := get_parent()
	if content == null:
		visible = open
		return
	for child: Node in content.get_children():
		if child == self or not child is CanvasItem:
			continue
		var item := child as CanvasItem
		if open:
			if not item.has_meta("before_chats"):
				item.set_meta("before_chats", item.visible)
			item.visible = false
		elif item.has_meta("before_chats"):
			item.visible = item.get_meta("before_chats")
			item.remove_meta("before_chats")
	visible = open
	if open:
		scroll_vertical = 0


## One saved chat on one line: its title, then when ("3h"); the open chat's accent dot. The
## whole row opens it; the pencil or a double click renames.
class ChatRow:
	extends HBoxContainer

	signal pressed
	signal title_changed(title: String)

	var title: Label
	## When it was last active ("now", "3d", "Nov 4").
	var caption: Label
	## The accent dot of the open chat (hidden on the others).
	var dot: Control
	var _open: Button
	var _field: LineEdit
	var _rename: Button

	func _init(chat: Dictionary, current: bool, now: int, marked := false) -> void:
		theme_type_variation = "JoineryRowTight"
		_open = UI.row_button("", "")
		_open.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		_open.accessibility_name = "Open chat: " + str(chat.get("title", ""))
		_open.pressed.connect(func() -> void: pressed.emit())
		_open.gui_input.connect(_on_open_input)
		var provider := str(chat.get("provider", ""))
		var who := STORE.provider_name(provider) if not provider.is_empty() else ""
		var when := STORE.when(int(chat.get("updated", 0)), now)
		_open.tooltip_text = "\n".join(
			PackedStringArray([str(chat.get("title", "")), " · ".join(_parts(who, when))])
		)
		add_child(_open)
		var inset := MarginContainer.new()
		inset.theme_type_variation = "JoineryInset"
		inset.mouse_filter = Control.MOUSE_FILTER_IGNORE
		inset.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		# SPACE.2 above and below the line, inside the hover fill: rows read as items.
		STYLE.on_theme_overrides(
			inset,
			func() -> void:
				var pad := UI_THEME.space(inset, 2)
				inset.add_theme_constant_override("margin_top", pad)
				inset.add_theme_constant_override("margin_bottom", pad)
		)
		_open.add_child(inset)
		var lines := HBoxContainer.new()
		lines.theme_type_variation = "JoineryRow"
		lines.mouse_filter = Control.MOUSE_FILTER_IGNORE
		inset.add_child(lines)
		if marked and AGENT_ICON.FILES.has(provider):
			# Chats with different agents: a 16 px mark says which, at the leading edge.
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
					mark.texture = AGENT_ICON.texture(provider, side, directory)
					mark.custom_minimum_size = Vector2(side, side)
			)
			lines.add_child(mark)
		title = UI.label(str(chat.get("title", "")), "")
		title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		title.mouse_filter = Control.MOUSE_FILTER_IGNORE
		lines.add_child(title)
		_field = LineEdit.new()
		_field.accessibility_name = "Chat name"
		_field.max_length = 80
		_field.select_all_on_focus = true
		_field.hide()
		_field.text_submitted.connect(func(_text: String) -> void: _commit())
		_field.focus_exited.connect(_commit)
		_field.gui_input.connect(_on_field_input)
		lines.add_child(_field)
		dot = UI.status_dot("working")
		dot.tooltip_text = "This chat"
		dot.visible = current
		dot.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		lines.add_child(dot)
		caption = UI.label(_SELF.short_when(int(chat.get("updated", 0)), now), "JoineryNumeric")
		caption.mouse_filter = Control.MOUSE_FILTER_IGNORE
		caption.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		lines.add_child(caption)
		# The row is as tall as its line and the inset around it.
		lines.minimum_size_changed.connect(_fit)
		# The pencil takes the time's place on the row pointed at, so the times keep the page's
		# right edge; F2 or a double click rename from the keyboard and the mouse too.
		_rename = UI.icon_button("rename", "Rename (F2)")
		_rename.accessibility_name = "Rename chat: " + title.text
		_rename.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		_rename.pressed.connect(begin_rename)
		_rename.hide()
		lines.add_child(_rename)
		STYLE.on_theme(
			_rename,
			func() -> void:
				_rename.custom_minimum_size = Vector2.ONE * UI_THEME.control_h(self, "xs")
		)
		mouse_filter = Control.MOUSE_FILTER_PASS
		mouse_entered.connect(_hover)
		mouse_exited.connect(_hover)
		_open.mouse_entered.connect(_hover)
		_open.mouse_exited.connect(_hover)
		_fit.call_deferred()

	## Pointed at: the pencil in the time's place; otherwise the time.
	func _hover() -> void:
		if not is_inside_tree():
			return
		var inside := get_global_rect().has_point(get_global_mouse_position())
		_rename.visible = inside and not _field.visible
		caption.visible = not _rename.visible

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

	func _fit() -> void:
		if is_instance_valid(_open) and _open.get_child_count() > 0:
			var inset := _open.get_child(0) as Control
			_open.custom_minimum_size.y = inset.get_combined_minimum_size().y

	## Put the title in an editable field, selected.
	func begin_rename() -> void:
		_field.text = title.text
		title.hide()
		_rename.hide()
		caption.show()
		_field.show()
		_open.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_field.grab_focus.call_deferred()

	func _commit() -> void:
		if not _field.visible:
			return
		var text := " ".join(_field.text.strip_edges().split(" ", false))
		_end_rename()
		if not text.is_empty() and text != title.text:
			title.text = text
			title.tooltip_text = text
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
