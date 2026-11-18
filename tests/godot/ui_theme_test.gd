extends SceneTree
## Design tokens, theme, live re-theming, icons, motion and building blocks, headless.

const UI_THEME := preload("res://addons/joinery/ui_theme.gd")
const UI_MOTION := preload("res://addons/joinery/ui_motion.gd")
const UI := preload("res://addons/joinery/ui_components.gd")
const INDICATOR := preload("res://addons/joinery/activity_indicator.gd")
const SETTINGS := preload("res://addons/joinery/journeyman_settings.gd")
const STYLE := preload("res://addons/joinery/sidebar_style.gd")
const BLOCKS := preload("res://addons/joinery/ui_blocks.gd")
const TEXT_ROLES := ["text", "text_muted", "text_faint", "accent", "success", "danger"]
const SURFACES := ["surface", "surface_sunken", "surface_raised"]
const LABELS := [
	"JoineryCaption",
	"JoineryFaint",
	"JoineryBodyLarge",
	"JoineryCardTitle",
	"JoineryTitle",
	"JoineryDisplay",
	"JoineryError",
	"JoineryNumeric",
	"JoinerySection",
]
const BUTTONS := [
	"JoineryPrimaryButton",
	"JoinerySecondaryButton",
	"JoineryLinkButton",
	"JoineryQuietButton",
	"JoineryQuietLink",
	"JoineryRowButton",
	"JoineryIconButton",
	"JoinerySendButton",
	"JoineryStopButton",
]
const PANELS := ["JoineryCard", "JoineryCardSelected", "JoineryBanner"]

var _failed := false
var _host: Control


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _run() -> void:
	var config := OS.get_cache_dir().path_join("joinery-ui-theme-test/journeyman.cfg")
	DirAccess.remove_absolute(config)
	Engine.set_meta("joinery_ui_config", config)
	Engine.remove_meta("joinery_ui_prefs")
	_host = Control.new()
	_host.size = Vector2(480, 640)
	root.add_child(_host)
	_check(not UI_MOTION.is_enabled(), "Motion is off headless unless a test asks for it")
	_check_contrast()
	_check_tokens()
	_check_build()
	_check_icons()
	await _check_watch(config)
	await _check_motion()
	await _check_components()
	await _check_blocks()
	await _check_column()
	await _check_settings()
	_host.queue_free()
	Engine.remove_meta("joinery_ui_config")
	Engine.remove_meta("joinery_ui_prefs")
	await process_frame
	print("ui theme: ok" if not _failed else "ui theme: failed")
	quit(1 if _failed else 0)


static func _contrast(a: Color, b: Color) -> float:
	var la := _luminance(a)
	var lb := _luminance(b)
	return (maxf(la, lb) + 0.05) / (minf(la, lb) + 0.05)


static func _luminance(value: Color) -> float:
	var channels: Array[float] = []
	for channel: float in [value.r, value.g, value.b]:
		channels.append(
			channel / 12.92 if channel <= 0.04045 else pow((channel + 0.055) / 1.055, 2.4)
		)
	return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


func _check_contrast() -> void:
	for mode: String in ["dark", "light"]:
		_check(UI_THEME.set_pref("theme", mode) == OK, "Save the theme preference")
		for accent: String in UI_THEME.ACCENTS:
			_check(UI_THEME.set_pref("accent", accent) == OK, "Save the accent preference")
			var colors := UI_THEME.palette(_host)
			var where := "%s/%s" % [mode, accent]
			for role: String in TEXT_ROLES:
				for surface: String in SURFACES:
					var ratio := _contrast(colors[role], colors[surface])
					_check(ratio >= 4.5, "%s %s on %s is %.2f:1" % [where, role, surface, ratio])
			for fill: String in ["accent", "accent_hover", "accent_pressed"]:
				var ratio := _contrast(colors["on_accent"], colors[fill])
				_check(ratio >= 4.5, "%s on_accent on %s is %.2f:1" % [where, fill, ratio])
			for fill: String in ["accent_tint", "danger_tint"]:
				var ratio := _contrast(colors["text"], colors[fill])
				_check(ratio >= 4.5, "%s text on %s is %.2f:1" % [where, fill, ratio])
			for surface: String in SURFACES:
				var ratio := _contrast(colors["border_strong"], colors[surface])
				_check(ratio >= 3.0, "%s input border on %s is %.2f:1" % [where, surface, ratio])
			for alias: String in ["secondary", "inset", "raised", "border", "focus", "primary"]:
				_check(not colors.has(alias), "The old palette name %s is gone" % alias)
			_check_sliders(where, colors)
	_check(UI_THEME.set_pref("theme", "match") == OK, "Restore the theme preference")
	_check(UI_THEME.set_pref("accent", "brass") == OK, "Restore the accent preference")


## Slider knobs and tracks, as built, read on every surface (3:1), both ways round.
func _check_sliders(where: String, colors: Dictionary) -> void:
	var theme := UI_THEME.build(_host)
	for type_name: String in ["HSlider", "VSlider"]:
		var track := theme.get_stylebox("slider", type_name) as StyleBoxFlat
		var filled := theme.get_stylebox("grabber_area", type_name) as StyleBoxFlat
		var knob := theme.get_icon("grabber", type_name)
		_check(track != null and filled != null and knob != null, "%s is styled" % type_name)
		if track == null or filled == null or knob == null:
			continue
		var image := knob.get_image()
		var middle := image.get_pixel(image.get_width() / 2, image.get_height() / 2)
		var edge := image.get_pixel(image.get_width() / 2, 0)
		_check(edge.a < 1.0 and middle.a == 1.0, "%s knob is a disc" % type_name)
		var parts := {"track": track.bg_color, "fill": filled.bg_color, "knob": middle}
		for part: String in parts:
			for surface: String in SURFACES:
				var ratio := _contrast(parts[part], colors[surface])
				_check(
					ratio >= 3.0,
					"%s %s %s on %s is %.2f:1" % [where, type_name, part, surface, ratio]
				)


func _check_tokens() -> void:
	# The nesting table: composer, command popup, provider card.
	_check(UI_THEME.nested(16, 8) == 8, "Composer children use radius 8")
	_check(UI_THEME.nested(12, 4) == 8, "Command popup rows use radius 8")
	_check(UI_THEME.nested(12, 12) == 4, "Provider icon tiles use radius 4")
	var size := _host.get_theme_default_font_size()
	var unit := maxf(size / 14.0, 1.0)
	_check(is_equal_approx(UI_THEME.unit(_host), unit), "Unit follows the font size")
	_check(UI_THEME.space(_host, 3) == roundi(12 * unit), "Spacing scales with the unit")
	_check(UI_THEME.radius(_host, "md") == roundi(8 * unit), "Radii scale with the unit")
	_check(UI_THEME.control_h(_host, "md") == roundi(size * 2.5), "Control heights follow the font")
	_check(UI_THEME.font_size(_host, "caption") == roundi(size * 0.85), "Caption is 0.85 em")
	_check(UI_THEME.MOTION["exit"] <= UI_THEME.MOTION["enter"] * 0.8 + 0.001, "Exits are shorter")
	_check(UI_THEME.MOTION["menu"] >= 0.15 and UI_THEME.MOTION["menu"] <= 0.25, "Menus 150–250 ms")
	var file := ConfigFile.new()
	file.load(Engine.get_meta("joinery_ui_config"))
	_check(file.get_value("appearance", "theme", "") == "match", "Theme is saved in [appearance]")
	_check(UI_THEME.set_pref("text_size", "Large") == OK, "Save the text size")
	file.load(Engine.get_meta("joinery_ui_config"))
	_check(
		file.get_value("journeyman", "text_size", "") == "Large", "Text size stays in [journeyman]"
	)
	_check(UI_THEME.set_pref("text_size", "Normal") == OK, "Restore the text size")
	# The named size exceptions (design direction v2 #62), and nothing else outside the scales.
	_check(UI_THEME.ICON == {"sm": 16, "md": 20, "tile": 28}, "ICON token")
	_check(UI_THEME.DOT == 8 and UI_THEME.SEND == 32 and UI_THEME.TRANSPORT == 40, "Size tokens")
	_check(UI_THEME.DIALOG["connect"] == Vector2(32, 36), "Connect dialog size in em")
	_check(UI_THEME.DIALOG["doc"] == Vector2(48, 36), "Document dialog size in em")
	_check(UI_THEME.icon_px(_host, "md") == roundi(20 * unit), "Rail icons are ICON.md")
	_check(UI_THEME.dot_px(_host) == roundi(8 * unit), "Dots are DOT")
	_check(UI_THEME.send_px(_host) == roundi(32 * unit), "Send is SEND")
	_check(UI_THEME.transport_px(_host) == roundi(40 * unit), "Play is TRANSPORT")
	var body := UI_THEME.font_size(_host, "body")
	_check(
		UI_THEME.dialog_size(_host, "doc") == Vector2i(48 * body, 36 * body), "Dialogs are in em"
	)
	var built := UI_THEME.build(_host)
	_check(
		built.get_constant("icon_max_width", "Button") == roundi(16 * unit),
		"Inline icons are ICON.sm"
	)
	var numeric := UI_THEME.numeric_font(_host) as FontVariation
	var tag := TextServerManager.get_primary_interface().name_to_tag("tnum")
	_check(
		numeric != null and numeric.opentype_features.get(tag) == 1, "Numbers use tabular digits"
	)


func _check_build() -> void:
	var theme := UI_THEME.build(_host)
	for type_name: String in LABELS:
		_check(
			theme.get_type_variation_base(type_name) == "Label", type_name + " is a Label variation"
		)
	for type_name: String in BUTTONS:
		_check(
			theme.get_type_variation_base(type_name) == "Button",
			type_name + " is a Button variation"
		)
	for type_name: String in PANELS:
		_check(
			theme.get_type_variation_base(type_name) == "PanelContainer", type_name + " is a panel"
		)
	_check(theme.get_type_variation_base("JoineryPicker") == "OptionButton", "Picker variation")
	_check(theme.get_type_variation_base("JoinerySwitch") == "CheckButton", "Switch variation")
	_check(theme.get_stylebox("normal", "RichTextLabel") is StyleBoxEmpty, "Replies have no inset")
	_check(theme.get_stylebox("normal", "Label") is StyleBoxEmpty, "Labels have no inset")
	var colors := UI_THEME.palette(_host)
	_check(theme.get_color("caret_color", "TextEdit") == colors["accent"], "One caret colour")
	_check(theme.get_color("caret_color", "LineEdit") == colors["accent"], "One caret colour")
	_check(
		theme.get_color("font_placeholder_color", "LineEdit") == colors["text_faint"],
		"Placeholders use text_faint"
	)
	var send: StyleBoxFlat = theme.get_stylebox("normal", "JoinerySendButton")
	var primary: StyleBoxFlat = theme.get_stylebox("normal", "JoineryPrimaryButton")
	_check(send.bg_color == primary.bg_color, "Send and primary share the accent fill")
	var ring: StyleBoxFlat = theme.get_stylebox("focus", "Button")
	var button: StyleBoxFlat = theme.get_stylebox("normal", "Button")
	_check(ring.border_color == colors["accent"], "Focus rings use the accent")
	_check(
		ring.corner_radius_top_left == button.corner_radius_top_left + int(ring.expand_margin_left),
		"Focus ring corners stay concentric with the button"
	)
	var size := _host.get_theme_default_font_size()
	var checked := theme.get_icon("checked", "CheckButton")
	_check(
		checked.get_size() == Vector2(roundi(2.0 * size), roundi(1.1 * size)),
		"Switch track is 2.0 × 1.1 em"
	)
	_check(theme.get_stylebox("normal", "CheckButton") is StyleBoxEmpty, "Switch has no box")
	_check(theme.has_icon("frame_3", "JoinerySwitch"), "Switch travel frames are built")
	# A checked box shows its state with the glyph; no fill behind the row, hovered or not.
	for state: String in ["pressed", "hover_pressed"]:
		var box := theme.get_stylebox(state, "CheckBox") as StyleBoxFlat
		_check(box != null and box.bg_color.a == 0.0, "CheckBox %s fill is transparent" % state)
	# Hovering a selected card keeps the selected look.
	_check(
		(
			theme.get_stylebox("hover_pressed", "JoineryCardButton")
			== theme.get_stylebox("pressed", "JoineryCardButton")
		),
		"A selected card keeps its look on hover"
	)
	_check(
		theme.get_type_variation_base("JoineryHoverCard") == "PanelContainer",
		"The hover card is a panel"
	)
	# One control height: inputs, buttons and pickers in a row share top and bottom edges.
	var holder := VBoxContainer.new()
	holder.theme = theme
	_host.add_child(holder)
	var expected := float(UI_THEME.control_h(_host, "md"))
	var controls: Array[Control] = [LineEdit.new(), Button.new(), OptionButton.new()]
	for variation: String in [
		"JoineryPrimaryButton", "JoinerySecondaryButton", "JoineryIconButton"
	]:
		var other := Button.new()
		other.theme_type_variation = variation
		if variation == "JoineryIconButton":
			other.icon = UI_THEME.icon(_host, "close")
		else:
			other.text = "Accept"
		controls.append(other)
	var check := CheckButton.new()
	check.text = "Show reply times"
	controls.append(check)
	(controls[1] as Button).text = "Accept"
	(controls[2] as OptionButton).add_item("Auto")
	for control: Control in controls:
		holder.add_child(control)
		var height := control.get_combined_minimum_size().y
		_check(
			absf(height - expected) <= 1.0,
			"%s is %.1f px tall, expected %.1f" % [control.get_class(), height, expected]
		)
	holder.queue_free()


func _check_icons() -> void:
	for name: String in UI_THEME.ICONS:
		var texture := UI_THEME.icon(_host, name)
		_check(texture != null and texture.get_width() > 0, "Icon %s resolves headless" % name)
	var tinted := UI_THEME.icon(_host, "chevron", Color.RED)
	_check(
		tinted != null and tinted != UI_THEME.icon(_host, "chevron"), "Tinted icons are separate"
	)
	# Official app logos (icons/apps) render in their own colours; Blender has none (its terms
	# need the word mark beside the logo), so the rail keeps its in-house glyph.
	for app: String in [
		"godot", "krita", "gimp", "inkscape", "material-maker", "surge-xt", "supercollider"
	]:
		var logo := UI_THEME.app_logo(_host, app)
		_check(logo != null and logo.get_width() > 0, "The %s logo renders headless" % app)
		if app not in ["inkscape", "supercollider"]:  # Black-and-white marks.
			_check(_has_colour(logo), "The %s logo keeps its own colours" % app)
	_check(not UI_THEME.has_app_logo("blender"), "No Blender logo is shipped")
	_check(UI_THEME.app_logo(_host, "my-custom-tool") == null, "Custom tools have no logo")
	for mode: String in ["dark", "light"]:
		_check(UI_THEME.set_pref("theme", mode) == OK, "Save the theme preference")
		# Inkscape's black mark has an official white variant for dark backgrounds.
		var ink := UI_THEME.app_logo(_host, "inkscape").get_image()
		var surface := UI_THEME.color(_host, "surface")
		var ratio := _contrast(_mean_opaque(ink), surface)
		_check(ratio >= 3.0, "Inkscape reads on the %s surface (%.2f:1)" % [mode, ratio])
		# An unchecked radio and check box outline reads on every surface (3:1).
		var built := UI_THEME.build(_host)
		for icon: String in ["radio_unchecked", "unchecked"]:
			var image := built.get_icon(icon, "CheckBox").get_image()
			for fill: String in SURFACES:
				var under := UI_THEME.color(_host, fill)
				# The strongest pixel down the top edge: the outline stroke, not its soft rim.
				ratio = 1.0
				for y: int in image.get_height() / 4:
					var drawn := under.blend(image.get_pixel(image.get_width() / 2, y))
					ratio = maxf(ratio, _contrast(drawn, under))
				_check(ratio >= 3.0, "%s %s on %s is %.2f:1" % [mode, icon, fill, ratio])
	_check(UI_THEME.set_pref("theme", "match") == OK, "Restore the theme preference")


## True when some opaque pixel of [param texture] is not grey.
static func _has_colour(texture: Texture2D) -> bool:
	var image := texture.get_image()
	for y: int in image.get_height():
		for x: int in image.get_width():
			var pixel := image.get_pixel(x, y)
			if pixel.a > 0.5 and pixel.s > 0.2:
				return true
	return false


## The average colour of the opaque pixels of [param image].
static func _mean_opaque(image: Image) -> Color:
	var sum := Color(0, 0, 0, 0)
	var count := 0
	for y: int in image.get_height():
		for x: int in image.get_width():
			var pixel := image.get_pixel(x, y)
			if pixel.a > 0.5:
				sum += Color(pixel.r, pixel.g, pixel.b, 0)
				count += 1
	return Color(sum.r / count, sum.g / count, sum.b / count) if count else Color.BLACK


func _check_watch(config: String) -> void:
	var script := GDScript.new()
	script.source_code = (
		"extends Control\nvar rebuilt := 0\n"
		+ "func _on_theme_rebuilt() -> void:\n\trebuilt += 1\n"
	)
	script.reload()
	var watched: Control = script.new()
	_host.add_child(watched)
	UI_THEME.watch(watched)
	_check(watched.theme != null, "watch() themes a root that has none")
	var before := watched.theme.get_color("accent", "Joinery")
	_check(UI_THEME.set_pref("accent", "violet") == OK, "Change the accent")
	await process_frame
	_check(watched.theme.get_color("accent", "Joinery") != before, "Accent applies live")
	_check(int(watched.get("rebuilt")) == 1, "The root hears about the rebuild once")
	var dark := watched.theme.get_color("surface", "Joinery")
	_check(UI_THEME.set_pref("theme", "light") == OK, "Change the theme")
	await process_frame
	await process_frame
	_check(watched.theme.get_color("surface", "Joinery") != dark, "Theme applies live")
	_check(int(watched.get("rebuilt")) == 2, "Re-theming does not loop on theme_changed")
	_check(UI_THEME.set_pref("reduce_motion", "on") == OK, "Reduce motion saves")
	await process_frame
	_check(int(watched.get("rebuilt")) == 2, "Unrelated preferences do not rebuild the theme")
	_check(UI_THEME.set_pref("reduce_motion", "off") == OK, "Reduce motion turns off")
	_check(UI_THEME.set_pref("theme", "match") == OK, "Restore the theme")
	_check(UI_THEME.set_pref("accent", "brass") == OK, "Restore the accent")
	var file := ConfigFile.new()
	_check(file.load(config) == OK, "Preferences are written to journeyman.cfg")
	watched.queue_free()
	await process_frame


func _check_motion() -> void:
	var themed := Control.new()
	themed.theme = UI_THEME.build(_host)
	_host.add_child(themed)
	var panel := PanelContainer.new()
	themed.add_child(panel)
	panel.hide()
	UI_MOTION.enter(panel)
	_check(panel.visible and panel.modulate.a == 1.0, "With motion off, enter shows at once")
	UI_MOTION.exit(panel)
	_check(not panel.visible, "With motion off, exit hides at once")
	var label := Label.new()
	themed.add_child(label)
	UI_MOTION.roll_number(label, 75, UI_MOTION.format_clock)
	_check(label.text == "1:15", "Clocks read m:ss")
	UI_MOTION.roll_number(label, 3, "%d files")
	_check(label.text == "3 files", "Counts use the format")
	UI_MOTION.set_enabled(true)
	_check(UI_MOTION.is_enabled(), "Tests can turn motion on")
	UI_MOTION.enter(panel, label)
	_check(panel.modulate.a < 1.0 and panel.scale.x < 1.0, "Popovers start small and clear")
	UI_MOTION.roll_number(label, 12, "%d files")
	_check(label.text == "12 files" and label.visible_characters == 0, "Numbers roll")
	await create_timer(UI_THEME.MOTION["base"] + 0.1).timeout
	_check(panel.modulate.a == 1.0 and panel.scale == Vector2.ONE, "Entrances settle")
	_check(label.visible_characters == -1, "The rolled number settles to plain text")
	UI_MOTION.exit(panel, label)
	await create_timer(UI_THEME.MOTION["menu"] + 0.1).timeout
	_check(not panel.visible and panel.scale == Vector2.ONE, "Exits hide and reset")
	var check := UI.switch("Reduce motion")
	themed.add_child(check)
	check.button_pressed = true
	await process_frame
	_check(check.has_theme_icon_override("checked"), "The knob travels through frames")
	await create_timer(UI_THEME.MOTION["base"] + 0.1).timeout
	_check(not check.has_theme_icon_override("checked"), "The knob settles on the theme icon")
	# A reveal cut short (its node leaves the tree, or stop()) takes its mask down.
	var box := VBoxContainer.new()
	box.size = Vector2(100, 100)
	themed.add_child(box)
	UI_MOTION.reveal(box)
	_check(box.has_meta("_joinery_reveal_state"), "A reveal masks its node")
	themed.remove_child(box)
	_check(not box.has_meta("_joinery_reveal_state"), "Leaving the tree ends the reveal")
	_check(box.clip_children == CanvasItem.CLIP_CHILDREN_DISABLED, "Clipping is restored")
	themed.add_child(box)
	UI_MOTION.reveal(box)
	UI_MOTION.stop(box)
	_check(not box.has_meta("_joinery_reveal_state"), "stop() ends the reveal")
	# A morph hands over only on landing: the target stays clear while the ghost travels.
	var source := PanelContainer.new()
	var target := PanelContainer.new()
	for node: Control in [source, target]:
		node.custom_minimum_size = Vector2(40, 20)
		themed.add_child(node)
	target.position = Vector2(200, 200)
	await process_frame
	UI_MOTION.morph(source, target)
	await create_timer(UI_THEME.MOTION["base"]).timeout
	_check(target.modulate.a == 0.0, "The morph target waits for the landing")
	await create_timer(UI_THEME.MOTION["base"] + UI_THEME.MOTION["fast"] + 0.1).timeout
	_check(target.modulate.a == 1.0 and not source.visible, "The morph lands and hands over")
	# A loading pulse runs only while it shows: a hidden loop would keep the editor redrawing.
	var page := VBoxContainer.new()
	themed.add_child(page)
	var waiting := Panel.new()
	page.add_child(waiting)
	var pulse := UI_MOTION.breathe(waiting)
	_check(pulse != null and pulse.is_running(), "A shown skeleton breathes")
	page.hide()
	_check(not pulse.is_running(), "A hidden page's skeleton stops breathing")
	await create_timer(0.3).timeout
	var resting := waiting.modulate.a
	await create_timer(0.3).timeout
	_check(waiting.modulate.a == resting, "A paused pulse changes nothing")
	page.show()
	_check(pulse.is_running(), "It breathes again once it shows")
	var hidden := Panel.new()
	hidden.hide()
	page.add_child(hidden)
	_check(not UI_MOTION.breathe(hidden).is_running(), "A skeleton that starts hidden waits")
	# A fade-in later on the same node is not the pulse's to pause.
	UI_MOTION.fade_in(hidden)
	page.hide()
	var fade: Tween = hidden.get_meta("_joinery_tween_presence")
	_check(fade.is_running(), "A fade-in is not paused by the pulse's watch")
	page.show()
	# A stopped pulse keeps its watch; showing and hiding it then must print no error.
	UI_MOTION.stop(waiting)
	page.hide()
	page.show()
	_check(not waiting.has_meta("_joinery_tween_presence"), "A stopped pulse stays stopped")
	_check(UI_THEME.set_pref("reduce_motion", "on") == OK, "Reduce motion")
	_check(not UI_MOTION.is_enabled(), "Reduce motion turns every animation off")
	_check(UI_THEME.set_pref("reduce_motion", "off") == OK, "Restore motion")
	UI_MOTION.set_enabled(false)
	themed.queue_free()
	await process_frame


func _check_components() -> void:
	var themed := VBoxContainer.new()
	themed.theme = UI_THEME.build(_host)
	_host.add_child(themed)
	var name_field := UI.field("Name", LineEdit.new(), "Shown in the editor title")
	themed.add_child(name_field)
	_check(not name_field.error.visible, "Errors start hidden")
	name_field.set_error("Write a project name")
	_check(name_field.error.visible, "set_error shows the message")
	_check(
		name_field.control.theme_type_variation == "JoineryLineEditError",
		"The field border turns red"
	)
	name_field.set_error("")
	_check(name_field.control.theme_type_variation == "", "Clearing restores the field")
	var md := float(UI_THEME.control_h(_host, "md"))
	var primary := UI.primary("Create project")
	var secondary := UI.secondary("Back")
	var picker := UI.picker(["Auto", "Fast"])
	var icon := UI.icon_button("close", "Close")
	var row := UI.row_button("Art direction")
	for control: Control in [primary, secondary, picker, icon, row]:
		themed.add_child(control)
		_check(control.custom_minimum_size.y == md, "%s is CONTROL_H.md" % control.get_class())
	_check(icon.icon != null and icon.custom_minimum_size.x == md, "Icon buttons are square")
	_check(row.icon != null, "Row buttons carry a chevron")
	var section := UI.section("Troubleshooting", true)
	themed.add_child(section)
	_check(not section.body.visible, "Collapsible sections start closed")
	(section.header as Button).button_pressed = true
	_check(section.body.visible and section.open, "The disclosure opens the section")
	var tag := UI.chip("hammer.glb", "model", true)
	themed.add_child(tag)
	var removed: Array[bool] = []
	tag.removed.connect(func() -> void: removed.append(true))
	tag.remove.pressed.emit()
	_check(removed.size() == 1, "Removing a chip reports it")
	var dot := UI.status_dot("ready")
	themed.add_child(dot)
	dot.state = "attention"
	_check(dot.custom_minimum_size.x > 0, "Status dots have a size")
	var indicator: Control = INDICATOR.new()
	themed.add_child(indicator)
	_check(not indicator.visible and not indicator.is_processing(), "Idle indicator is still")
	indicator.set("running", true)
	_check(indicator.visible and indicator.custom_minimum_size.y > 0, "Running indicator shows")
	indicator.set("running", false)
	_check(not indicator.visible and not indicator.is_processing(), "Stopped indicator hides")
	await _check_readiness_parts(themed)
	themed.queue_free()
	await process_frame


## The switch row, the status action and the readiness badge.
func _check_readiness_parts(themed: Control) -> void:
	var switch_row := UI.switch_row("Online play", "Play with friends", false)
	themed.add_child(switch_row)
	var flips: Array[bool] = []
	switch_row.toggled.connect(func(on: bool) -> void: flips.append(on))
	switch_row.switch_button.button_pressed = true
	_check(flips == [true], "A switch row reports its switch")
	switch_row.set_on(false)
	_check(flips == [true] and not switch_row.is_on(), "set_on changes it quietly")
	_check(not switch_row.reason.visible, "No reason while the switch can change")
	switch_row.set_disabled_reason("Needs online play first.")
	_check(switch_row.switch_button.disabled, "A reason disables the switch")
	_check(switch_row.reason.visible, "A disabled switch says why")
	switch_row.set_disabled_reason("")
	_check(not switch_row.switch_button.disabled and not switch_row.reason.visible, "Re-enabled")
	var action := UI.status_action({"id": "start_docker", "label": "Start Docker"})
	themed.add_child(action)
	_check(action.visible and action.text == "Start Docker", "The action shows its label")
	_check(action.action_id == "start_docker", "The action keeps its id")
	action.set_action(null)
	_check(not action.visible, "No action, no button")
	var badge := UI.readiness()
	themed.add_child(badge)
	_check(not badge.visible, "An unknown readiness shows nothing")
	(
		badge
		. set_entry(
			{
				"status": "needs_docker",
				"summary": "Krita runs offline in Docker.",
				"action": {"id": "start_docker", "label": "Start Docker"},
				"checked_at": "2026-11-10T14:02:00+00:00",
			}
		)
	)
	_check(badge.visible and badge.wording.text == "Needs Docker", "Readiness wording")
	_check(badge.dot.state == "attention", "Needs Docker is an attention dot")
	_check(badge.action.visible and badge.action.text == "Start Docker", "One action")
	_check(badge.tooltip_text.contains("Last checked "), "The tooltip says when it was checked")
	badge.set_entry({"status": "ready", "checking": true})
	_check(badge.wording.text == "Checking…" and not badge.action.visible, "Checking, no action")
	_check(UI.last_checked("") == "", "No time, no stamp")
	_check(UI.last_checked("2026-11-10T14:02:00+00:00").begins_with("Last checked "), "Stamp")
	await process_frame


func _check_settings() -> void:
	var script := GDScript.new()
	script.source_code = (
		"extends PanelContainer\nsignal reconnect_requested\nsignal connect_requested\n"
		+ "var applied: Array = []\n"
		+ 'var ran: Array = []\nvar connection_state := "error"\nvar _agent_name := ""\n'
		+ "func apply_preferences(values: Dictionary) -> void:\n\tapplied.append(values)\n"
		+ "func run_command(entry: String) -> void:\n\tran.append(entry)\n"
	)
	script.reload()
	var dock: Control = script.new()
	dock.size = Vector2(420, 700)
	_host.add_child(dock)
	var content := VBoxContainer.new()
	dock.add_child(content)
	var header := HBoxContainer.new()
	content.add_child(header)
	var chat := Label.new()
	chat.text = "chat"
	content.add_child(chat)
	var page: ScrollContainer = SETTINGS.attach(dock, header, content)
	await process_frame
	page.set_open(true)
	_check(page.visible and not header.visible and not chat.visible, "Settings replace the chat")
	var texts := _texts(page)
	for expected: String in [
		"Settings",
		"Agent",
		"Appearance",
		"Project",
		"Theme",
		"Accent",
		"Text size",
		"Reduce motion"
	]:
		_check(expected in texts, "Settings show " + expected)
	for gone: String in [
		"Editor", "Start new requests in", "Saved for you in", "Checking", "Agent: ", "Replies"
	]:
		_check(
			not texts.any(func(text: String) -> bool: return text.begins_with(gone)),
			gone + " is gone"
		)
	_check(
		texts.all(func(text: String) -> bool: return text != text.to_upper() or text.length() < 4),
		"No all-caps labels"
	)
	var text_picker: OptionButton = page.get("_text_picker")
	text_picker.select(2)
	text_picker.item_selected.emit(2)
	var applied: Array = dock.get("applied")
	_check(
		not applied.is_empty() and is_equal_approx(float(applied[-1].get("text_scale", 0)), 1.15),
		"Text size applies through apply_preferences"
	)
	_check(UI_THEME.prefs()["text_size"] == "Large", "Text size is saved")
	var reconnect: Button = page.get("_reconnect")
	_check(reconnect.visible, "Reconnect shows while not connected")
	var deadline := Time.get_ticks_msec() + 30000
	var history: Label = page.get("_history_size")
	while page.get("_history_job") != null and Time.get_ticks_msec() < deadline:
		await process_frame
	_check(not history.text.is_empty(), "History shows a size or Unavailable")
	_check(_accents(page) <= 1, "Settings show at most one filled accent button")
	var advanced: Control = page.find_child("Advanced", true, false)
	_check(advanced != null and not advanced.get("open"), "Advanced starts folded")
	for folded: String in ["Show reply times", "Open review details", "Reviewer agent"]:
		var row := _find_text(page, folded)
		_check(row != null and advanced.is_ancestor_of(row), folded + " waits under Advanced")
	var auto := _find_text(page, "Accept changes automatically")
	var agent_group: Control = page.find_child("AgentGroup", true, false)
	_check(
		auto != null and agent_group != null and agent_group.is_ancestor_of(auto),
		"Accept changes automatically sits in the Agent group"
	)
	_check(_find_text(page, "Keep versions") != null, "Older versions reads Keep versions")
	var art: Button = null
	for button: Node in page.find_children("*", "Button", true, false):
		if (button as Button).text == "The look":
			art = button
	_check(art != null, "The look row exists")
	if art != null:
		art.pressed.emit()
	_check((dock.get("ran") as Array).has("/art"), "Rows run their command through the dock")
	var switches := [0]
	dock.connect("connect_requested", func() -> void: switches[0] += 1)
	var agent_setting: Control = page.get("_agent")
	var agent_row: Control = agent_setting.get("row")
	var switch_agent: Button = agent_setting.get("switch_button")
	_check(agent_row.get("title").text == "No agent", "No agent is named yet")
	_check(
		switch_agent.text == "Connect" and switch_agent.theme_type_variation == "JoineryLinkButton",
		"Without an agent the row offers Connect, as a link"
	)
	dock.set("_agent_name", "Claude Code")
	page.set_open(false)
	page.set_open(true)
	_check(agent_row.get("title").text == "Claude Code", agent_row.get("title").text)
	_check(switch_agent.text == "Change ›", "The row offers Change: " + switch_agent.text)
	switch_agent.pressed.emit()
	_check(switches[0] == 1, "Switch opens the Connect screen through the dock")
	page.set_open(false)
	_check(header.visible and chat.visible and not page.visible, "Back restores the chat")
	_check(UI_THEME.set_pref("text_size", "Normal") == OK, "Restore the text size")
	dock.queue_free()
	await process_frame


## The skeleton, the blankslate, the palette strip and the reference grid.
func _check_blocks() -> void:
	var themed := VBoxContainer.new()
	themed.theme = UI_THEME.build(_host)
	themed.size = Vector2(480, 600)
	_host.add_child(themed)
	var lines := UI.skeleton(3)
	themed.add_child(lines)
	await process_frame
	var bars := lines.find_children("Bar", "Panel", true, false)
	_check(bars.size() == 3, "A skeleton has one bar per line")
	_check(
		(bars[0] as Control).custom_minimum_size.y > 0 and not "Checking" in _texts(lines),
		"Skeleton bars are text-shaped and say nothing"
	)
	_check(
		(bars[2] as Control).size.x < (bars[0] as Control).size.x,
		"The last line of a paragraph is shorter"
	)
	var value := UI.skeleton(1)
	themed.add_child(value)
	await process_frame
	var bar := value.find_children("Bar", "Panel", true, false)[0] as Control
	_check(
		is_equal_approx(bar.custom_minimum_size.x, UI_THEME.space(_host, 6) * 2.0),
		"A one-line skeleton is a value-sized bar"
	)
	var connect := UI.primary("Connect")
	var which := UI.quiet_link("Which one?")
	var blank := UI.blankslate("chats", "Connect an agent", connect, which)
	themed.add_child(blank)
	await process_frame
	_check(blank.find_child("Picture", true, false) != null, "A blankslate leads with a picture")
	_check(_texts(blank) == ["Connect an agent", "Connect", "Which one?"], str(_texts(blank)))
	_check(_accents(blank) == 1, "A blankslate has exactly one primary action")
	_check(connect.size_flags_horizontal == Control.SIZE_SHRINK_CENTER, "Its actions are centred")
	var bare := UI.blankslate("", "No look yet")
	themed.add_child(bare)
	_check(
		bare.find_child("Picture", true, false) == null and _accents(bare) == 0,
		"Picture and actions are optional"
	)
	var roles := [
		{"role": "Primary", "hex": "#7a4a2b", "use": "Wood"},
		{"role": "Accent", "hex": "#2b6a7a"},
		{"role": "Light", "hex": ""},
	]
	var strip := UI.palette_strip(roles, "md")
	themed.add_child(strip)
	await process_frame
	var swatches: Control = strip.get_node("Swatches")
	_check(
		is_equal_approx(swatches.custom_minimum_size.y, UI_THEME.swatch_h(_host, "md")),
		"Swatches are SWATCH_H tall"
	)
	_check(swatches.get("roles").size() == 3, "One swatch per role")
	var tip: String = swatches.call("_get_tooltip", Vector2(1, 1))
	_check(tip.contains("Primary") and tip.contains("#7a4a2b") and tip.contains("Wood"), tip)
	var empty_tip: String = swatches.call("_get_tooltip", Vector2(swatches.size.x - 1, 1))
	_check(empty_tip.contains("Not chosen"), "An empty slot says so: " + empty_tip)
	var picked: Array[int] = []
	swatches.connect("swatch_pressed", func(index: int) -> void: picked.append(index))
	var click := InputEventMouseButton.new()
	click.button_index = MOUSE_BUTTON_LEFT
	click.pressed = true
	click.position = Vector2(swatches.size.x / 2.0, 2)
	swatches.call("_gui_input", click)
	_check(picked == [1], "A click reports its swatch: %s" % [picked])
	_check(_texts(strip.get_node("Names")) == ["Primary", "Accent", "Light"], "Role names")
	var picture := Image.create(64, 48, false, Image.FORMAT_RGBA8)
	picture.fill(Color.CORAL)
	var path := OS.get_cache_dir().path_join("joinery-ui-theme-test/reference.png")
	picture.save_png(path)
	var refs := [
		{"title": "Brass handle", "image": path, "take": "worn brass", "not": "shiny gold"},
		{"title": "Monument Valley", "take": "chunky shapes"},
	]
	var grid := UI.reference_grid(refs, 3)
	themed.add_child(grid)
	await process_frame
	await process_frame
	var tiles := grid.get_children()
	_check(tiles.size() == 2 and grid.columns == 3, "One tile per reference, three columns")
	_check(tiles[0].find_child("Picture", true, false) != null, "A picture shows the image")
	_check(
		(
			tiles[1].find_child("StandIn", true, false) != null
			and tiles[1].find_child("Picture", true, false) == null
		),
		"A reference without a picture is a stand-in tile, not a word"
	)
	_check((tiles[0].get_node("Take") as Label).text == "worn brass", "The caption is the take")
	_check((tiles[0] as Control).tooltip_text.contains("Not: shiny gold"), "Not goes to the tip")
	var frame := tiles[0].get_node("Frame") as Control
	_check(absf(frame.size.y - frame.size.x * 0.75) <= 1.0, "Tiles are 4:3")
	var auto := UI.reference_grid(refs)
	themed.add_child(auto)
	auto.size = Vector2(320, 0)
	auto.resized.emit()
	_check(auto.columns == 2, "Narrow, two columns")
	auto.size = Vector2(UI_THEME.max_w(_host, "column"), 0)
	auto.resized.emit()
	_check(auto.columns == 4, "Wide, four columns")
	# The empty chat: no accent action while connected; the Connect blankslate has exactly one.
	var empty: Control = load("res://addons/joinery/sidebar_empty.gd").new()
	themed.add_child(empty)
	empty.call("set_connection", "connected")
	_check(_accents(empty) == 0, "The empty chat has no filled accent button")
	_check(
		_texts(empty.get("start")).has("Select something, then say what to change."),
		"One caption line, no Type / hint"
	)
	empty.call("set_context", [{"label": "Door", "kind": "Object", "node_type": "MeshInstance3D"}])
	var asks: Array[String] = []
	for chip: Button in empty.get("examples"):
		asks.append(chip.text)
	_check(asks.has("Make it taller") and asks.has("Change its material"), str(asks))
	_check(_texts(empty.get("start")).has("Door"), "The start names what is selected")
	empty.call("set_connection", "none")
	_check(_accents(empty) == 0, "Without an agent the agents are the choices, no extra button")
	_check((empty.get("agent_rows") as Array).size() == 4, "One row per agent")
	_check(not (empty.get("start") as Control).visible, "The examples step aside")
	var long_name := "door_with_long_descriptive_name.glb"
	var font := _host.get_theme_default_font()
	var short := BLOCKS.middle(long_name, font, 14, 120.0)
	_check(
		short.contains("…") and short.ends_with(".glb") and short.begins_with("door"),
		"Middle ellipsis keeps both ends: " + short
	)
	themed.queue_free()
	await process_frame


## The chat's centred column: at 340 and 480 px it fills the width; at 1000 it is MAX_W.column
## wide and centred.
func _check_column() -> void:
	var page := ScrollContainer.new()
	page.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_host.add_child(page)
	var body := VBoxContainer.new()
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	page.add_child(body)
	var row := Label.new()
	row.text = "Row"
	body.add_child(row)
	var column := STYLE.centered_column(page)
	_check(column != null and body.get_parent() == column, "The page's content moves in")
	_check(STYLE.centered_column(page) == column, "Asking again returns the same column")
	var cap := float(UI_THEME.max_w(_host, "column"))
	_check(UI_THEME.MAX_W == {"column": 640, "group": 560, "measure": 560}, "MAX_W token")
	_check(UI_THEME.SWATCH_H == {"sm": 24, "md": 56, "lg": 72}, "SWATCH_H token")
	for width: int in [340, 480, 1000]:
		page.size = Vector2(width, 400)
		await process_frame
		await process_frame
		var wide := minf(width, cap)
		_check(
			absf(body.size.x - wide) <= 1.0,
			"At %d px the column is %.0f wide, not %.0f" % [width, wide, body.size.x]
		)
		_check(
			absf(body.position.x - floorf((width - wide) / 2.0)) <= 1.0,
			"At %d px the column is centred (%.0f)" % [width, body.position.x]
		)
	var group := STYLE.centered_column(page, "group")
	await process_frame
	_check(group == column and absf(body.size.x - UI_THEME.max_w(_host, "group")) <= 1.0, "560")
	var holder := VBoxContainer.new()
	_host.add_child(holder)
	var composer := PanelContainer.new()
	holder.add_child(composer)
	var wrapped := STYLE.centered_column(composer)
	_check(composer.get_parent() == wrapped and wrapped.get_parent() == holder, "Wrapped in place")
	composer.hide()
	_check(not wrapped.visible, "A hidden row takes its column with it")
	page.queue_free()
	holder.queue_free()
	await process_frame


## Visible buttons filled with the accent (primary), under [param node].
func _accents(node: Node) -> int:
	var count := 0
	for child: Node in node.find_children("*", "Button", true, false):
		var button := child as Button
		if button.theme_type_variation == "JoineryPrimaryButton" and button.is_visible_in_tree():
			count += 1
	return count


## The first Label or Button under [param node] whose text is [param text], or null.
func _find_text(node: Node, text: String) -> Control:
	for child: Node in node.find_children("*", "", true, false):
		if (child is Label or child is Button) and child.get("text") == text:
			return child
	return null


func _texts(node: Node) -> Array[String]:
	var result: Array[String] = []
	for child: Node in node.find_children("*", "", true, false):
		if child is Label:
			result.append((child as Label).text)
		elif child is Button:
			result.append((child as Button).text)
	return result
