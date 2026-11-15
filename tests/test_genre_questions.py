"""The optional question round: per-starting-point data, answers marked by where they
came from in the brief, defaults that never become design-check rules, and the
paths that carry the answers (wizard answers file, `joinery new --answers`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery import blueprints, brief, cli, design, genre_questions
from tests.test_design import write

STARTING_POINTS = (
    "blank",
    "first-person-3d",
    "third-person-3d",
    "top-down",
    "platformer-2d",
    "puzzle-cards",
    "vr-room",
)


def test_every_starting_point_asks_four_to_six_questions() -> None:
    assert genre_questions.problems() == []
    for blueprint_id in STARTING_POINTS:
        sheet = genre_questions.sheet(blueprint_id)
        assert sheet is not None, blueprint_id
        assert 4 <= len(sheet.question) <= 6
        keys = {q.key for q in sheet.question}
        # The loop, the challenge, the feel and the length of a sitting, everywhere.
        assert {"focus", "challenge", "feel", "session"} <= keys
        for question in sheet.question:
            assert question.choice(question.default) is not None
            assert len(question.ask) <= 70 and question.ask.endswith("?")


def test_the_wizard_catalogue_and_brief_questions_carry_the_round() -> None:
    described = blueprints.describe("top-down")
    round_ = described["questions"]
    assert round_["intro"]
    first = round_["questions"][0]
    assert first["key"] == "focus" and first["section"] == "Core loop"
    assert first["default"] in {c["id"] for c in first["choices"]}
    assert genre_questions.as_json("no-such-thing") == {"intro": "", "questions": []}


def test_brief_questions_with_a_blueprint_print_the_round(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["brief", "--project", str(tmp_path), "--questions"]) == 0
    plain = json.loads(capsys.readouterr().out)
    assert "round" not in plain
    assert (
        cli.main(
            [
                "brief",
                "--project",
                str(tmp_path),
                "--questions",
                "--blueprint",
                "platformer-2d",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["pages"] == plain["pages"]
    assert [q["key"] for q in data["round"]["questions"]][:2] == ["focus", "challenge"]


def test_answers_resolve_to_asked_default_or_skipped() -> None:
    answers = genre_questions.resolve(
        "platformer-2d",
        {"focus": "speed", "challenge": "skip", "feel": "a slow, careful climb"},
    )
    by_key = {a.key: a for a in answers}
    assert by_key["focus"].source == "asked" and by_key["focus"].choice == "speed"
    assert by_key["challenge"].source == "skipped" and by_key["challenge"].text == ""
    # Own words are the user's answer too, as one tidy sentence.
    assert by_key["feel"].source == "asked"
    assert by_key["feel"].text == "A slow, careful climb."
    # Questions not answered keep the suggestion, unconfirmed.
    assert by_key["session"].source == "default"
    assert by_key["goal"].source == "default" and by_key["goal"].choice == "flag"
    assert (
        genre_questions.resolve("platformer-2d", {"session": "default"})[3].source
        == "default"
    )


@pytest.mark.parametrize("skipped", [None, False, "skip"])
def test_the_whole_round_can_be_skipped(skipped: object) -> None:
    assert genre_questions.resolve("top-down", skipped) == []


def test_unknown_questions_and_long_answers_are_refused() -> None:
    with pytest.raises(ValueError, match="does not ask colour"):
        genre_questions.resolve("top-down", {"colour": "red"})
    with pytest.raises(ValueError, match="one short line"):
        genre_questions.resolve("top-down", {"feel": "x" * 300})
    with pytest.raises(ValueError, match="object of answers"):
        genre_questions.resolve("top-down", ["focus"])


def test_the_brief_marks_each_answer_and_reads_them_back() -> None:
    described = blueprints.describe("platformer-2d")
    answers = genre_questions.resolve(
        "platformer-2d", {"focus": "exploring", "challenge": "skip"}
    )
    text = brief.draft(
        {"pitch": "A fox climbs a tower."}, described, [], questions=answers
    )
    assert (
        "- Most of the time: Exploring levels for hidden coins and side paths. "
        "_(your answer)_" in text
    )
    assert "- A fall: _Not decided yet._ _(skipped)_" in text
    assert (
        "- One level: A level takes 1 to 3 minutes. "
        "_(suggested default, not confirmed)_" in text
    )
    # The starting point's own text stays, after the answers.
    loop = text.split("## 5. Core loop", 1)[1].split("## 6.", 1)[0]
    assert loop.index("_(your answer)_") < loop.index("_From the ")
    recorded = {(a.section, a.label): a.source for a in genre_questions.recorded(text)}
    assert recorded[("core_loop", "Most of the time")] == "asked"
    assert recorded[("progression", "A fall")] == "skipped"
    assert recorded[("session", "One level")] == "default"
    assert recorded[("fantasy", "Jump feel")] == "default"
    # A skipped question leaves its section open for Journeyman to ask about.
    assert "Win, lose, progression" in brief.parse(text).gaps


def test_without_the_round_the_brief_is_drafted_as_before() -> None:
    described = blueprints.describe("top-down")
    before = brief.draft({"pitch": "Arena."}, described, ["controller"])
    assert (
        brief.draft({"pitch": "Arena."}, described, ["controller"], questions=[])
        == before
    )
    assert "_(your answer)_" not in before and genre_questions.recorded(before) == []


def test_blank_puts_the_genre_answer_on_the_genre_line() -> None:
    described = blueprints.describe("blank")
    answers = genre_questions.resolve("blank", {"kind": "puzzle"})
    text = brief.draft({}, described, [], questions=answers)
    assert "- Genre: Puzzle: working things out matters most. _(your answer)_" in text
    assert ("genre", "Genre") in {
        (a.section, a.label) for a in genre_questions.recorded(text)
    }


def test_the_answers_file_carries_the_round(tmp_path: Path) -> None:
    (tmp_path / brief.ANSWERS_FILE).write_text(
        json.dumps({"pitch": "Arena.", "questions": {"feel": "deliberate"}}),
        encoding="utf-8",
    )
    written = brief.write_from_answers_file(
        tmp_path, blueprints.describe("top-down"), []
    )
    assert written is not None
    text = written.read_text(encoding="utf-8")
    assert "- Feel: Careful: read the enemy, then strike. _(your answer)_" in text
    assert "questions" not in text.split("## 2.", 1)[0]


def test_a_bad_round_is_refused_before_the_project_is_made(tmp_path: Path) -> None:
    target = tmp_path / "game"
    (tmp_path / "answers.json").write_text(
        json.dumps({"brief": {"pitch": "x", "questions": {"nope": "a"}}}),
        encoding="utf-8",
    )
    code = cli.main(
        [
            "new",
            str(target),
            "--blueprint",
            "top-down",
            "--tools",
            "godot",
            "--answers",
            str(tmp_path / "answers.json"),
        ]
    )
    assert code == 1
    assert not target.exists()


def test_brief_suggests_confirming_standing_defaults(tmp_path: Path) -> None:
    described = blueprints.describe("top-down")
    answers = genre_questions.resolve("top-down", {"focus": "fighting"})
    text = brief.draft({"pitch": "Arena."}, described, [], questions=answers)
    (tmp_path / "design").mkdir()
    (tmp_path / brief.BRIEF_PATH).write_text(text, encoding="utf-8")
    suggestions = brief.summary(tmp_path, None)["suggestions"]
    standing = [s for s in suggestions if "suggested defaults" in s]
    assert len(standing) == 1 and "Feel" in standing[0]
    assert "Most of the time" not in standing[0]


GODOT = """\
config_version=5

[application]

config/name="Arena"
"""


def _brief(win_line: str) -> str:
    return (
        "# Game brief — Arena\n\n## 1. Pitch\nArena.\n\n"
        "## 9. Win, lose, progression\n" + win_line + "\n"
    )


def test_a_suggested_default_is_never_a_design_rule(tmp_path: Path) -> None:
    default = write(
        tmp_path / "default",
        {
            "project.godot": GODOT,
            "design/brief.md": _brief(
                "- Lose: Lose: health runs out. _(suggested default, not confirmed)_"
            ),
        },
    )
    asked = write(
        tmp_path / "asked",
        {
            "project.godot": GODOT,
            "design/brief.md": _brief("- Lose: Lose: health runs out. _(your answer)_"),
        },
    )
    assert "no-lose" not in {f.check for f in design.check(default).findings}
    assert "no-lose" in {f.check for f in design.check(asked).findings}


def test_agent_findings_resting_on_a_default_are_dropped(tmp_path: Path) -> None:
    root = write(
        tmp_path / "p",
        {
            "project.godot": GODOT,
            "design/brief.md": _brief(
                "- Feel: Fast and frantic. _(suggested default, not confirmed)_"
            ),
        },
    )
    item = {
        "severity": "medium",
        "title": "Fights are slow",
        "player": "The fights drag.",
        "why": "The brief says fast and frantic.",
        "basis": "brief",
        "evidence": [
            {"path": "design/brief.md", "line": 7, "quote": "Fast and frantic"}
        ],
    }
    kept, dropped = design.agent_findings(root, {"findings": [item]})
    assert kept == []
    assert any("suggested default" in line for line in dropped)
