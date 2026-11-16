@tool
extends RefCounted
## A file's name on a line of the work trail (sidebar_trail.gd) as a link: "Read
## [backyard_blockout.tscn]", "Edited [level.gd] +12 −3". The line's own words stay in its
## Words label (the trail rewrites them: past tense, the live line); while a line names a file
## by its project path, a row of three parts shows instead: the words before the name, the name
## in the accent colour (lighter on hover, a focus ring from the keyboard) and the words after
## it. The link's accessible name is "Open <file>"; pressing it calls the trail's opener with
## the res:// path (sidebar_file_open.gd decides where it opens).

const UI := preload("ui_components.gd")
const UI_THEME := preload("ui_theme.gd")
const STYLE := preload("sidebar_style.gd")
## The meta on a Words label: the res:// path its line names.
const PATH_META := "trail_path"
## Marks a link set inside a line of words (the visual checks' inline target exception).
const INLINE_META := "joinery_inline_link"
## The widest a name gets, in caption font sizes.
const WIDEST_EM := 16


## Show [param words] with its file name as a link, or as plain words when its text no longer
## names the file. [param path] is res://; "" keeps the one set before. [param opened] gets the
## path on a press.
static func render(words: Label, path: String, opened: Callable) -> void:
	if not path.is_empty():
		words.set_meta(PATH_META, path)
	var target := str(words.get_meta(PATH_META, ""))
	var name := target.get_file()
	var at := words.text.find(name) if not name.is_empty() else -1
	var linked := words.get_parent().get_node_or_null(NodePath(words.name + "Linked")) as Control
	if at < 0:
		if linked != null:
			linked.hide()
		words.show()
		return
	if linked == null:
		linked = _build(words, opened)
	var parts := linked.get_children()
	(parts[0] as Label).text = words.text.substr(0, at)
	(parts[0] as Label).visible = at > 0
	var link := parts[1] as Button
	link.text = name
	link.accessibility_name = "Open " + name
	link.tooltip_text = target.trim_prefix("res://")
	_fit(link)
	(parts[2] as Label).text = words.text.substr(at + name.length())
	words.hide()
	linked.show()


## Forget the file of [param words] (the live line moved to a line that names none).
static func clear(words: Label) -> void:
	words.remove_meta(PATH_META)
	render(words, "", Callable())


## What shows in place of [param words]: its linked row, or the label itself.
static func shown(words: Label) -> Control:
	var linked := words.get_parent().get_node_or_null(NodePath(words.name + "Linked")) as Control
	return linked if linked != null and linked.visible else words


## The link of [param words]' line, or null when it shows none.
static func link_of(words: Label) -> Button:
	var linked := words.get_parent().get_node_or_null(NodePath(words.name + "Linked")) as Control
	if linked == null or not linked.visible:
		return null
	return linked.get_child(1) as Button


## The name in caption type on the line's own height, as wide as the name, up to
## [constant WIDEST_EM] font sizes (a longer one is cut with an ellipsis). A link inside a
## line of words keeps the line's height (the inline exception to the 24 px target size,
## WCAG 2.5.8); its focus ring still shows from the keyboard.
static func _fit(link: Button) -> void:
	if not link.is_inside_tree():
		return
	var size := UI_THEME.font_size(link, "caption")
	link.add_theme_font_size_override("font_size", size)
	for state: String in ["normal", "hover", "pressed", "hover_pressed", "disabled"]:
		link.add_theme_stylebox_override(state, StyleBoxEmpty.new())
	var font := link.get_theme_font("font")
	var wide := font.get_string_size(link.text, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x
	link.custom_minimum_size = Vector2(minf(ceilf(wide) + 1.0, float(size * WIDEST_EM)), 0.0)


static func _build(words: Label, opened: Callable) -> Control:
	var linked := HBoxContainer.new()
	linked.name = words.name + "Linked"
	linked.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	linked.add_theme_constant_override("separation", 0)
	linked.mouse_filter = Control.MOUSE_FILTER_PASS
	var before := UI.label("", words.theme_type_variation)
	before.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	before.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	linked.add_child(before)
	var link := UI.link("")
	link.name = "Link"
	link.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	link.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	link.clip_text = true
	link.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	link.set_meta(INLINE_META, true)
	STYLE.on_theme_overrides(link, func() -> void: _fit(link))
	link.pressed.connect(
		func() -> void:
			if opened.is_valid():
				opened.call(str(words.get_meta(PATH_META, "")))
	)
	linked.add_child(link)
	var after := UI.label("", words.theme_type_variation)
	after.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	after.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	linked.add_child(after)
	var line := words.get_parent()
	line.add_child(linked)
	line.move_child(linked, words.get_index() + 1)
	return linked
