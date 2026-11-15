"""submit_for_review's guessed and not_as_asked: kept with the change, shown, and a
departure from the request holds auto-accept."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from bridge.chat_record import ChatRecorder
from joinery import auto_accept, chats
from joinery.bootstrap import transaction_lines, transactions
from joinery.cli import main
from joinery.headless import Outcome, finish
from joinery.transactions import FileTransaction
from joinery.turn import DEPARTURE_CHARS, DEPARTURE_ITEMS, departures
from tests.test_submit_for_review import Session


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    return Session(tmp_path, monkeypatch)


def _in_review(session: Session) -> dict[str, Any]:
    return next(e for e in session.events if e.get("state") == "in_review")


def _preview(session: Session) -> dict[str, Any]:
    return next(e for e in session.events if e.get("kind") == "file_preview")


def test_lines_are_tidied_and_kept_short() -> None:
    lines = ["  Made it  oak\n", "", "Made it oak", *[str(n) * 500 for n in range(9)]]
    tidied = departures(lines)
    assert tidied[0] == "Made it oak" and len(tidied) == DEPARTURE_ITEMS
    assert all(len(line) <= DEPARTURE_CHARS for line in tidied)


def test_both_ride_with_the_change_its_card_and_its_journal(session: Session) -> None:
    session.turn.begin("Make a door")
    tx = session.stage({"a.gd": "extends Node\n"})
    result = session.run(
        lambda: session.turn.submit(
            "A door",
            guessed=["Made it oak; no wood was named"],
            not_as_asked=["1.2 m wide, not 3 m: the wall is 2.5 m"],
        )
    )
    assert result.status == "in_review"
    for event in (_in_review(session), _preview(session)):
        assert event["guessed"] == ["Made it oak; no wood was named"]
        assert event["not_as_asked"] == ["1.2 m wide, not 3 m: the wall is 2.5 m"]
    saved = FileTransaction.load(session.root, tx.id)
    assert saved.guessed == ["Made it oak; no wood was named"]
    assert saved.not_as_asked == ["1.2 m wide, not 3 m: the wall is 2.5 m"]
    (listed,) = transactions(session.root, "list", tx.id)
    assert listed["guessed"] == saved.guessed
    assert listed["not_as_asked"] == saved.not_as_asked
    assert "  Not as asked: 1.2 m wide, not 3 m: the wall is 2.5 m" in (
        transaction_lines(listed)
    )


def test_nothing_to_say_leaves_them_out(session: Session) -> None:
    session.turn.begin("Make a door")
    tx = session.stage({"a.gd": "extends Node\n"})
    session.run(lambda: session.turn.submit("A door", guessed=[" "]))
    assert "guessed" not in _in_review(session)
    assert "not_as_asked" not in _preview(session)
    journal = json.loads(FileTransaction.load(session.root, tx.id).journal.read_text())
    assert "guessed" not in journal and "not_as_asked" not in journal


def test_a_departure_holds_auto_accept(session: Session) -> None:
    auto_accept.set_enabled(session.root, True)
    session.turn.begin("Make a door 3 m wide")
    session.stage({"a.gd": "extends Node\n"})
    result = session.run(
        lambda: session.turn.submit(
            "A door", not_as_asked=["1.2 m wide: the wall is 2.5 m"]
        )
    )
    assert result.status == "in_review" and session.state == "review"
    held = next(e for e in session.events if e["kind"] == "auto_accept_held")
    assert held["reasons"] == [auto_accept.DEPARTED]
    assert auto_accept.DEPARTED == "differs from your request"


def test_a_guess_alone_is_kept_and_shown_with_it(session: Session) -> None:
    auto_accept.set_enabled(session.root, True)
    session.turn.begin("Make a door")
    tx = session.stage({"a.gd": "extends Node\n"})
    result = session.run(lambda: session.turn.submit("A door", guessed=["Made it oak"]))
    assert result.status == "accepted"
    decision = session.events[-1]
    assert decision["auto_accepted"] is True
    assert decision["notes"] == ["Guessed: Made it oak"]
    assert FileTransaction.load(session.root, tx.id).guessed == ["Made it oak"]


def test_the_rule_itself() -> None:
    clean = [{"path": "game/a.gd"}]
    assert auto_accept.held(changes=clean, not_as_asked=["", "  "]) == []
    assert auto_accept.held(changes=clean, not_as_asked=["Smaller"]) == [
        auto_accept.DEPARTED
    ]
    assert auto_accept.guessed_notes(["oak", " "]) == ["Guessed: oak"]


def test_the_rpc_takes_them_and_refuses_anything_but_lines(session: Session) -> None:
    from joinery.gateway_rpc import GatewayRPC, rpc

    session.turn.begin("Make a door")
    session.stage({"a.gd": "extends Node\n"})

    async def go() -> dict[str, Any]:
        server = GatewayRPC(session.gateway)
        await server.start()
        try:
            with pytest.raises(RuntimeError, match="guessed is a list"):
                await rpc(
                    server.path, "submit", {"summary": "A door", "guessed": "oak"}
                )
            return await rpc(
                server.path,
                "submit",
                {"summary": "A door", "guessed": ["oak"], "not_as_asked": []},
            )
        finally:
            await server.close()

    assert asyncio.run(go())["status"] == "in_review"
    assert _in_review(session)["guessed"] == ["oak"]


def test_the_proxy_offers_both() -> None:
    from servers.joinery_registry.proxy import make_server

    tools = asyncio.run(make_server(Path("gateway.sock")).list_tools())
    (tool,) = [t for t in tools if t.name == "submit_for_review"]
    assert tool.input_schema["required"] == ["summary"]
    assert {"guessed", "not_as_asked"} <= set(tool.input_schema["properties"])
    assert "leave them empty when there's nothing to say" in (tool.description or "")


def test_every_agent_is_told_to_fill_them() -> None:
    from bridge import instructions

    text = instructions.REVIEW_GUIDE
    assert "guessed" in text and "not_as_asked" in text
    assert "leave them empty when there is nothing to say" in text


def test_the_chat_keeps_them_with_the_change(tmp_path: Path) -> None:
    record = ChatRecorder(tmp_path, "codex")
    record.begin({"text": "Make a door"}, ())
    record.observe(
        {
            "kind": "file_preview",
            "transaction_id": "a" * 32,
            "summary": "A door",
            "guessed": ["oak"],
            "not_as_asked": ["1.2 m wide"],
        }
    )
    _meta, entries = chats.read(tmp_path, record.chat_id)
    (change,) = [e for e in entries if e["type"] == "change"]
    assert change["guessed"] == ["oak"] and change["not_as_asked"] == ["1.2 m wide"]


def test_the_run_result_carries_them(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    session.turn.begin("Make a door")
    tx = session.stage({"a.gd": "extends Node\n"})
    session.run(
        lambda: session.turn.submit("A door", guessed=["oak"], not_as_asked=["smaller"])
    )
    outcome = Outcome(state="review", transaction_id=tx.id, summary="A door")
    finish(outcome, session.root, True)
    data = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert data["guessed"] == ["oak"] and data["not_as_asked"] == ["smaller"]
    finish(outcome, session.root, False)
    text = capsys.readouterr().out
    assert "Guessed: oak" in text and "Not as asked: smaller" in text


def test_transactions_show(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    session.turn.begin("Make a door")
    tx = session.stage({"a.gd": "extends Node\n"})
    session.run(lambda: session.turn.submit("A door", guessed=["oak"]))
    args = ["transactions", "show", "--project", str(session.root), "--id", tx.id]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["guessed"] == ["oak"]
    assert main([*args, "--text"]) == 0
    assert "  Guessed: oak" in capsys.readouterr().out.splitlines()
    assert main(args[:4]) == 1
    assert "--id" in capsys.readouterr().out
