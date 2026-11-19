@tool
extends HBoxContainer
## An app at work in a change card's status line (sidebar_change_card.gd): its icon at text
## size with its name beside it, the header of the steps below. The app's official logo in its
## own colours when the theme has one, else its tool icon tinted, else the name alone.

const UI_THEME := preload("ui_theme.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")


func _init(app := "") -> void:
	theme_type_variation = "JoineryRowTight"
	accessibility_name = app
	var slug := app.to_lower().replace(" ", "_")
	var base := (get_script() as Script).resource_path.get_base_dir()
	for name: String in [slug, slug.replace("_", "-"), slug.replace("_", "")]:
		if UI_THEME.has_app_logo(name):
			var logo := STYLE.glyph("")
			STYLE.on_theme(logo, func() -> void: logo.texture = UI_THEME.app_logo(logo, name))
			add_child(logo)
			break
		var path := base.path_join("icons/tools/%s.svg" % name)
		if FileAccess.file_exists(path):
			var picture := STYLE.glyph("text_muted")
			STYLE.on_theme(
				picture,
				func() -> void:
					var image := Image.new()
					var svg := FileAccess.get_file_as_string(path)
					if image.load_svg_from_string(svg, UI_THEME.unit(picture)) == OK:
						picture.texture = ImageTexture.create_from_image(image)
			)
			add_child(picture)
			break
	# The name keeps its full width: it is the glyph's meaning, never cut to an ellipsis.
	var label := UI.label(app, "JoineryCaption")
	label.text_overrun_behavior = TextServer.OVERRUN_NO_TRIMMING
	label.clip_text = false
	label.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	add_child(label)
