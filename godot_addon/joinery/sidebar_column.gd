@tool
extends Container
## One column of sidebar content at most MAX_W[[member cap]] wide, centred once the sidebar is
## wider (sidebar_style.centered_column). Its children fill the column; its minimum size is
## theirs, so the column never widens the sidebar and a narrow sidebar is filled edge to edge.

const UI_THEME := preload("ui_theme.gd")

## The MAX_W token: "column" (pages and the chat), "group" (settings) or "measure".
var cap := "column":
	set(value):
		cap = value
		queue_sort()


## The column's rectangle inside this container, in local coordinates.
func column_rect() -> Rect2:
	var width := minf(size.x, float(UI_THEME.max_w(self, cap)))
	return Rect2(floorf((size.x - width) / 2.0), 0.0, width, size.y)


func _notification(what: int) -> void:
	if what == NOTIFICATION_SORT_CHILDREN:
		var rect := column_rect()
		for child: Node in get_children():
			var control := child as Control
			if control != null and control.visible and not control.top_level:
				fit_child_in_rect(control, rect)
	elif what == NOTIFICATION_THEME_CHANGED:
		queue_sort()


func _get_minimum_size() -> Vector2:
	var result := Vector2.ZERO
	for child: Node in get_children():
		var control := child as Control
		if control != null and control.visible and not control.top_level:
			result = result.max(control.get_combined_minimum_size())
	return result
