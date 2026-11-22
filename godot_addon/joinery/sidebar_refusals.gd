@tool
extends VBoxContainer
## The calls the project's gate refused on a request's card (sidebar_change_card.gd): one quiet
## line per reason, behind the stop mark ("Not run: Krita isn't enabled for this project. Turn
## it on in Project apps."). It stays in sight when the card's steps fold; the same reason is
## said once, while every refused call counts for the card's timing line.

const UI_MOTION := preload("ui_motion.gd")
const UI := preload("ui_components.gd")
const STYLE := preload("sidebar_style.gd")

## Words of the agent's own "needs approval" refusal (bridge/trail.py NEEDS_APPROVAL).
const NEEDS_APPROVAL := [
	"no approval surface", "needs a permission prompt, which this session can't show"
]

## The refused calls, by call id and reason.
var _calls: Dictionary = {}
var _lines: Array[String] = []


func _init() -> void:
	name = "Refusals"
	theme_type_variation = "JoineryTight"
	accessibility_name = "Steps not run"
	hide()


## A refused call ({call_id, reason}) adds its line, unless its reason was said already.
func add(event: Dictionary) -> void:
	var reason := str(event.get("reason", "")).strip_edges()
	_calls[str(event.get("call_id", "")) + "|" + reason] = true
	var line := "Not run: " + reason if not reason.is_empty() else "A step was not run"
	if line in _lines:
		return
	_lines.append(line)
	var row := HBoxContainer.new()
	row.theme_type_variation = "JoineryRowTight"
	row.name = "Refused"
	row.accessibility_name = line
	var mark := STYLE.glyph("text_muted")
	STYLE.set_glyph(mark, "stop", "text_muted")
	mark.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	row.add_child(mark)
	row.add_child(UI.label(line, "JoineryCaption", true))
	add_child(row)
	show()
	UI_MOTION.fade_in(row)


## How many calls were refused.
func count() -> int:
	return _calls.size()


## True for Claude Code's own refusal of a call that needed approval nobody can give in a Joinery
## session ({server: "claude", reason}), saved as a refusal by an older Joinery or guessed by the
## host. It is no judgment of the project's gate, so it reads as a skipped step, not a refusal.
## Joinery's own refusals ("Blocked by Joinery policy", Rule Zero stops) never match.
static func needs_approval(event: Dictionary) -> bool:
	var reason := str(event.get("reason", "")).to_lower()
	if str(event.get("server", "")) != "claude" or reason.contains("blocked by joinery"):
		return false
	return NEEDS_APPROVAL.any(func(words: String) -> bool: return reason.contains(words))


## The trail event for that skipped call ({tool, call_id}): its line ends quietly, saying so.
static func skipped_step(event: Dictionary) -> Dictionary:
	var tool := str(event.get("tool", ""))
	var what := (
		"command" if tool == "Bash" else ("search" if tool in ["Grep", "Glob", "LS"] else "step")
	)
	return {
		"kind": "trail",
		"id": str(event.get("call_id", "")),
		"text": "Skipped a %s that needed approval" % what,
		"state": "skipped",
	}
