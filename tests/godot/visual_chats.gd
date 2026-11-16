extends RefCounted
## The visual suite's Chats screens beyond the list itself. [param suite] is
## visual_sidebar_test.gd, whose `_shot`, `_frames` and `_check` these use; the chat on screen is
## its saved chats' latest one.
##
## [method aligned]: the Chats page's title row sits exactly where the sidebar's header row sits
## on the chat (same top edge and height), also when the task list changes behind it.
## [method working]: New chat while Journeyman works ("chat-new-while-working"), and the list
## marking the chat still at work ("chats-working").

const LATEST := "20261110-090000-aaaaaa"


## The sidebar's header row, taken while the chat is on screen.
static func header_rect(chat: Control) -> Rect2:
	var row: Control = chat.get("_header").get("row")
	return row.get_global_rect()


static func aligned(suite: Variant, chat: Control, header: Rect2) -> void:
	var page: Control = chat.get("_chats").get("page")
	var row: Control = page.get("_body").get_child(0)
	var top := row.get_global_rect()
	suite._check(
		is_equal_approx(top.position.y, header.position.y),
		"The Chats row sits where the header row does: %s vs %s" % [top, header]
	)
	suite._check(
		is_equal_approx(top.size.y, header.size.y),
		"And is as tall: %s vs %s" % [top.size.y, header.size.y]
	)
	# A task card arriving behind the page (the list resizes) never pushes the page down.
	var tasks: Control = chat.get("_tasks")
	tasks.show()
	tasks.minimum_size_changed.emit()
	await suite._frames(2)
	var after := row.get_global_rect()
	suite._check(
		is_equal_approx(after.position.y, header.position.y),
		"Still there with a task card behind the page: %s" % [after]
	)
	tasks.hide()
	tasks.minimum_size_changed.emit()


static func working(suite: Variant, light: bool, width: int, chat: Control) -> void:
	var sessions: Node = chat.get("_chats")
	sessions.call("_show", LATEST, false)
	var stand_in := func(_text: String, _targets: Array) -> void: pass
	chat.connect("prompt_submitted", stand_in)
	chat.call("_on_submit", "Light the harbour lamps at dusk")
	chat.set_chat_busy(true)
	chat.call("_on_sent")
	chat.set_chat_state("working", "Opening lamp.blend in Blender")
	sessions.call("new_chat")
	await suite._shot("chat-new-while-working", light, width, chat)
	sessions.call("open_list")
	var page: Node = sessions.get("page")
	suite._check(page.call("row_for", LATEST).get("working"), "The working chat has its dot")
	await suite._shot("chats-working", light, width, chat)
	page.call("set_open", false)
	sessions.call("open_chat", LATEST)
	chat.show_timing({"outcome": "cancelled", "total_ms": 1000})
	chat.set_chat_busy(false)
	chat.disconnect("prompt_submitted", stand_in)
	await suite._frames(2)
