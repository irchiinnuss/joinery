@tool
extends RefCounted
## How the sidebar words the end of a turn: the timing line on the request's card ("Completed in
## 0:12", chat_history.gd) and the kind of a decision message (journeyman_dock.gd).

const UI_MOTION := preload("ui_motion.gd")


## The turn's timing line for [param outcome] ("done", "cancelled", anything else failed) after
## [param seconds]. A turn shorter than a second says so in words, never "0:00".
static func timing_line(outcome: String, seconds: float) -> String:
	var quick := seconds < 1.0
	var clock := UI_MOTION.format_clock(seconds)
	if outcome == "done":
		return "Completed in under a second" if quick else "Completed in " + clock
	if outcome == "cancelled":
		return "Stopped within a second" if quick else "Stopped after " + clock
	return "Failed within a second" if quick else "Failed after " + clock


## Kind of a decision message ("success", "rejected", "info" or what [param kind] says); older
## callers pass only the message.
static func decision_kind(message: String, kind: String) -> String:
	if kind != "info":
		return kind
	var lower := message.to_lower()
	if lower.contains("accepted"):
		return "success"
	if lower.contains("rejected") or lower.contains("discarded"):
		return "rejected"
	return "info"
