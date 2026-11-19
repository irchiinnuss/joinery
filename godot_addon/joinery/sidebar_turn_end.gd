@tool
extends RefCounted
## The end of Journeyman's turn as the chat host tells it: its done, then its timing line just
## behind. What follows the turn (the next queued message, a request the art gate held) waits for
## that timing line, so the line lands on the turn it belongs to, never on the next request. The
## two may arrive in different reads of the pipe. A turn whose timing never comes lets the next
## one go after [constant WAIT] seconds.

const WAIT := 1.0
var _then: Array[Callable] = []
var _serial := 0


## The turn ended: run [param then] once its timing line came ([method timed]), or after
## [constant WAIT] seconds on [param tree]; at once without a tree.
func ended(tree: SceneTree, then: Array[Callable]) -> void:
	_serial += 1
	var serial := _serial
	_then = then
	if tree == null:
		timed()
		return
	tree.create_timer(WAIT).timeout.connect(
		func() -> void:
			if serial == _serial:
				timed()
	)


## True while what follows the turn waits for its timing line: the turn isn't over yet.
func waiting() -> bool:
	return not _then.is_empty()


## The timing line came (or the wait ran out): what follows the turn goes now, once.
func timed() -> void:
	var then := _then
	_then = []
	for each: Callable in then:
		if each.is_valid():
			each.call()
