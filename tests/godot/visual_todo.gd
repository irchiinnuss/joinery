extends RefCounted
## The visual suite's to-do screens (ADR 0060): a request's steps and "done when" items waiting
## for Go, editable; the list being worked through (some steps done, one in progress), one
## step opened to its detail, then the items checked, then the card minimised to its one line
## with its live light while Journeyman works. [param suite] is visual_sidebar_test.gd, whose
## `_shot` these use; the sidebar's review shows the same results (visual_review.gd). The work
## trail's screens follow (visual_trail.gd).

const TRAIL := preload("visual_trail.gd")
const STEPS := [
	"Mower body and deck",
	"Mower: pull-cord handle and fuel cap",
	"Grass box on the back",
	"Rake with a worn handle",
	"Shed shelf for the tools",
	"Place the mower by the shed door",
	"Put the rake on the shelf",
	"Hook the mower up to the yard scene",
]
const DETAILS := [
	"art/props/mower.blend: a 60 × 40 cm deck on four 18 cm wheels, red paint, chipped.",
	(
		"In mower.blend: a 12 cm T-handle on a 60 cm cord to the engine's top, a 6 cm"
		+ " screw cap on the tank's left side. Bones named PullCord and FuelCap."
	),
	"40 × 30 × 25 cm, clipped to the deck's back edge; open top.",
	"art/props/rake.blend, 1.5 m handle, tape wrapped near the grip.",
	"A pine shelf, 1.2 m wide, 1.4 m up the shed's back wall.",
	"res://scenes/yard.tscn, Shed/Door: 1 m in front, facing the gate.",
	"",
	"Instance both props in yard.tscn under Props; collisions from their low-poly hulls.",
]
const DONE := [
	{"id": "d1", "text": "The player can reach the shed", "kind": "reach"},
	{"id": "d2", "text": "No errors in 60 s of play", "kind": "no_errors"},
	{"id": "d3", "text": "The mower reads as worn and red", "kind": "look"},
]


## Results as the host sends them: one of each.
static func results() -> Array:
	return [
		{
			"id": "d1",
			"text": "The player can reach the shed",
			"status": "pass",
			"evidence": "The bot reached Shed at 0:42, seed 4242.",
		},
		{
			"id": "d2",
			"text": "No errors in 60 s of play",
			"status": "inconclusive",
			"evidence": "The game ran only 0:41 of the 60 s asked for.",
		},
		{
			"id": "d3",
			"text": "The mower reads as worn and red",
			"status": "inconclusive",
			"evidence": "Nothing checks this by itself; look for yourself.",
		},
	]


static func shots(suite: Variant, light: bool, width: int, chat: Control) -> void:
	var tasks: Node = chat.get("_tasks")
	var todo: Node = tasks.get("todo")
	var event := {
		"kind": "todo",
		"todo_id": "t1",
		"state": "waiting",
		"steps": STEPS,
		"details": DETAILS,
		"done_when": DONE,
	}
	todo.call("on_event", event)
	var card: Control = tasks.get("todo_card")
	# Waiting for Go: every line edits in place; the second step is open to its detail.
	((card.call("step_rows") as Array)[1] as Control).call("set_open", true)
	await suite._shot("todo", light, width, chat)
	# Working: two steps done, the third in progress, the rest still to do.
	event["state"] = "going"
	todo.call("on_event", event)
	var marks := ["done", "done", "started", "", "", "", "", ""]
	todo.call("on_event", {"kind": "todo", "todo_id": "t1", "step": 3, "step_states": marks})
	await suite._shot("todo-working", light, width, chat)
	# A step opened to its detail while it is worked on.
	((card.call("step_rows") as Array)[1] as Control).call("set_open", true)
	await suite._shot("todo-detail", light, width, chat)
	# Minimised to its one line while Journeyman works: the live light.
	card.call("set_minimised", true)
	await suite._shot("todo-minimised", light, width, chat)
	card.call("set_minimised", false)
	(
		todo
		. call(
			"on_event",
			{
				"kind": "todo",
				"todo_id": "t1",
				"state": "checked",
				"step_states": ["done", "done", "done", "done", "done", "done", "skipped", "done"],
				"results": results(),
				"summary": "1 of 3 done",
			}
		)
	)
	await suite._shot("todo-checked", light, width, chat)
	card.free()
	tasks.set("todo_card", null)
	tasks.hide()
	await TRAIL.shots(suite, light, width, chat)
