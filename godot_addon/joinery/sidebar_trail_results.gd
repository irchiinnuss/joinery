@tool
extends VBoxContainer
## A test run's results under its line in the opened work trail (sidebar_trail.gd): grouped by
## folder when there are several, each failure by name with its one-line reason, and the
## passed ones briefly, on one quiet line per group. Built again from each event, which
## carries the whole run (joinery/test_progress.py).

const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")


func _init() -> void:
	name = "Results"
	theme_type_variation = "JoineryTight"
	accessibility_name = "Test results"
	size_flags_horizontal = Control.SIZE_EXPAND_FILL


## Show [param results] ([{name, area, passed, reason}]).
func show_results(results: Array) -> void:
	for child: Node in get_children():
		remove_child(child)
		child.queue_free()
	var areas: Array[String] = []
	for result: Variant in results:
		var area := str((result as Dictionary).get("area", ""))
		if not areas.has(area):
			areas.append(area)
	for area: String in areas:
		var group := results.filter(
			func(each: Variant) -> bool: return str((each as Dictionary).get("area", "")) == area
		)
		if areas.size() > 1:
			var heading := UI.label(
				area.capitalize() if not area.is_empty() else "Other", "JoineryCaption"
			)
			heading.name = "Area"
			add_child(heading)
		var passed := PackedStringArray()
		for result: Dictionary in group:
			if bool(result.get("passed", false)):
				passed.append(str(result.get("name", "")))
			else:
				add_child(_failure(result))
		if not passed.is_empty():
			add_child(_passed(passed))
	visible = get_child_count() > 0


## A failed test: the error mark, its name, and why under it.
func _failure(result: Dictionary) -> Control:
	var row := HBoxContainer.new()
	row.name = "Failed"
	row.theme_type_variation = "JoineryRowTight"
	var mark := STYLE.glyph("danger")
	STYLE.set_glyph(mark, "error", "danger")
	mark.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	row.add_child(mark)
	var words := VBoxContainer.new()
	words.theme_type_variation = "JoineryFlush"
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(words)
	var name_label := UI.label(str(result.get("name", "")), "JoineryCaption", true)
	words.add_child(name_label)
	var why := str(result.get("reason", ""))
	if not why.is_empty():
		var reason := UI.label(why, "JoineryFaint", true)
		reason.name = "Reason"
		words.add_child(reason)
	return row


## The tests that passed, on one quiet line.
func _passed(names: PackedStringArray) -> Control:
	var row := HBoxContainer.new()
	row.name = "Passed"
	row.theme_type_variation = "JoineryRowTight"
	var mark := STYLE.glyph("text_muted")
	STYLE.set_glyph(mark, "check", "text_muted")
	mark.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	row.add_child(mark)
	var label := UI.label(", ".join(names), "JoineryFaint", true)
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	label.tooltip_text = "Passed: " + ", ".join(names)
	row.add_child(label)
	return row
