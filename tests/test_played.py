"""Changes not yet played by you (joinery.played): stamped on accept, cleared by a
person's run of the game from the editor, shown everywhere the same way."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from joinery import cli, played, timeline
from joinery.bootstrap import transaction_lines, transactions
from joinery.headless import Outcome, finish
from joinery.transactions import FileTransaction
from tests.test_submit_for_review import Session
from tests.test_timeline import _isolated_git, accept, started

__all__ = ["_isolated_git"]


def project(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "main.gd").write_text("extends Node\n")
    return tmp_path


def accepted(root: Path, summary: str, edits: dict[str, str]) -> FileTransaction:
    tx = FileTransaction(root)
    tx.begin(summary)
    for path, text in edits.items():
        target = tx.stage / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    tx.review()
    tx.accept()
    return tx


@pytest.mark.parametrize(
    ("paths", "game"),
    [
        (["main.gd"], True),
        (["res://levels/one.tscn"], True),
        (["project.godot"], True),
        (["art/models/door.blend", "art/models/door.glb"], True),
        (["audio/sfx/step.wav"], True),
        (["docs/notes.md"], False),
        (["design/brief.md", "README.md"], False),
        (["tests/test_door.gd"], False),
        (["art/reference/look.png", "joinery.toml", ".gitignore"], False),
        (["addons/joinery/plugin.gd"], False),
        (["docs/x.md", "main.gd"], True),
        ([], False),
    ],
)
def test_what_affects_the_game(paths: list[str], game: bool) -> None:
    assert played.affects_game(paths) is game


def test_an_accepted_change_to_the_game_is_stamped(tmp_path: Path) -> None:
    root = project(tmp_path)
    tx = accepted(root, "Wider door", {"door.gd": "extends Node\n"})
    assert played.is_unplayed(root, tx.id)
    summary = played.summary(root)
    assert summary["count"] == 1 and summary["ids"] == [tx.id]
    assert summary["text"] == "1 change not played yet"
    assert played.unplayed(root)[0]["summary"] == "Wider door"


def test_docs_only_changes_are_not_stamped(tmp_path: Path) -> None:
    root = project(tmp_path)
    accepted(root, "Notes", {"docs/notes.md": "# Notes\n"})
    assert played.summary(root)["count"] == 0
    assert not (root / played.STATE).exists()


def test_the_count_reads_plainly() -> None:
    assert played.count_text(0) == ""
    assert played.count_text(1) == "1 change not played yet"
    assert played.count_text(3) == "3 changes not played yet"


def test_a_long_enough_run_after_the_change_clears_it(tmp_path: Path) -> None:
    root = project(tmp_path)
    first = accepted(root, "Wider door", {"door.gd": "extends Node\n"})
    started_at = time.time() + 1
    # A run that starts after the change but is too short changes nothing.
    short = played.record_play(root, started_at, played.PLAY_SECONDS - 1)
    assert short["counted"] is False and short["count"] == 1
    result = played.record_play(root, started_at, played.PLAY_SECONDS + 5)
    assert result["counted"] is True and result["count"] == 0
    assert not played.is_unplayed(root, first.id)
    assert played.summary(root)["last_played"] == started_at


def test_a_change_accepted_during_the_run_stays_unplayed(tmp_path: Path) -> None:
    root = project(tmp_path)
    accepted(root, "Wider door", {"door.gd": "extends Node\n"})
    started_at = time.time() + 1
    later = accepted(root, "Red door", {"red.gd": "extends Node\n"})
    later_stamp = played.unplayed(root)[-1]
    later_stamp["accepted"] = started_at + 5
    state = json.loads((root / played.STATE).read_text())
    state["unplayed"][-1] = later_stamp
    (root / played.STATE).write_text(json.dumps(state))
    assert played.record_play(root, started_at, 60)["ids"] == [later.id]


def test_a_reverted_change_has_nothing_left_to_play(tmp_path: Path) -> None:
    root = project(tmp_path)
    tx = accepted(root, "Wider door", {"door.gd": "extends Node\n"})
    FileTransaction.load(root, tx.id).revert()
    assert played.summary(root)["count"] == 0


def test_a_broken_file_reads_as_nothing_waiting(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / ".joinery").mkdir()
    (root / played.STATE).write_text("not json")
    assert played.summary(root)["count"] == 0
    accepted(root, "Wider door", {"door.gd": "extends Node\n"})
    assert played.summary(root)["count"] == 1


def test_the_state_stays_out_of_git() -> None:
    assert ".joinery/*" in timeline.IGNORE_RULES
    assert not any(played.STATE.as_posix() in rule for rule in timeline.IGNORE_RULES)


def test_transactions_list_and_show_say_it(tmp_path: Path) -> None:
    root = project(tmp_path)
    tx = accepted(root, "Wider door", {"door.gd": "extends Node\n"})
    (listed,) = transactions(root, "list", tx.id)
    assert listed["unplayed"] is True
    assert "  " + played.CHANGE_LINE in transaction_lines(listed)
    played.record_play(root, time.time() + 1, 60)
    assert transactions(root, "list", tx.id)[0]["unplayed"] is False


def test_the_timeline_marks_its_entries(tmp_path: Path) -> None:
    root = started(tmp_path / "game")
    tx = accept(root, "Wider door", {"door.gd": "extends Node\n"})
    assert played.unplayed(root)[0]["commit"] == tx.timeline["commit"]
    # Godot's import files joining the entry rewrite it; the mark follows.
    (root / "door.gd.uid").write_text("uid://abc\n")
    tx.commit_generated()
    assert tx.timeline["commit"] == timeline._head(root)
    assert played.unplayed(root)[0]["commit"] == tx.timeline["commit"]
    accept(root, "Notes", {"docs/notes.md": "# Notes\n"})
    rows = timeline.entries(root)["entries"]
    by_subject = {row["subject"]: row["unplayed"] for row in rows}
    assert by_subject["Wider door"] is True and by_subject["Notes"] is False


def test_played_status_and_record_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    accepted(root, "Wider door", {"door.gd": "extends Node\n"})
    assert cli.main(["played", "status", "--project", str(root)]) == 0
    out = capsys.readouterr().out
    assert "1 change not played yet:" in out and "Wider door" in out
    assert cli.main(["played", "status", "--project", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1
    args = ["played", "record", "--project", str(root), "--json"]
    when = str(time.time() + 1)
    assert cli.main([*args, "--started", when, "--seconds", "30"]) == 0
    assert json.loads(capsys.readouterr().out) | {"last_played": 0} == {
        "count": 0,
        "text": "",
        "ids": [],
        "commits": [],
        "last_played": 0,
        "counted": True,
    }
    assert cli.main(["played", "status", "--project", str(root)]) == 0
    assert "played since" in capsys.readouterr().out


def test_joinery_status_says_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery import readiness

    root = project(tmp_path)
    accepted(root, "Wider door", {"door.gd": "extends Node\n"})
    report = {"tools": [], "unplayed": played.summary(root)}
    assert "  1 change not played yet: play the game to check them." in (
        readiness.lines(report)
    )


def test_the_run_result_says_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    accepted(root, "Wider door", {"door.gd": "extends Node\n"})
    finish(Outcome(state="accepted"), root, True)
    data = json.loads(capsys.readouterr().out)
    assert data["unplayed"]["count"] == 1
    finish(Outcome(state="accepted"), root, False)
    assert "1 change not played yet" in capsys.readouterr().out


def test_the_decision_event_carries_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = Session(tmp_path, monkeypatch)
    session.turn.begin("Add a script")
    tx = session.stage({"a.gd": "extends Node\n"})

    async def submit_and_accept() -> dict[str, object]:
        await session.turn.submit("A script")
        return await session.turn.decide(tx.id, True)

    event = session.run(submit_and_accept)
    unplayed = event["unplayed"]
    assert isinstance(unplayed, dict) and unplayed["ids"] == [tx.id]
