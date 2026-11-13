@tool
extends MarginContainer
## A quiet "Select a node" in the Inspector dock while the Inspector shows nothing, so the
## empty half-panel says what it is for instead of looking broken. It sits over the Inspector
## in the dock's MarginContainer (a ScrollContainer takes only one child), inset back inside
## the dock where that container reaches past it, ignores the mouse and hides as soon as the
## Inspector edits something.

const UI_THEME := preload("ui_theme.gd")
const STYLE := preload("sidebar_style.gd")
const TEXT := "Select a node to see its properties"

var label: Label
var _inspector: EditorInspector


## Put the hint over [param inspector] and follow what it edits.
static func attach(inspector: EditorInspector) -> Control:
	if inspector == null or not inspector.get_parent() is Container:
		return null
	var hint: Control = preload("inspector_hint.gd").new()
	hint.set("_inspector", inspector)
	inspector.get_parent().add_child(hint)
	inspector.get_parent().move_child(hint, inspector.get_index() + 1)
	inspector.edited_object_changed.connect(hint.sync)
	hint.call("sync")
	return hint


func _init() -> void:
	name = "JoinerySelectHint"
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	label = Label.new()
	label.text = TEXT
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	label.tooltip_text = TEXT
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(label)
	# Overrides set on a theme change would raise another one; the helper guards against it.
	STYLE.on_theme_overrides(
		self,
		func() -> void:
			label.add_theme_color_override("font_color", UI_THEME.color(self, "text_faint"))
			label.add_theme_font_size_override("font_size", UI_THEME.font_size(self, "caption"))
			var parent := get_parent() as MarginContainer
			var pad := UI_THEME.space(self, 2)
			for side: String in ["left", "right", "top", "bottom"]:
				var reach := 0
				if parent != null:
					reach = maxi(0, -parent.get_theme_constant("margin_" + side))
				add_theme_constant_override("margin_" + side, reach + pad)
	)


## Shown only while the Inspector edits nothing.
func sync() -> void:
	visible = is_instance_valid(_inspector) and _inspector.get_edited_object() == null
