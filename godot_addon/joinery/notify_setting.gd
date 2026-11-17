@tool
extends RefCounted
## The Notifications group in Journeyman settings: "Desktop notifications" (on by default) when
## Journeyman is done or needs the user while the editor is in the background, and "Only when
## you're needed" to keep just a change to review, a to-do waiting for Go and questions
## (desktop_notify.gd). Both are editor settings, for this user across projects; the Editor
## Settings dialog lists them too, and the switches read them again whenever the page shows.

const UI := preload("ui_components.gd")
const NOTIFY := preload("desktop_notify.gd")


## Add both rows to [param body] (a settings group's).
static func add_to(body: Control) -> void:
	NOTIFY.register()
	var main := UI.switch_row("Desktop notifications", "While the editor is in the background")
	main.name = "DesktopNotifications"
	var only := UI.switch_row("Only when you're needed", "A change to review, Go, or a question")
	only.name = "NotifyInputOnly"
	only.switch_button.accessibility_name = "Notify only when Journeyman needs you"
	var sync := func() -> void:
		main.set_on(NOTIFY.pref(NOTIFY.ENABLED))
		only.set_on(NOTIFY.pref(NOTIFY.INPUT_ONLY))
		only.switch_button.disabled = not main.is_on()
	main.toggled.connect(
		func(on: bool) -> void:
			NOTIFY.set_pref(NOTIFY.ENABLED, on)
			only.switch_button.disabled = not on
	)
	only.toggled.connect(func(on: bool) -> void: NOTIFY.set_pref(NOTIFY.INPUT_ONLY, on))
	main.visibility_changed.connect(
		func() -> void:
			if main.is_visible_in_tree():
				sync.call()
	)
	sync.call()
	body.add_child(main)
	body.add_child(only)
