@tool
extends RefCounted
## A reply's Markdown as RichTextLabel BBCode (chat_history.gd): headings as semibold lines,
## "- " and "1. " lines as lists, **bold**, *italics*, and `code` in the mono font on a faint
## ground. Fenced code blocks are mono too. Every block is one paragraph (a line break inside
## it is [br]), so blocks sit the label's paragraph separation apart: a blank line in the
## source is never a blank line on screen. BBCode in the source is escaped, so a reply can't
## style itself. The theme's mono font has its ligatures off (ui_theme.gd): "->" stays as typed.

static var _patterns: Dictionary = {}


## [param source] as BBCode; inline code sits on [param ground].
static func format(source: String, ground: Color) -> String:
	var blocks := PackedStringArray()
	var parts := source.split("```")
	for index: int in parts.size():
		if index % 2 == 1:
			var block := parts[index]
			var newline := block.find("\n")
			block = block.substr(newline + 1) if newline >= 0 else ""
			block = block.trim_suffix("\n")
			if not block.is_empty():
				blocks.append("[code]" + _escape(block) + "[/code]")
		else:
			var text := prose(parts[index], ground)
			if not text.is_empty():
				blocks.append(text)
	return "\n".join(blocks)


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
	if not list.is_empty():
		out[-1] += "[/%s]" % list
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
