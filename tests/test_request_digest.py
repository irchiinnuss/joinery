"""The digest a request's fresh session starts from: brief, chat, review, stall."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from bridge.chat_record import ChatRecorder
from joinery import chats, request_digest
from joinery.transactions import FileTransaction

BRIEF = """# Ironworks
<!-- notes -->

A fast first-person shooter in a burning foundry: find the keycards, reach the
exit switch, keep moving.

## Pillars
- Speed
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "design").mkdir()
    (tmp_path / "design/brief.md").write_text(BRIEF)
    return tmp_path


def waiting(project: Path, summary: str, files: dict[str, str]) -> FileTransaction:
    tx = FileTransaction(project)
    tx.begin(summary)
    for path, text in files.items():
        target = tx.stage / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    tx.review()
    return tx


def test_the_brief_is_one_line(project: Path) -> None:
    line = request_digest.brief_line(project)
    assert line.startswith("The game (design/brief.md): Ironworks. A fast first-person")
    assert "Pillars" not in line and "\n" not in line


def test_a_new_request_hears_the_game_and_whats_waiting(project: Path) -> None:
    tx = waiting(
        project,
        "Casting Hall blockout",
        {f"game/room_{n}.tscn": "[gd_scene]\n" for n in range(6)},
    )
    digest = request_digest.build(project)
    assert digest.startswith(request_digest.HEAD)
    assert "Ironworks" in digest
    assert f"Change {tx.id[:8]} (Casting Hall blockout): 6 files" in digest
    assert "and 2 more." in digest


def test_a_chat_gives_its_last_turns_short(project: Path) -> None:
    chat_id = chats.create(project, "Weapons", "claude")
    for n in range(5):
        chats.append(project, chat_id, {"type": "user", "text": f"Request {n} " * 80})
        chats.append(project, chat_id, {"type": "agent", "text": f"Reply {n} " * 120})
        chats.append(project, chat_id, {"type": "turn", "outcome": "done"})
    meta, entries = chats.read(project, chat_id)
    digest = request_digest.build(project, chat=(meta, entries))
    assert 'This chat ("Weapons"), its last 3 of 5 turns:' in digest
    assert "Request 1" not in digest and "Request 4" in digest
    assert len(digest) <= request_digest.DIGEST_CHARS
    assert "stopped before it finished" not in digest


def test_a_stalled_turn_is_carried_on_not_replayed(project: Path) -> None:
    chat_id = chats.create(project, "Keycards", "claude")
    chats.append(project, chat_id, {"type": "user", "text": "Keycards and doors"})
    chats.append(project, chat_id, {"type": "turn", "outcome": "stopped"})
    meta, entries = chats.read(project, chat_id)
    digest = request_digest.build(project, chat=(meta, entries))
    assert "The last turn stopped before it finished (stopped)" in digest
    assert "don't make it again" in digest


def test_changes_since_the_last_turn_are_listed(project: Path) -> None:
    before = time.time() - 1
    (project / "notes.txt").write_text("edited by hand\n")
    digest = request_digest.build(project, since=before)
    assert "Since the chat's last turn:" in digest
    assert "notes.txt" in digest


def test_it_fits_its_limit(project: Path) -> None:
    for n in range(8):
        waiting(project, f"Change number {n} " * 10, {f"a{n}.gd": "extends Node\n"})
    digest = request_digest.build(project, limit=400)
    assert len(digest) <= 400 or digest.count("\n") <= 2


def test_nothing_known_gives_nothing(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    assert request_digest.build(tmp_path) == ""


# --- When a request starts fresh (bridge.chat_record) ----------------------------------


class FakeSessions:
    def __init__(self) -> None:
        self.handle = "h1"
        self.resumed: list[str] = []
        self.new = 0

    async def resume(self, handle: str) -> bool:
        self.resumed.append(handle)
        return True

    async def new_session(self) -> None:
        self.new += 1


def one_turn(
    recorder: ChatRecorder, sessions: FakeSessions, text: str, outcome: str = "done"
) -> str:
    sent, _ = recorder.begin({"text": text}, ())
    recorder.observe({"kind": "message", "text": "Made it."})
    recorder.observe({"kind": "timing", "outcome": outcome, "total_ms": 5})
    recorder.observe({"kind": "done"})
    recorder.finish(sessions, 120_000)
    return sent


def test_a_new_chat_starts_from_the_digest(project: Path) -> None:
    recorder, sessions = ChatRecorder(project, "claude"), FakeSessions()
    assert asyncio.run(recorder.switch("", sessions)) is None
    sent = one_turn(recorder, sessions, "Add a crate")
    assert sent.startswith(request_digest.HEAD) and sent.endswith("Add a crate")
    meta, _ = chats.read(project, recorder.chat_id)
    assert meta["context"] == "120000"


def test_a_big_session_starts_fresh_for_the_next_request(project: Path) -> None:
    recorder, sessions = ChatRecorder(project, "claude"), FakeSessions()
    one_turn(recorder, sessions, "Add a crate")
    chat_id = recorder.chat_id
    event = asyncio.run(recorder.switch(chat_id, sessions, context=120_000))
    assert event == {"kind": "chat", "chat_id": chat_id, "mode": "fresh", "message": ""}
    assert sessions.new == 1
    sent = one_turn(recorder, sessions, "Now a barrel")
    assert "The user asked: Add a crate" in sent and sent.endswith("Now a barrel")


def test_a_small_session_or_a_follow_up_goes_on(project: Path) -> None:
    recorder, sessions = ChatRecorder(project, "claude"), FakeSessions()
    one_turn(recorder, sessions, "Add a crate")
    chat_id = recorder.chat_id
    assert asyncio.run(recorder.switch(chat_id, sessions, context=20_000)) is None
    follow = recorder.switch(chat_id, sessions, follow_up=True, context=120_000)
    assert asyncio.run(follow) is None
    assert sessions.new == 0


def test_go_on_a_plan_builds_in_a_fresh_session(project: Path) -> None:
    recorder, sessions = ChatRecorder(project, "claude"), FakeSessions()
    one_turn(recorder, sessions, "Block out the Casting Hall")
    event = asyncio.run(
        recorder.switch(recorder.chat_id, sessions, context=30_000, build=True)
    )
    assert event is not None and event["mode"] == "fresh" and sessions.new == 1


def test_a_stopped_turn_is_carried_on_from_the_digest(project: Path) -> None:
    recorder, sessions = ChatRecorder(project, "claude"), FakeSessions()
    one_turn(recorder, sessions, "Keycards", outcome="stopped")
    event = asyncio.run(
        recorder.switch(recorder.chat_id, sessions, follow_up=True, context=120_000)
    )
    assert event is not None and event["mode"] == "fresh"
    sent = one_turn(recorder, sessions, "Please finish")
    assert "The last turn stopped before it finished (stopped)" in sent


def test_a_reopened_big_chat_resumes_only_as_a_follow_up(project: Path) -> None:
    first, sessions = ChatRecorder(project, "claude"), FakeSessions()
    one_turn(first, sessions, "Add a crate")
    chat_id = first.chat_id
    again = ChatRecorder(project, "claude")
    event = asyncio.run(again.switch(chat_id, sessions, follow_up=True))
    assert event is not None and event["mode"] == "resumed"
    later = ChatRecorder(project, "claude")
    event = asyncio.run(later.switch(chat_id, sessions))
    assert event is not None and event["mode"] == "fresh"
    assert sessions.resumed == ["h1"]


def test_fresh_at_zero_keeps_every_session(project: Path) -> None:
    (project / "joinery.toml").write_text("[agents]\nfresh_at = 0\n")
    assert request_digest.fresh_at(project) == 0
    recorder, sessions = ChatRecorder(project, "claude"), FakeSessions()
    sent = one_turn(recorder, sessions, "Add a crate")
    assert sent == "Add a crate"
    big = recorder.switch(recorder.chat_id, sessions, context=10**6)
    assert asyncio.run(big) is None
