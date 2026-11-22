extends SceneTree
## A call Claude Code refused on its own because it needed approval nobody can give in a Joinery
## session ("Permission for this tool use was denied ... no approval surface") is a quiet skipped
## step on the trail, live and in a reopened chat: never a refusal row, and the card still reads
## done. Joinery's own refusals keep their row and stop mark exactly as before.

const REFUSALS := preload("res://addons/joinery/sidebar_refusals.gd")
const DENIED := (
	"Permission for this tool use was denied. It requires approval, and this session has no "
	+ "approval surface - nobody can answer a permission prompt here"
)
const POLICY := (
	"PreToolUse hook error: Blocked by Joinery policy: godot. is not a read-only tool; "
	+ "no approval surface either"
)

var _failed := false


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		_failed = true


func _card(chat: Control) -> Control:
	return chat.get("_transcript").call("current_card")


func _run() -> void:
	load("res://addons/joinery/ui_motion.gd").call("set_enabled", false)
	var bash := {"server": "claude", "tool": "Bash", "reason": DENIED, "call_id": "a"}
	_check(REFUSALS.needs_approval(bash), "Claude Code's own approval refusal is a skip")
	_check(
		not REFUSALS.needs_approval(bash.merged({"reason": POLICY}, true)),
		"Joinery's policy judgment never is, whatever else it says"
	)
	_check(
		not REFUSALS.needs_approval(bash.merged({"server": "krita"}, true)),
		"Only Claude Code's own refusals"
	)
	_check(
		REFUSALS.skipped_step(bash)["text"] == "Skipped a command that needed approval",
		"A shell command reads as a command"
	)
	_check(
		REFUSALS.skipped_step({"tool": "Grep"})["text"] == "Skipped a search that needed approval",
		"A search reads as a search"
	)

	var chat: Control = load("res://addons/joinery/journeyman_dock.gd").new()
	root.size = Vector2i(480, 900)
	root.add_child(chat)
	chat.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	var backend: Node = load("res://addons/joinery/chat_bridge.gd").new()
	chat.add_child(backend)
	chat.connect("prompt_submitted", func(_text: String, _targets: Array) -> void: pass)
	chat.set_chat_state("connected", "")
	chat.call("_on_submit", "Paint the soil")
	chat.set_chat_busy(true)
	chat.call("_on_sent")
	var card := _card(chat)
	var trail: Node = card.get("_steps")
	var refusals: Node = card.get("_refusals")

	# Live: the host's skipped line ends the running call quietly.
	backend.call(
		"_handle",
		{"kind": "trail", "id": "a", "text": "Running grep on recipes", "state": "running"}
	)
	backend.call("_handle", REFUSALS.skipped_step(bash).merged({"kind": "trail"}))
	# An older host's refusal event for the same kind of call: the bridge turns it into a step.
	backend.call(
		"_handle", {"kind": "trail", "id": "b", "text": "Searching the code", "state": "running"}
	)
	backend.call(
		"_handle",
		{
			"kind": "tool_activity",
			"server": "claude",
			"tool": "Grep",
			"allowed": false,
			"reason": DENIED,
			"call_id": "b"
		}
	)
	await process_frame
	trail.call("show_all", true)
	var texts: PackedStringArray = trail.call("texts")
	_check(
		texts.has("Skipped a command that needed approval"),
		"The command's line says it was skipped: %s" % [texts]
	)
	_check(texts.has("Skipped a search that needed approval"), "And the search's: %s" % [texts])
	_check(int(refusals.call("count")) == 0, "Neither is a refusal")
	_check(not (refusals as Control).visible, "No refusal row shows")
	var marks := 0
	# The finished lines, not the live line's copy of the newest.
	var history: Node = trail.get("_history")
	for words: Node in history.find_children("Words", "Label", true, false):
		if (words as Label).text.begins_with("Skipped"):
			marks += 1
			_check(
				(words as Label).theme_type_variation == "JoineryFaint",
				"A skipped step reads faint"
			)
	_check(marks == 2, "One quiet line per skipped call: %d" % marks)

	# Joinery's own refusal keeps its row and counts, exactly as before.
	backend.call(
		"_handle", {"kind": "trail", "id": "c", "text": "Opening sign.kra", "state": "running"}
	)
	backend.call(
		"_handle",
		{
			"kind": "tool_activity",
			"server": "claude",
			"tool": "mcp__joinery-registry__read_tool",
			"allowed": false,
			"reason": POLICY,
			"call_id": "c"
		}
	)
	await process_frame
	_check(int(refusals.call("count")) == 1, "Joinery's refusal still counts")
	_check((refusals as Control).visible, "And its row shows")
	chat.set_chat_busy(false)

	# Reopened: a saved skipped step, and a refusal row an older Joinery saved for one.
	var sessions: Node = chat.get("_chats")
	(
		sessions
		. call(
			"render",
			[
				{"type": "user", "text": "Paint the soil"},
				{"type": "step", "text": "Running grep on recipes"},
				{"type": "step", "text": "Skipped a command that needed approval", "skipped": true},
				{"type": "refusal", "server": "claude", "tool": "Grep", "reason": DENIED},
				{"type": "turn", "outcome": "done", "total_ms": 4000},
			]
		)
	)
	await process_frame
	var reopened := _card(chat)
	var steps: Node = reopened.get("_steps")
	steps.call("show_all", true)
	var again: PackedStringArray = steps.call("texts")
	_check(
		again.has("Skipped a command that needed approval"),
		"The saved skip is back as a step: %s" % [again]
	)
	_check(
		again.has("Skipped a search that needed approval"),
		"The older refusal row reads as a skip too: %s" % [again]
	)
	_check(int(reopened.get("_refusals").call("count")) == 0, "Reopened, no refusal either")

	chat.queue_free()
	await process_frame
	print("skipped steps: ", "FAILED" if _failed else "ok")
	quit(1 if _failed else 0)
