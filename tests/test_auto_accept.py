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
    assert json.loads(capsys.readouterr().out) == {"enabled": False}
    assert main(["auto-accept", "--project", project, "--json", "--set", "on"]) == 0
    assert json.loads(capsys.readouterr().out) == {"enabled": True}
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


def _note(severity: Any) -> PeerReview:
    return PeerReview("reviewed", [ReviewNote(severity, "Check this.", "a.gd")])


def test_a_clean_change_needs_nobody() -> None:
    assert auto_accept.held(changes=[{"path": "game/a.gd"}]) == []
    info = auto_accept.held(
        changes=[{"path": "a.gd"}], peer=_note("info"), reviewer_on=True
    )
    skipped = auto_accept.held(
        changes=[{"path": "a.gd"}], peer=PeerReview("skipped"), reviewer_on=True
    )
    assert info == [] and skipped == []


@pytest.mark.parametrize(
    "path",
    [
        "art/bible.md",
        "res://art/bible.md",
        "design/brief.md",
        "art/reference/mood.png",
        ".joinery/art-approval.json",
    ],
)
def test_art_direction_and_approvals_always_wait(path: str) -> None:
    held = auto_accept.held(changes=[{"path": "a.gd"}, {"path": path}])
    assert held == ["it changes the art direction or the game brief"]


@pytest.mark.parametrize(
    ("given", "reason"),
    [
        ({"notes": ["Nothing in a scene uses mug.glb yet."]}, "notes for the user"),
        ({"peer": _note("warn")}, "reviewer raised a warning"),
        ({"peer": _note("block")}, "reviewer raised a warning"),
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
    assert held["reasons"] == ["it changes the art direction or the game brief"]
    assert session.kinds()[-2:] == ["file_preview", "in_review"]


def test_on_a_reviewer_warning_waits(session: Session) -> None:
    auto_accept.set_enabled(session.root, True)

    async def reviewer(_request: Any) -> PeerReview:
        return _note("warn")

    session.turn.reviewer = reviewer
    session.turn.begin("Add a script")
    session.stage({"a.gd": "extends Node\n"})
    result = session.run(lambda: session.turn.submit("A script"))
    assert result.status == "in_review" and session.state == "review"


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


def test_metrics_record_an_automatic_accept(tmp_path: Path) -> None:
    from joinery import metrics

    target = metrics.metrics_dir(tmp_path) / "tx1.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("")
    metrics.record_decision(tmp_path, "tx1", True, auto=True)
    (span,) = metrics.read(target)
    assert span["attributes"][metrics.AUTO_ACCEPTED] is True
