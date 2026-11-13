@tool
extends VBoxContainer
## One settings group (Apple's inset grouped list): a quiet caption title (JoineryCaption,
## sentence case) SPACE.2 above one card (surface_raised, RADIUS.lg) that holds its rows. Rows
## are CONTROL_H.md tall, label left and value right on one line; a hairline (border_subtle)
## parts them, inset SPACE.4 like their text. Anything marked attached
## ([method attach]: a caption, an inline question) belongs to the row above it and gets no
## hairline. Add rows to [member body], as with ui_components' Section.

const UI_THEME := preload("ui_theme.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")
const ATTACHED := "joinery_attached"

var header: Label
var card: PanelContainer
var body: VBoxContainer


func _init(title: String) -> void:
	name = title.capitalize().replace(" ", "") + "Group"
	theme_type_variation = "JoineryRow"
	header = UI.label(title, "JoineryCaption")
	header.name = "Title"
	add_child(header)
	card = PanelContainer.new()
	card.name = "Card"
	add_child(card)
	body = VBoxContainer.new()
	body.name = "Rows"
	body.theme_type_variation = "JoineryFlush"
	card.add_child(body)
	# Rows inside the card start and end on its padding: no second inset.
	var local := Theme.new()
	for inset: String in ["JoineryInset", "JoineryInsetTall"]:
		local.set_type_variation(inset, "MarginContainer")
		local.set_constant("margin_left", inset, 0)
		local.set_constant("margin_right", inset, 0)
	body.theme = local
	body.sort_children.connect(card.queue_redraw)
	card.draw.connect(_draw_hairlines)
	STYLE.on_theme_overrides(card, _restyle)


## Mark [param control] as part of the row above it (no hairline between them).
static func attach(control: Control) -> Control:
	control.set_meta(ATTACHED, true)
	return control


func _restyle() -> void:
	var source := card.get_theme_stylebox("panel", "JoineryChangeCard")
	if not source is StyleBoxFlat:
		return
	var box := source.duplicate() as StyleBoxFlat
	box.set_corner_radius_all(UI_THEME.radius(card, "lg"))
	box.content_margin_left = UI_THEME.space(card, 4)
	box.content_margin_right = UI_THEME.space(card, 4)
	box.content_margin_top = UI_THEME.space(card, 1)
	box.content_margin_bottom = UI_THEME.space(card, 1)
	card.add_theme_stylebox_override("panel", box)


func _draw_hairlines() -> void:
	var ink := card.get_theme_color("border_subtle", "Joinery")
	var width := maxf(1.0, roundf(UI_THEME.unit(card)))
	var first := true
	for child: Node in body.get_children():
		var row := child as Control
		# Hidden rows, and rows with nothing to show yet (no height), get no hairline.
		if row == null or not row.visible or row.size.y < 1.0:
			continue
		if not first and not row.has_meta(ATTACHED):
			var y := body.position.y + row.position.y
			card.draw_line(
				Vector2(body.position.x, y), Vector2(body.position.x + body.size.x, y), ink, width
			)
		first = false
