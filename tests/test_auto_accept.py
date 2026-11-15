"""Accept changes automatically: the project's opt-in and the rule (ADR 0085)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from joinery import auto_accept
from joinery.cli import main
from joinery.done_when import Result
from joinery.peer_review import PeerReview, ReviewNote
from joinery.transactions import FileTransaction
from joinery.turn import NEXT_AUTO, TurnState
from tests.test_crew import CrewTest, makes, project, run
from tests.test_submit_for_review import Session

__all__ = ["project"]  # the crew tests' project fixture


def test_off_by_default_and_saved_beside_other_settings(tmp_path: Path) -> None:
    assert auto_accept.enabled(tmp_path) is False
    (tmp_path / "joinery.toml").write_text(
        "[tools]\nblender = true\n\n[agents]\nreviewer = true\n", encoding="utf-8"
    )
    assert auto_accept.enabled(tmp_path) is False
    auto_accept.set_enabled(tmp_path, True)
    assert auto_accept.enabled(tmp_path) is True
    text = (tmp_path / "joinery.toml").read_text(encoding="utf-8")
    assert "blender = true" in text and "reviewer = true" in text
    assert "auto_accept = true" in text
    auto_accept.set_enabled(tmp_path, False)
    assert auto_accept.enabled(tmp_path) is False


def test_only_a_real_true_turns_it_on(tmp_path: Path) -> None:
    (tmp_path / "joinery.toml").write_text(
        '[agents]\nauto_accept = "yes"\n', encoding="utf-8"
    )
    assert auto_accept.enabled(tmp_path) is False
    (tmp_path / "joinery.toml").write_text("not toml [", encoding="utf-8")
    assert auto_accept.enabled(tmp_path) is False


def test_cli_reads_and_sets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = str(tmp_path)
    assert main(["auto-accept", "--project", project, "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"enabled": False, "hold_on": "block"}
    assert main(["auto-accept", "--project", project, "--json", "--set", "on"]) == 0
    assert json.loads(capsys.readouterr().out) == {"enabled": True, "hold_on": "block"}
    assert auto_accept.enabled(tmp_path) is True
    assert main(["auto-accept", "--project", project, "--set", "off"]) == 0
    assert "waits for your Accept" in capsys.readouterr().out


def test_cli_says_why_a_save_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "joinery.toml").write_text("[agents\n", encoding="utf-8")
    code = main(["auto-accept", "--project", str(tmp_path), "--json", "--set", "on"])
    assert code == 1
    assert "error" in json.loads(capsys.readouterr().out)


# --- The rule ------------------------------------------------------------------------


def _note(severity: Any, rule: str = "") -> PeerReview:
    return PeerReview("reviewed", [ReviewNote(severity, "Check this.", "a.gd", rule)])


def test_a_clean_change_needs_nobody() -> None:
    assert auto_accept.held(changes=[{"path": "game/a.gd"}]) == []
    info = auto_accept.held(
        changes=[{"path": "a.gd"}], peer=_note("info"), reviewer_on=True
    )
    skipped = auto_accept.held(
        changes=[{"path": "a.gd"}], peer=PeerReview("skipped"), reviewer_on=True
    )
    assert info == [] and skipped == []
    # Live edits the editor keeps itself, in a saved scene with no unsaved work.
    live = auto_accept.held(
        changes=[],
        live_edits=2,
        live_scenes=["res://room.tscn"],
        editor_keeps_live=True,
    )
    assert live == []


@pytest.mark.parametrize(
    "path",
    [
        "art/bible.md",
        "res://art/bible.md",
        "art/reference/mood.png",
        ".joinery/art-approval.json",
    ],
)
def test_art_direction_and_approvals_always_wait(path: str) -> None:
    held = auto_accept.held(changes=[{"path": "a.gd"}, {"path": path}])
    assert held == ["it changes the art direction"]


@pytest.mark.parametrize(
    ("given", "reason"),
    [
        ({"notes": ["Nothing in a scene uses mug.glb yet."]}, "notes for the user"),
        ({"peer": _note("warn"), "hold_on": "warn"}, "reviewer raised a warning"),
        ({"peer": _note("warn", "broken")}, "may break or work be lost"),
        ({"peer": _note("warn", "work_lost")}, "may break or work be lost"),
        ({"peer": _note("block")}, "a problem that wasn't fixed"),
        ({"peer": _note("block"), "hold_on": "warn"}, "a problem that wasn't fixed"),
        ({"peer": PeerReview("timeout"), "reviewer_on": True}, "didn't finish"),
        ({"peer": PeerReview("error"), "reviewer_on": True}, "didn't finish"),
        ({"reviewer_on": True}, "didn't finish"),
        ({"done": [Result("d1", "Door opens", "inconclusive", "")]}, "done-when"),
        ({"done": [Result("d1", "Door opens", "fail", "")]}, "done-when"),
        ({"live_edits": 1}, "live scene edits"),
        ({"art_gate": True}, "needs approval"),
        ({"last_chance": True}, "submitted it for the agent"),
        ({"unsaved": ["res://room.tscn"]}, "unsaved edits"),
    ],
)
def test_anything_for_the_user_waits(given: dict[str, Any], reason: str) -> None:
    changes = [{"path": "a.gd"}, {"path": "room.tscn"}]
    held = auto_accept.held(changes=changes, **given)
    assert len(held) == 1 and reason in held[0]


def test_unsaved_scenes_come_from_the_editor_context() -> None:
    context = {"unsaved_scenes": json.dumps(["res://room.tscn"])}
    assert auto_accept.unsaved_scenes(context) == ["res://room.tscn"]
    assert auto_accept.unsaved_scenes({"unsaved_scenes": "{"}) == []
    assert auto_accept.unsaved_scenes({}) == []


# --- In a turn -----------------------------------------------------------------------


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    return Session(tmp_path, monkeypatch)


def test_off_a_passing_change_waits_for_the_user(session: Session) -> None:
    session.turn.begin("Add a script")
    session.stage({"a.gd": "extends Node\n"})
    result = session.run(lambda: session.turn.submit("A script"))
    assert result.status == "in_review" and session.state == "review"
    assert "file_decision" not in session.kinds()


def test_on_a_passing_change_is_kept_through_the_accept_path(
    session: Session,
) -> None:
    auto_accept.set_enabled(session.root, True)
    session.turn.begin("Add a script")
    tx = session.stage({"a.gd": "extends Node\n"})
    result = session.run(lambda: session.turn.submit("A script"))
    assert result.status == "accepted" and result.final
    tool = result.as_tool_result()
    assert tool["auto_accepted"] is True and tool["next"] == NEXT_AUTO
    assert (session.root / "a.gd").is_file()
    # No review opened: the Review tab has nothing pending for it.
    assert session.kinds() == [
        "working",
        "submitting",
        "in_review",
        "decided",
        "idle",
        "file_decision",
    ]
    assert session.turn.state is TurnState.IDLE and not session.turn.review_open
    decision = session.events[-1]
    assert decision["accepted"] and decision["auto_accepted"] is True
    assert decision["message"] == auto_accept.ACCEPTED
    assert decision["summary"] == "A script"
    saved = FileTransaction.load(session.root, tx.id)
    assert saved.state == "accepted" and saved.auto_accepted is True
    # The same Revert any accepted change has puts it back.
    saved.revert()
    assert not (session.root / "a.gd").exists()


def test_on_the_host_accept_path_is_used(session: Session) -> None:
    auto_accept.set_enabled(session.root, True)
    seen: list[str] = []

    async def host(transaction_id: str) -> dict[str, Any]:
        seen.append(transaction_id)
        return await session.turn.decide(transaction_id, True)

    session.turn.auto_decide = host
    session.turn.begin("Add a script")
    tx = session.stage({"a.gd": "extends Node\n"})
    result = session.run(lambda: session.turn.submit("A script"))
    assert result.status == "accepted" and seen == [tx.id]


def test_on_a_bible_change_still_waits_for_the_user(session: Session) -> None:
    auto_accept.set_enabled(session.root, True)
    session.turn.begin("Draft the art direction")
    session.stage({"art/bible.md": "# Art bible\n"})
    result = session.run(lambda: session.turn.submit("Art direction draft"))
    assert result.status == "in_review" and session.state == "review"
    held = next(e for e in session.events if e["kind"] == "auto_accept_held")
    assert held["reasons"] == ["it changes the art direction"]
    assert session.kinds()[-2:] == ["file_preview", "in_review"]


def test_on_a_reviewer_warning_waits_when_the_project_holds_warnings(
    session: Session,
) -> None:
    auto_accept.set_enabled(session.root, True)
    auto_accept.set_hold_level(session.root, "warn")

    async def reviewer(_request: Any) -> PeerReview:
        return _note("warn")

    session.turn.reviewer = reviewer
    session.turn.begin("Add a script")
    session.stage({"a.gd": "extends Node\n"})
    result = session.run(lambda: session.turn.submit("A script"))
    assert result.status == "in_review" and session.state == "review"


def test_on_reviewer_warnings_and_notes_ride_with_the_kept_change(
    session: Session,
) -> None:
    auto_accept.set_enabled(session.root, True)

    async def reviewer(_request: Any) -> PeerReview:
        return PeerReview(
            "reviewed",
            [
                ReviewNote("warn", "The HUD sits outside the safe area.", "hud.tscn"),
                ReviewNote("info", "No Krita texture yet.", ""),
            ],
        )

    session.turn.reviewer = reviewer
    session.turn.begin("Add a script")
    session.stage({"a.gd": "extends Node\n"})
    result = session.run(lambda: session.turn.submit("A script"))
    assert result.status == "accepted"
    decision = session.events[-1]
    assert decision["auto_accepted"] is True
    assert decision["notes"] == [
        "Warning: The HUD sits outside the safe area. (hud.tscn)",
        "Note: No Krita texture yet.",
    ]


BRIEF = """# Game brief - Foundry

## 1. Pitch
Hold the foundry against the slag.

## 2. Genre and references
- Genre: first-person shooter

## 7. Controls per device
| Action | Keys |
| fire | Mouse 1 |

## 17. Scope and milestones
Ten rooms.
"""


def test_on_a_routine_brief_edit_is_kept_with_a_note(session: Session) -> None:
    (session.root / "design").mkdir()
    (session.root / "design/brief.md").write_text(BRIEF, encoding="utf-8")
    auto_accept.set_enabled(session.root, True)
    session.turn.begin("Rename fire to attack")
    session.stage({"design/brief.md": BRIEF.replace("| fire |", "| attack |")})
    result = session.run(lambda: session.turn.submit("Renamed the action"))
    assert result.status == "accepted"
    assert session.events[-1]["notes"] == [
        "Note: updated the game brief (Controls per device); its direction is unchanged."
    ]


def test_on_a_brief_direction_change_waits(session: Session) -> None:
    (session.root / "design").mkdir()
    (session.root / "design/brief.md").write_text(BRIEF, encoding="utf-8")
    auto_accept.set_enabled(session.root, True)
    session.turn.begin("Make it a platformer")
    session.stage(
        {"design/brief.md": BRIEF.replace("first-person shooter", "platformer")}
    )
    result = session.run(lambda: session.turn.submit("New genre"))
    assert result.status == "in_review"
    held = next(e for e in session.events if e["kind"] == "auto_accept_held")
    assert held["reasons"] == [
        "it changes the game brief's direction (Genre and references)"
    ]


def test_a_brief_change_nobody_read_waits() -> None:
    assert auto_accept.held(changes=[{"path": "design/brief.md"}]) == [
        "it changes the game brief"
    ]
    assert (
        auto_accept.held(changes=[{"path": "design/brief.md"}], brief_direction=[])
        == []
    )


def test_the_hold_level_is_saved_and_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert auto_accept.hold_level(tmp_path) == "block"
    project = str(tmp_path)
    assert (
        main(["auto-accept", "--project", project, "--json", "--hold-on", "warn"]) == 0
    )
    assert json.loads(capsys.readouterr().out)["hold_on"] == "warn"
    assert 'auto_accept_hold = "warn"' in (tmp_path / "joinery.toml").read_text()
    (tmp_path / "joinery.toml").write_text('[agents]\nauto_accept_hold = "x"\n')
    assert auto_accept.hold_level(tmp_path) == "block"
    with pytest.raises(ValueError, match="block or warn"):
        auto_accept.set_hold_level(tmp_path, "info")


def test_notes_shown_are_those_that_did_not_hold() -> None:
    peer = PeerReview(
        "reviewed",
        [
            ReviewNote("block", "Broken.", "a.gd", "broken"),
            ReviewNote("warn", "Risky.", "a.gd", "request"),
            ReviewNote("warn", "Crashes.", "a.gd", "broken"),
            ReviewNote("info", "Polish later.", "a.gd"),
        ],
    )
    assert auto_accept.shown_notes(peer) == [
        "Warning: Risky. (a.gd)",
        "Note: Polish later. (a.gd)",
    ]
    assert auto_accept.shown_notes(peer, "warn") == ["Note: Polish later. (a.gd)"]


def test_on_a_variant_is_never_kept_alone(session: Session) -> None:
    auto_accept.set_enabled(session.root, True)
    session.turn.auto_accepts = False
    session.turn.begin("Add a script")
    session.stage({"a.gd": "extends Node\n"})
    result = session.run(lambda: session.turn.submit("A script"))
    assert result.status == "in_review"
    assert "auto_accept_held" not in session.kinds()


def test_an_accept_that_fails_leaves_the_change_in_review(
    session: Session,
) -> None:
    auto_accept.set_enabled(session.root, True)

    async def refuses(_transaction_id: str) -> dict[str, Any]:
        raise ValueError("The project changed under this change.")

    session.turn.auto_decide = refuses
    session.turn.begin("Add a script")
    tx = session.stage({"a.gd": "extends Node\n"})
    result = session.run(lambda: session.turn.submit("A script"))
    assert result.status == "in_review" and session.state == "review"
    assert tx.auto_accepted is False
    assert session.kinds()[-3:] == ["in_review", "auto_accept_held", "file_preview"]
    assert session.turn.state is TurnState.IN_REVIEW and session.turn.review_open


def test_on_live_edits_alone_go_to_the_editor_with_the_hint(session: Session) -> None:
    auto_accept.set_enabled(session.root, True)
    session.turn.begin("Put the mug on the table")
    session.turn.record_live_edit("res://room.tscn", True)
    result = session.run(lambda: session.turn.submit("Mug on the table"))
    assert result.status == "accepted" and result.as_tool_result()["auto_accepted"]
    # The editor keeps them with its own Accept: the turn waits for its decision.
    assert session.turn.state is TurnState.IN_REVIEW and session.turn.review_open
    state = next(e for e in session.events if e.get("state") == "in_review")
    assert state["auto_accept"] is True
    assert "auto_accept_held" not in session.kinds()
    assert "file_decision" not in session.kinds()
    session.turn.live_decided()
    assert session.turn.state is TurnState.IDLE


def test_on_files_with_live_edits_go_to_the_editor_with_the_hint(
    session: Session,
) -> None:
    auto_accept.set_enabled(session.root, True)
    session.turn.begin("Make a script and move the mug")
    tx = session.stage({"a.gd": "extends Node\n"})
    session.turn.record_live_edit("res://room.tscn", True)
    result = session.run(lambda: session.turn.submit("Script and mug"))
    assert result.status == "accepted" and result.transaction_id == tx.id
    preview = next(e for e in session.events if e["kind"] == "file_preview")
    assert preview["auto_accept"] is True and preview["auto_accept_held"] == []
    # Nothing is accepted here: the editor's own Accept decides the files too.
    assert session.state == "review" and tx.auto_accepted is False
    assert session.kinds()[-2:] == ["file_preview", "in_review"]


@pytest.mark.parametrize(
    ("scene", "unsaved", "task", "reason"),
    [
        ("res://room.tscn", ["res://room.tscn"], "", "unsaved edits"),
        ("", [], "", "isn't saved yet"),
        ("res://room.tscn", [], "t1", "live scene edits"),
    ],
)
def test_live_edits_still_wait_when_the_editor_must_not_keep_them(
    session: Session, scene: str, unsaved: list[str], task: str, reason: str
) -> None:
    auto_accept.set_enabled(session.root, True)
    # The user's own unsaved work from before the request is never saved for them.
    session.gateway.editor_context = {"unsaved_scenes": json.dumps(unsaved)}
    if task:
        session.gateway.task_id = task
    session.turn.begin("Put the mug on the table")
    session.turn.record_live_edit(scene, True)
    result = session.run(lambda: session.turn.submit("Mug on the table"))
    assert result.status == "in_review"
    (held,) = [e for e in session.events if e["kind"] == "auto_accept_held"]
    assert any(reason in r for r in held["reasons"])
    state = next(e for e in session.events if e.get("state") == "in_review")
    assert "auto_accept" not in state


def test_the_editors_own_accept_is_recorded_as_automatic(session: Session) -> None:
    session.turn.begin("Add a script")
    tx = session.stage({"a.gd": "extends Node\n"})

    async def go() -> dict[str, Any]:
        await session.turn.submit("A script")
        tx.auto_accepted = True  # what the host does for the editor's "auto" decision
        return await session.turn.decide(tx.id, True)

    decision = session.run(go)
    assert decision["auto_accepted"] is True
    assert decision["message"] == auto_accept.ACCEPTED


def test_a_parallel_task_is_kept_through_the_crews_accept(
    project: Path,
) -> None:
    auto_accept.set_enabled(project, True)
    test = CrewTest(project)

    async def go() -> None:
        crew = test.crew()
        test.steps = [makes("notes/chair.txt", "chair")]
        task = await crew.start(
            "Chair", "Make a chair", origin="user", prompt=test.prompt()
        )
        assert task.job is not None
        await task.job
        assert task.state == "accepted" and (project / "notes/chair.txt").exists()
        decision = next(e for e in test.events if e["kind"] == "file_decision")
        assert decision["auto_accepted"] is True and decision["task_id"] == task.id
        assert decision["transaction_id"] == task.transaction_id
        assert not test.tasks("in_review")
        await crew.close()
        await test.gateway.close()

    run(go)


def test_a_variant_task_waits_for_the_users_pick(project: Path) -> None:
    auto_accept.set_enabled(project, True)
    test = CrewTest(project)

    async def go() -> None:
        crew = test.crew()
        test.steps = [makes("notes/chair.txt", "chair")]
        task = crew.add(
            "Chair",
            "Make a chair",
            origin="user",
            prompt=test.prompt(),
            text="Make a chair",
            group="g1",
        )
        crew.pump()
        assert task.job is not None
        await task.job
        assert task.state == "in_review"
        assert not (project / "notes/chair.txt").exists()
        await crew.close()
        await test.gateway.close()

    run(go)


def test_a_saved_chat_remembers_the_change_was_kept_without_asking(
    tmp_path: Path,
) -> None:
    from bridge.chat_record import ChatRecorder
    from joinery import chats

    recorder = ChatRecorder(tmp_path, "codex")
    recorder.begin({"text": "Add a script"}, ())
    recorder.observe(
        {
            "kind": "file_decision",
            "transaction_id": "a" * 32,
            "accepted": True,
            "auto_accepted": True,
            "summary": "A script",
            "notes": ["Reviewer: the timer never stops", " "],
        }
    )
    _, entries = chats.read(tmp_path, recorder.chat_id)
    (change,) = [e for e in entries if e["type"] == "change"]
    assert change["state"] == "accepted" and change["auto"] is True
    assert change["summary"] == "A script"
    # A reopened chat shows the notes under "Accepted automatically".
    assert change["notes"] == ["Reviewer: the timer never stops"]


def test_a_kept_change_keeps_its_notes_in_the_transaction(tmp_path: Path) -> None:
    from joinery.bootstrap import transactions

    (tmp_path / "project.godot").write_text("config_version=5\n")
    tx = FileTransaction(tmp_path)
    tx.begin("A script")
    tx.auto_accepted = True
    tx.auto_notes = ["Reviewer: the timer never stops"]
    tx._record()
    saved = FileTransaction.load(tmp_path, tx.id)
    assert saved.auto_notes == ["Reviewer: the timer never stops"]
    (listed,) = transactions(tmp_path, "list")
    assert listed["auto_notes"] == ["Reviewer: the timer never stops"]


def test_the_agent_says_it_was_kept_and_never_asks_for_an_accept() -> None:
    from bridge.instructions import CONTINUE_AFTER_FAILED_SUBMIT, REVIEW_GUIDE

    assert "If it returns accepted" in REVIEW_GUIDE
    assert "that it was kept, never ask the user to accept it" in REVIEW_GUIDE
    assert "nor after it returns accepted" in CONTINUE_AFTER_FAILED_SUBMIT
    assert "Don't ask the user to accept it" in NEXT_AUTO


def test_metrics_record_an_automatic_accept(tmp_path: Path) -> None:
    from joinery import metrics

    target = metrics.metrics_dir(tmp_path) / "tx1.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("")
    metrics.record_decision(tmp_path, "tx1", True, auto=True)
    (span,) = metrics.read(target)
    assert span["attributes"][metrics.AUTO_ACCEPTED] is True


def test_a_change_to_joinerys_own_harness_always_waits(tmp_path: Path) -> None:
    from bridge.instructions import REVIEW_GUIDE
    from tests.blueprints.godot_run import make_project

    game = make_project(tmp_path, "blank")
    paths = ["tests/support/play.gd", "game/level.gd", "res://notes.md"]
    owned = auto_accept.owned_changes(game, paths)
    assert owned == ["tests/support/play.gd"]
    reasons = auto_accept.held(
        changes=[{"path": "tests/support/play.gd"}], owned=owned
    )
    assert reasons == ["a Joinery-owned harness file changed (tests/support/play.gd)"]
    assert "tests/support/play.gd" in auto_accept.owned_note(owned)
    # The game's own files keep the usual rule.
    assert auto_accept.owned_changes(game, ["game/level.gd"]) == []
    assert auto_accept.held(changes=[{"path": "game/level.gd"}]) == []
    # Journeyman is told to fix the game or its own test, never the harness.
    assert "never edit Joinery's shared core or test harness" in REVIEW_GUIDE
