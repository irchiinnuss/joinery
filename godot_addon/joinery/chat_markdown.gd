@tool
extends RefCounted
## A reply's Markdown (chat_history.gd): headings as semibold lines, "- " and "1. " lines as
## lists, **bold**, *italics*, and `code` in the mono font on a faint ground. Every block is one
## paragraph (a line break inside it is [br]), so blocks sit the label's paragraph separation
## apart: a blank line in the source is never a blank line on screen. BBCode in the source is
## escaped, so a reply can't style itself. The theme's mono font has its ligatures off
## (ui_theme.gd): "->" stays as typed.
##
## A fenced code block is a box of its own under the text before it: mono, never wrapped, and
## scrolled sideways when a line is wider than the conversation, so code keeps its shape at any
## width. The mouse wheel over it still scrolls the conversation (Shift+wheel scrolls the code).

const UI_THEME := preload("ui_theme.gd")
const STYLE := preload("sidebar_style.gd")
const CODE := "joinery_code"

static var _patterns: Dictionary = {}


## Show [param source] in [param body]'s parts ([param parts]: the reply's first label, then a
## control per block), updating them in place while it streams. [param make_label] returns a
## new reply label, set up and scaled like the first.
static func render(body: VBoxContainer, parts: Array, source: String, make_label: Callable) -> void:
	var wanted := blocks(source, body.get_theme_color("surface_raised", "Joinery"))
	for index: int in wanted.size():
		var code: bool = wanted[index][0]
		if index < parts.size() and (parts[index] as Control).has_meta(CODE) != code:
			for extra: Control in parts.slice(index):
				extra.queue_free()
			parts.resize(index)
		if index >= parts.size():
			var part: Control = _code_block(make_label, body) if code else make_label.call()
			body.add_child(part)
			parts.append(part)
		var label: RichTextLabel = (parts[index] as Control).get_meta(CODE, parts[index])
		var text: String = wanted[index][1]
		if label.text != text:
			label.text = text
		(parts[index] as Control).visible = not text.is_empty()
	for extra: Control in parts.slice(wanted.size()):
		extra.queue_free()
	parts.resize(wanted.size())


## The reply's blocks as [code?, text]: the text's BBCode, or a code block's plain lines. The
## first is always text (empty when the reply starts with code).
static func blocks(source: String, ground: Color) -> Array:
	var result: Array = []
	var pieces := source.split("```")
	for index: int in pieces.size():
		if index % 2 == 0:
			var text := prose(pieces[index], ground)
			if not text.is_empty() or result.is_empty():
				result.append([false, text])
			continue
		var block := pieces[index]
		var newline := block.find("\n")
		block = block.substr(newline + 1).trim_suffix("\n") if newline >= 0 else ""
		if not block.is_empty():
			result.append([true, block])
	return result


## The words a reply shows, block by block, for tests and the right-click menu.
static func shown(parts: Array) -> String:
	var words := PackedStringArray()
	for part: Control in parts:
		if is_instance_valid(part) and part.visible:
			var label: RichTextLabel = part.get_meta(CODE, part)
			words.append(label.get_parsed_text())
	return "\n".join(words)


## A code block: a box in the inline code's ground holding a mono label that never wraps,
## scrolled sideways when it is wider than the conversation.
static func _code_block(make_label: Callable, context: Control) -> PanelContainer:
	var box := PanelContainer.new()
	box.name = "CodeBlock"
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_AUTO
	scroll.vertical_scroll_mode = ScrollContainer.SCROLL_MODE_SHOW_NEVER
	scroll.follow_focus = false
	# The wheel goes on to the conversation; only Shift+wheel or a trackpad scrolls the code.
	scroll.mouse_filter = Control.MOUSE_FILTER_PASS
	box.add_child(scroll)
	var label: RichTextLabel = make_label.call()
	label.name = "Code"
	label.bbcode_enabled = false
	label.autowrap_mode = TextServer.AUTOWRAP_OFF
	label.mouse_filter = Control.MOUSE_FILTER_PASS
	label.add_theme_font_override("normal_font", UI_THEME.mono_font(context))
	label.add_theme_constant_override("paragraph_separation", 0)
	scroll.add_child(label)
	box.set_meta(CODE, label)
	var fit := func() -> void:
		var need := label.get_combined_minimum_size()
		var bar := scroll.get_h_scroll_bar()
		var wide := need.x > scroll.size.x + 0.5
		scroll.custom_minimum_size.y = need.y + (bar.get_combined_minimum_size().y if wide else 0.0)
	label.minimum_size_changed.connect(fit)
	scroll.resized.connect(fit)
	STYLE.on_theme_overrides(
		box,
		func() -> void:
			var panel := StyleBoxFlat.new()
			panel.bg_color = box.get_theme_color("surface_raised", "Joinery")
			panel.set_corner_radius_all(UI_THEME.radius(box, "md"))
			panel.set_content_margin_all(float(UI_THEME.space(box, 2)))
			panel.content_margin_left = float(UI_THEME.space(box, 3))
			panel.content_margin_right = panel.content_margin_left
			box.add_theme_stylebox_override("panel", panel)
	)
	return box


## Text outside code fences: headings, lists and paragraphs, each its own paragraph.
static func prose(source: String, ground: Color) -> String:
	var out := PackedStringArray()
	var list := ""
	# The previous line was a paragraph's: the next plain line continues it.
	var follows := false
	for raw: String in source.split("\n"):
		var line := raw.strip_edges(false, true)
		if line.strip_edges().is_empty():
			follows = false
			continue
		var heading := _regex("heading").search(line)
		var bullet := _regex("bullet").search(line)
		var numbered := _regex("numbered").search(line)
		var kind := "ul" if bullet != null else "ol" if numbered != null else ""
		if heading != null:
			kind = ""
		if not list.is_empty() and kind != list:
			out[-1] += "[/%s]" % list
			list = ""
		var piece := inline(line.strip_edges(), ground)
		var plain := heading == null and kind.is_empty()
		if plain and follows and not out.is_empty():
			out[-1] += "[br]" + piece  # A line break inside a paragraph, not a new one.
			continue
		follows = plain
		if heading != null:
			piece = "[b]" + inline(heading.get_string(1), ground) + "[/b]"
		elif not kind.is_empty():
			var item := inline((bullet if bullet != null else numbered).get_string(1), ground)
			if item.begins_with("[bgcolor"):
				item = "\u2060" + item  # The bullet takes the body font, not the code's.
			if list == kind:
				out[-1] += "\n" + item
				continue
			list = kind
			piece = "[%s]%s" % ["ul" if kind == "ul" else "ol type=1", item]
		out.append(piece)
	# A list that ends the text stays open: closing it would add an empty line under it.
	return "\n".join(out)


## One line's spans: `code`, **bold** and *italics* (or _italics_); the rest escaped.
static func inline(text: String, ground: Color) -> String:
	var result := ""
	var spans := text.split("`")
	for index: int in spans.size():
		var span := spans[index]
		if index % 2 == 1 and index < spans.size() - 1:
			result += "[bgcolor=#%s][code]%s[/code][/bgcolor]" % [ground.to_html(), _escape(span)]
			continue
		if index % 2 == 1:
			span = "`" + span  # An unclosed tick, as typed.
		var safe := _escape(span)
		for key: String in ["bold", "strong", "italic", "emphasis"]:
			var tag := "b" if key in ["bold", "strong"] else "i"
			safe = _regex(key).sub(safe, "[%s]$1[/%s]" % [tag, tag], true)
		result += safe
	return result


static func _escape(text: String) -> String:
	return text.replace("[", "[lb]")


static func _regex(name: String) -> RegEx:
	if _patterns.is_empty():
		var sources := {
			"heading": "^\\s{0,3}#{1,6}\\s+(.*?)\\s*#*\\s*$",
			"bullet": "^\\s*[-*+]\\s+(.*)$",
			"numbered": "^\\s*\\d{1,3}[.)]\\s+(.*)$",
			"bold": "\\*\\*([^*\\n]+)\\*\\*",
			"strong": "(?<!\\w)__([^_\\n]+)__(?!\\w)",
			"italic": "(?<![\\w*])\\*(?!\\s)([^*\\n]+?)\\*(?![\\w*])",
			"emphasis": "(?<!\\w)_(?!\\s)([^_\\n]+?)_(?!\\w)",
		}
		for key: String in sources:
			var regex := RegEx.new()
			regex.compile(sources[key])
			_patterns[key] = regex
	return _patterns[name]
