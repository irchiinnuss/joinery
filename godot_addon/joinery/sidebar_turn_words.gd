@tool
extends RefCounted
## How the sidebar words the end of a turn: the timing line on the request's card ("Completed in
## 0:12", chat_history.gd; "Done in 0:12, 1 step not run" when a call was refused,
## sidebar_change_card.gd), a reply cut off by Stop, and the kind of a decision message
## (journeyman_dock.gd).

const UI_MOTION := preload("ui_motion.gd")


## The turn's timing line for [param outcome] ("done", "cancelled", anything else failed) after
## [param seconds]. A turn shorter than a second says so in words, never "0:00".
static func timing_line(outcome: String, seconds: float) -> String:
	var quick := seconds < 1.0
	var clock := UI_MOTION.format_clock(seconds)
	if outcome == "done":
		return "Completed in under 1 s" if quick else "Completed in " + clock
	if outcome == "cancelled":
		return "Stopped within 1 s" if quick else "Stopped after " + clock
	return "Failed within 1 s" if quick else "Failed after " + clock


## A reply [param text] that Stop cut off, ended cleanly: a sentence that ended stays as it is;
## otherwise the last, perhaps half, word goes and "…" says it stopped there. Inside an open
## code block nothing is cut.
static func cut_off(text: String) -> String:
	var kept := text.strip_edges(false, true)
	if kept.is_empty() or kept.count("```") % 2 == 1:
		return kept
	if kept[-1] in [".", "!", "?", "…", ":", ")", '"', "'", "`", "*", "_"]:
		return kept
	var space := maxi(kept.rfind(" "), kept.rfind("\n"))
	if space > 0:
		kept = kept.left(space).strip_edges(false, true)
	while not kept.is_empty() and kept[-1] in [",", ";", "-", "–", "—"]:
		kept = kept.left(-1).strip_edges(false, true)
	return kept + "…"


## [param seconds] as a clock ("0:12"), or "under 1 s" for less than a second, never "0:00".
static func duration(seconds: float) -> String:
	return "under 1 s" if seconds < 1.0 else UI_MOTION.format_clock(seconds)


## [param line] (a done turn's timing line) for a turn in which [param count] calls were
## refused: "Done in 0:12, 1 step not run". A line already counted is counted again.
static func with_refusals(line: String, count: int) -> String:
	var base := line.left(line.rfind(", ")) if line.ends_with(" not run") else line
	var plural := "" if count == 1 else "s"
	return "%s, %d step%s not run" % [base.replace("Completed in ", "Done in "), count, plural]


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
