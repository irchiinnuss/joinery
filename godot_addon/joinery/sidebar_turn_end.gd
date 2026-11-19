@tool
extends RefCounted
## The end of Journeyman's turn as the chat host tells it: its done and its timing line, usually
## in that order and sometimes in different reads of the pipe. The next queued message (and a
## request the art gate held) waits for that timing line, so the line lands on the turn it
## belongs to, never on the next request. A turn whose timing came first, or never comes, lets
## the next one go at once, or after [constant WAIT] seconds.

const WAIT := 1.0
var _then: Array[Callable] = []
var _serial := 0
## The timing line of the turn now running came already.
var _timed := false


## A turn started: its timing line is still to come.
func started() -> void:
	_timed = false


## The turn ended: run [param then] once its timing line came ([method timed]), at once when it
## came already or without a [param tree], else after [constant WAIT] seconds at the latest.
func ended(tree: SceneTree, then: Array[Callable]) -> void:
	_serial += 1
	var serial := _serial
	_then = then
	if _timed or tree == null:
		_run()
		return
	tree.create_timer(WAIT).timeout.connect(
		func() -> void:
			if serial == _serial:
				_run()
	)


## True while what follows the turn waits for its timing line.
func waiting() -> bool:
	return not _then.is_empty()


## The timing line came: what follows the turn goes now.
func timed() -> void:
	_timed = true
	_run()


func _run() -> void:
	var then := _then
	_then = []
	for each: Callable in then:
		if each.is_valid():
			each.call()
