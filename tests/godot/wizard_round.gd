extends RefCounted
## The new-project wizard's optional "How it plays" round, for project_setup_flow_test.gd:
## open it on step 3, pick one answer, skip one and leave the rest; then check the brief
## marks each one by where it came from. Each function returns what went wrong.


static func _picker(dialog: Window, accessible: String) -> OptionButton:
	for node: Node in dialog.find_children("*", "OptionButton", true, false):
		var picker := node as OptionButton
		if picker.get_accessibility_name() == accessible and picker.is_visible_in_tree():
			return picker
	return null


static func answer(dialog: Window) -> PackedStringArray:
	var toggle: Button = null
	for node: Node in dialog.find_children("*", "Button", true, false):
		var button := node as Button
		if button.text.begins_with("Answer ") and button.is_visible_in_tree():
			toggle = button
	if toggle == null:
		return PackedStringArray(["Step 3 offers the starting point's questions"])
	if _picker(dialog, "What kind of game is it?") != null:
		return PackedStringArray(["The questions start closed"])
	toggle.button_pressed = true
	var kind := _picker(dialog, "What kind of game is it?")
	var hard := _picker(dialog, "What makes it hard?")
	if kind == null or hard == null:
		return PackedStringArray(["Opening the round shows a picker per question"])
	for index: int in kind.item_count:
		if kind.get_item_metadata(index) == "puzzle":
			kind.select(index)
			kind.item_selected.emit(index)
	hard.select(hard.item_count - 1)
	hard.item_selected.emit(hard.item_count - 1)
	return PackedStringArray()


static func check(project: String) -> PackedStringArray:
	var brief := FileAccess.get_file_as_string(project.path_join("design/brief.md"))
	var problems := PackedStringArray()
	if not brief.contains("- Genre: Puzzle: working things out matters most. _(your answer)_"):
		problems.append("A picked answer is the user's own in the brief")
	if not brief.contains("- What makes it hard: _Not decided yet._ _(skipped)_"):
		problems.append("A skipped question stays open in the brief")
	if not brief.contains("_(suggested default, not confirmed)_"):
		problems.append("A suggestion left standing is marked as not confirmed")
	return problems
