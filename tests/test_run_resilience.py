"""`joinery run` over long requests: an idle timeout, work kept when a run stops or
crashes, continuing a chat, and a to-do's Go when nobody can press it."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from joinery import headless
from joinery.cli import main as cli_main
from joinery.gateway import ProjectGateway
from joinery.transactions import FileTransaction

#: A host that starts working, then goes silent until its input closes; it then
#: keeps the change it had applied and closes.
SILENT_HOST = """
import json, sys
def say(event):
    print(json.dumps(event), flush=True)
say({"kind": "connection", "state": "connected"})
got = [json.loads(sys.stdin.readline())]
say({"kind": "chat", "chat_id": "c42", "mode": "new"})
say({"kind": "turn_state", "state": "working"})
rest = sys.stdin.readline()  # nothing more until the run gives up and closes it
say({"kind": "kept", "transaction_id": "tx9", "summary": "Half a door",
     "note": "The run stopped before this change was finished."})
with open(sys.argv[1], "w") as out:
    json.dump(got, out)
"""

#: A host that proposes a to-do waiting for Go, then works once Go comes.
TODO_HOST = """
import json, sys
def say(event):
    print(json.dumps(event), flush=True)
def hear():
    return json.loads(sys.stdin.readline())
got = []
say({"kind": "connection", "state": "connected"})
got.append(hear())
say({"kind": "todo", "todo_id": "t1", "steps": ["a", "b"], "state": "waiting"})
say({"kind": "timing", "outcome": "done"})
got.append(hear())
if got[-1]["kind"] == "todo_go":
    say({"kind": "file_preview", "transaction_id": "tx1", "summary": "Both"})
    say({"kind": "timing", "outcome": "done"})
    got.append(hear())
say({"kind": "quiet"})
with open(sys.argv[1], "w") as out:
    json.dump(got, out)
sys.stdin.readline()
"""

#: A host that stages a change, then dies mid-turn.
CRASH_HOST = """
import json, sys
from pathlib import Path
from joinery.transactions import FileTransaction
print(json.dumps({"kind": "connection", "state": "connected"}), flush=True)
sys.stdin.readline()
tx = FileTransaction(Path(sys.argv[1]).parent)
tx.begin("Door")
(tx.stage / "door.gd").write_text("extends Node3D\\n")
sys.exit(3)
"""


def fake(tmp_path: Path, source: str) -> tuple[Any, Path]:
    script = tmp_path / "host.py"
    script.write_text(source)
    seen = tmp_path / "seen.json"
    return (lambda _p, _pin: [sys.executable, str(script), str(seen)]), seen


def test_a_silent_run_stops_on_the_idle_timeout_and_keeps_its_work(
    tmp_path: Path,
) -> None:
    command, seen = fake(tmp_path, SILENT_HOST)
    events: list[dict[str, Any]] = []
    outcome = asyncio.run(
        headless.run_request(
            tmp_path,
            "Make a door",
            headless.Pin("claude"),
            on_event=events.append,
            timeout=1.5,
            command=command,
        )
    )
    assert outcome.state == "review" and outcome.transaction_id == "tx9"
    assert outcome.chat_id == "c42"
    assert "no progress for 1.5 s" in outcome.note
    assert "waits for your review" in outcome.note
    assert outcome.error.startswith("Stopped:")
    assert any(e["kind"] == "kept" for e in events)
    assert json.loads(seen.read_text())[0]["kind"] == "prompt"


def test_a_run_past_its_cap_is_stopped_even_while_busy(tmp_path: Path) -> None:
    command, _seen = fake(tmp_path, SILENT_HOST)
    outcome = asyncio.run(
        headless.run_request(
            tmp_path,
            "Make a door",
            headless.Pin("claude"),
            timeout=60,
            max_time=1.0,
            command=command,
        )
    )
    assert "--max-time 1" in outcome.note and outcome.state == "review"


def test_a_crashed_host_leaves_its_stage_kept_for_review(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "door.gd").write_text("extends Node\n")
    command, _seen = fake(tmp_path, CRASH_HOST)
    outcome = asyncio.run(
        headless.run_request(
            tmp_path, "Door", headless.Pin("claude"), timeout=30, command=command
        )
    )
    assert outcome.state == "review", outcome
    tx = FileTransaction.load(tmp_path, outcome.transaction_id)
    assert tx.state == "review" and tx.held
    assert "stopped unexpectedly" in tx.review_notes[-1]["text"]
    assert "stopped unexpectedly" in outcome.note


def test_a_leftover_asset_without_provenance_is_never_kept(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    tx = FileTransaction(tmp_path)
    tx.begin("Crate")
    (tx.stage / "art").mkdir()
    (tx.stage / "art" / "crate.glb").write_bytes(b"glTF")
    assert headless.keep_leftovers(tmp_path, 0.0, "a crash") == []
    assert FileTransaction.load(tmp_path, tx.id).state == "staging"


def test_the_gateway_keeps_an_unfinished_change_only_in_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))

    def staging() -> FileTransaction:
        tx = FileTransaction(tmp_path)
        tx.begin("Door")
        (tx.stage / "door.gd").write_text("extends Node3D\n")
        return tx

    async def go() -> None:
        kept: list[dict[str, Any]] = []
        gateway = ProjectGateway(tmp_path)
        gateway.transaction = first = staging()
        assert gateway.keep_unfinished("why") == ""  # the editor's chat: never
        gateway.keeps_unfinished = gateway.keep_reviews = True
        gateway.on_kept = kept.append
        assert gateway.keep_unfinished("It timed out.") == first.id
        loaded = FileTransaction.load(tmp_path, first.id)
        assert loaded.state == "review" and loaded.held
        assert loaded.review_notes[-1]["text"] == "It timed out."
        assert kept[0]["kind"] == "kept" and kept[0]["transaction_id"] == first.id
        # Closing mid-change keeps it too, with the standard note.
        gateway.transaction = second = staging()
        await gateway.close()
        assert FileTransaction.load(tmp_path, second.id).held

    asyncio.run(go())


def test_a_policy_file_change_is_not_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    gateway = ProjectGateway(tmp_path)
    gateway.keeps_unfinished = True
    tx = FileTransaction(tmp_path)
    tx.begin("Policy")
    (tx.stage / "joinery.toml").write_text("[agents]\n")
    gateway.transaction = tx
    assert gateway.keep_unfinished("why") == ""
    asyncio.run(gateway.close())
    assert FileTransaction.load(tmp_path, tx.id).state == "rejected"


def test_go_ahead_with_a_waiting_todo_when_nobody_can_say_go(tmp_path: Path) -> None:
    command, seen = fake(tmp_path, TODO_HOST)
    outcome = asyncio.run(
        headless.run_request(
            tmp_path, "Two things", headless.Pin("codex"), command=command, go="auto"
        )
    )
    got = json.loads(seen.read_text())
    assert got[1] == {"kind": "todo_go", "todo_id": "t1"}
    assert outcome.state == "review" and outcome.todo_waiting == ""


def test_never_leaves_the_todo_waiting_and_says_so(
    tmp_path: Path,
) -> None:
    command, seen = fake(tmp_path, TODO_HOST)
    outcome = asyncio.run(
        headless.run_request(
            tmp_path, "Two things", headless.Pin("codex"), command=command, go="never"
        )
    )
    assert json.loads(seen.read_text())[1] == {"kind": "when_quiet"}
    assert outcome.todo_waiting == "t1" and outcome.state == "none"


def test_go_policy_asks_only_on_a_terminal() -> None:
    assert headless.go_policy("", interactive=False) == "auto"
    assert headless.go_policy("", interactive=True) is headless.ask_go
    assert headless.go_policy("ask", interactive=False) == "never"
    assert headless.go_policy("never", interactive=True) == "never"


def test_chat_continues_by_id_or_last(tmp_path: Path) -> None:
    from joinery import chats

    first = chats.create(tmp_path, "Door", "claude")
    assert headless.chat_to_continue(tmp_path, "last") == first
    assert headless.chat_to_continue(tmp_path, first) == first
    assert headless.chat_to_continue(tmp_path, "") == ""
    with pytest.raises(ValueError, match="No chat"):
        headless.chat_to_continue(tmp_path, "nope")
    message = headless.prompt_message("More", headless.Pin("claude"), first)
    assert message["chat_id"] == first


def test_the_cli_prints_the_chat_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    command, _seen = fake(tmp_path, SILENT_HOST)
    monkeypatch.setattr(headless, "chat_command", command)
    monkeypatch.setattr(headless, "CLOSE_GRACE", 5.0)
    args = ["run", "--project", str(tmp_path), "--request", "Door", "--agent"]
    assert cli_main([*args, "claude", "--timeout", "1"]) == 0
    out = capsys.readouterr().out
    assert "Chat c42" in out and "--chat c42" in out
    assert "waits for your review" in out
