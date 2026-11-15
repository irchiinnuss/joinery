"""A blank look is said plainly: by `joinery new`, `joinery status`, doctor, the
editor's art gate and `joinery bible --json`, each with how to fill it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from joinery import look_preview, readiness
from joinery.art_approval import approve, approved
from joinery.bible import write_bible
from joinery.bible_lint import completeness, gate_note
from joinery.cli import main
from tests.test_bible_approved_at_creation import ANSWERS

REQUIRED = ["Vision", "References", "Visual style", "Palette", "Audio", "Never"]


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOINERY_NO_DOWNLOADS", "1")


def _project(tmp_path: Path, answers: dict[str, str] | None = None) -> Path:
    project = tmp_path / "game"
    project.mkdir(parents=True)
    (project / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Door"\n'
    )
    if answers is not None:
        write_bible(project, answers)
    return project


def test_a_bible_written_without_answers_is_blank(tmp_path: Path) -> None:
    project = _project(tmp_path, {})
    look = completeness(project)
    assert look["state"] == "blank" and look["complete"] is False
    assert look["missing"] == REQUIRED
    assert "Pillars" in look["open"]
    assert "is blank" in look["message"] and "can't be approved" in look["message"]
    # How to fill it: the interview in the editor, or an answers file.
    assert "/art" in look["message"] and "--answers FILE" in look["message"]
    assert gate_note(look).startswith("The look is blank.")


def test_a_partly_answered_bible_names_what_it_still_lacks(tmp_path: Path) -> None:
    project = _project(tmp_path, {"vision": ANSWERS["vision"], "look": ANSWERS["look"]})
    look = completeness(project)
    assert look["state"] == "unfinished"
    assert look["missing"] == ["References", "Palette", "Audio", "Never"]
    assert "References, Palette, Audio, Never still to fill" in look["message"]
    assert gate_note(look) == "Still to decide: References, Palette, Audio, Never."


def test_a_finished_bible_is_complete(tmp_path: Path) -> None:
    project = _project(tmp_path, ANSWERS)
    look = completeness(project)
    assert look["state"] == "complete" and look["complete"] is True
    assert look["missing"] == [] and look["message"] == ""
    assert gate_note(look) == ""
    assert completeness(tmp_path)["state"] == "missing"


def test_a_blank_bible_approved_at_creation_still_says_it_is_blank(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, {})
    approve(project, "user (at project creation)", allow_open=True)
    look = completeness(project)
    assert look["state"] == "blank" and look["approved"] is True
    assert "no look to follow" in look["message"]
    assert "can't be approved" not in look["message"]


def test_approving_an_unfinished_bible_says_what_is_missing(tmp_path: Path) -> None:
    project = _project(tmp_path, {})
    with pytest.raises(ValueError, match="Finish the art direction") as refused:
        approve(project, "Mila")
    assert "still to fill: Vision, References" in str(refused.value)
    assert "--answers FILE" in str(refused.value)


def test_bible_json_reports_completeness(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path, {})
    assert main(["bible", "--project", str(project), "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["state"] == "blank" and found["complete"] is False
    assert found["missing"] == REQUIRED and found["written"] == ""


def test_bible_answers_fill_a_blank_look_but_never_overwrite_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path, {})
    answers = tmp_path / "look.json"
    # The shape `joinery new --answers` takes works too.
    answers.write_text(json.dumps({"bible": ANSWERS}))
    args = ["bible", "--project", str(project), "--answers", str(answers), "--json"]
    assert main(args) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["state"] == "complete" and found["written"]
    assert "Cosy and hand-made" in (project / "art/bible.md").read_text()
    assert not (project / ".joinery-bible-answers.json").exists()
    # Now the look has the user's words: changing it is a reviewed edit.
    answers.write_text(json.dumps({"vision": "Grim."}))
    assert main(args) == 1
    assert "already exists" in json.loads(capsys.readouterr().out)["error"]
    assert "Cosy and hand-made" in (project / "art/bible.md").read_text()


def test_joinery_new_says_the_look_is_blank(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "game"
    assert main(["new", str(path), "--tools", "godot", "--blueprint", "top-down"]) == 0
    out = capsys.readouterr().out
    assert "The look (art/bible.md) is blank" in out
    assert "--answers FILE" in out


def test_status_and_doctor_text_say_the_look_is_blank(tmp_path: Path) -> None:
    project = _project(tmp_path, {})
    report: dict[str, Any] = {"tools": [], "look": completeness(project)}
    text = "\n".join(readiness.lines(report))
    assert "The look (art/bible.md) is blank" in text
    report["look"] = completeness(_project(tmp_path / "done", ANSWERS))
    assert "art/bible.md" not in "\n".join(readiness.lines(report))


def test_status_json_carries_the_look(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _project(tmp_path, {})
    monkeypatch.setattr(
        readiness, "status", lambda *_a, **_k: {"tools": [], "checking": False}
    )
    assert readiness.main(["--project", str(project)]) == 0
    assert json.loads(capsys.readouterr().out)["look"]["state"] == "blank"


def test_doctor_report_carries_the_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from joinery.doctor import readiness_report

    project = _project(tmp_path, {})
    monkeypatch.setattr(
        readiness, "status", lambda *_a, **_k: {"tools": [], "checking": False}
    )
    found = readiness_report(project / "joinery.toml")
    assert found["look"]["state"] == "blank" and found["problems"] == 0


def test_the_art_gate_says_the_look_is_blank(tmp_path: Path) -> None:
    from joinery.gateway import ProjectGateway

    project = _project(tmp_path, {})
    (project / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    gate = ProjectGateway(project).art_gate_event()
    assert gate["message"].startswith("The look (art/bible.md) is blank")
    preview = look_preview.gate_preview(project)
    assert preview["state"] == "blank" and preview["note"].startswith(
        "The look is blank"
    )
    assert not approved(project)
