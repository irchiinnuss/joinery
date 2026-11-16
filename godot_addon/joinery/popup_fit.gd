@tool
extends RefCounted
## Keeps Joinery's menus inside the editor's window, not only inside the screen (Godot's own
## clamp): a menu opens below its button, or above it when there is more room there, and ends
## at the button's right edge when it would run past the window's. A button at the sidebar's
## right edge or at the bottom of the composer would otherwise open its menu over whatever lies
## beside the window, or cover the button once Godot pushes it back onto the screen.


## Fit [param button]'s menu each time it opens.
static func menu(button: MenuButton) -> void:
	var popup := button.get_popup()
	popup.about_to_popup.connect(func() -> void: place(popup, button))


## Place [param popup] beside [param anchor], inside the window [param anchor] is in. Call it
## from the popup's about_to_popup, before Godot sizes and shows it.
static func place(popup: Window, anchor: Control) -> void:
	if not is_instance_valid(anchor) or not anchor.is_inside_tree():
		return
	var window := anchor.get_window()
	var origin: Vector2 = Vector2.ZERO if popup.is_embedded() else Vector2(window.position)
	var area: Rect2 = Rect2(origin, Vector2(window.size))
	var at := anchor.get_global_rect()
	at.position += origin
	var size := Vector2(popup.size).max(popup.get_contents_minimum_size())
	var spot := Vector2(at.position.x, at.end.y)
	if spot.x + size.x > area.end.x:
		spot.x = at.end.x - size.x
	spot.x = clampf(spot.x, area.position.x, maxf(area.position.x, area.end.x - size.x))
	var below: float = area.end.y - at.end.y
	var above: float = at.position.y - area.position.y
	if spot.y + size.y > area.end.y and above > below:
		spot.y = maxf(area.position.y, at.position.y - size.y)
		# MenuButton capped its height at the room below the button; above, the room is more.
		popup.max_size = Vector2i.ZERO
	popup.position = Vector2i(spot.round())
