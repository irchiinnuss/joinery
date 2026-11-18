@tool
extends RefCounted
## How the sidebar words the end of a turn (journeyman_dock.gd): the kind of a decision message.


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
