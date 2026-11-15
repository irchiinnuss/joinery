"""The brief's Keep and Avoid lines: parsed, checked at submit, and holding an accept."""

from __future__ import annotations

from pathlib import Path

import pytest

from joinery import auto_accept, brief, keep_avoid
from tests.test_submit_for_review import Session

BRIEF = """# Game brief - Lamplight

## 1. Pitch
A keeper fixes the harbour lights before the storm.

## 19. Keep
What must stay true as the game changes.

- Jump height 1.2 m
- Player speed: 11 m/s
- Gravity 9.8 (physics/3d/default_gravity)
- The lamp flickers when the storm is close
- Dash time 0.2 s (res://player/dash.gd:dash_seconds)

## 20. Avoid
What the game must not have.

- Guns (gun, rifle, pistol)
- Timers in puzzles
- Loot boxes (loot box)

## 21. Open questions
None yet.
"""


def _project(root: Path) -> Path:
    (root / "design").mkdir(parents=True, exist_ok=True)
    (root / "design/brief.md").write_text(BRIEF, encoding="utf-8")
    return root


# --- Parsing -------------------------------------------------------------------------------


def test_keep_lines_pin_the_values_they_name() -> None:
    parsed = brief.parse(BRIEF)
    pins = {line.text: line.pin for line in parsed.keep}
    jump = pins["Jump height 1.2 m"]
    assert jump is not None and (jump.name, jump.value, jump.unit) == (
        "Jump height",
        1.2,
        "m",
    )
    speed = pins["Player speed: 11 m/s"]
    assert speed is not None and speed.unit == "m/s" and speed.name == "Player speed"
    gravity = pins["Gravity 9.8 (physics/3d/default_gravity)"]
    assert gravity is not None and gravity.target == "physics/3d/default_gravity"
    dash = pins["Dash time 0.2 s (res://player/dash.gd:dash_seconds)"]
    assert dash is not None and dash.target == "res://player/dash.gd:dash_seconds"
    assert pins["The lamp flickers when the storm is close"] is None
    assert [line.line for line in parsed.keep][:1] == [
        BRIEF.splitlines().index("- Jump height 1.2 m") + 1
    ]


def test_avoid_lines_say_what_to_look_for() -> None:
    parsed = brief.parse(BRIEF)
    guns, timers, loot = parsed.avoid
    assert guns.words == ["gun", "rifle", "pistol"] and guns.any_word
    assert timers.words == ["timer", "puzzle"] and not timers.any_word
    assert loot.words == ["loot box"] and loot.any_word


def test_the_template_has_empty_keep_and_avoid_sections() -> None:
    text = brief.draft({"pitch": "A frog jumps"}, None, [])
    parsed = brief.parse(text)
    assert parsed.keep == [] and parsed.avoid == []
    assert "## 19. Keep" in text and "## 20. Avoid" in text
    assert "Keep" not in parsed.gaps and "Avoid" not in parsed.gaps


def test_editing_keep_or_avoid_changes_the_briefs_direction() -> None:
    after = BRIEF.replace("- Timers in puzzles\n", "")
    assert brief.direction_changes(BRIEF, after) == ["Avoid"]
    after = BRIEF.replace("Jump height 1.2 m", "Jump height 1.5 m")
    assert brief.direction_changes(BRIEF, after) == ["Keep"]


def test_the_registry_summary_lists_them(tmp_path: Path) -> None:
    data = brief.summary(_project(tmp_path), None)
    assert "Jump height 1.2 m" in data["keep"]
    assert "Guns (gun, rifle, pistol)" in data["avoid"]


# --- The check -----------------------------------------------------------------------------


def _stage(
    tmp_path: Path, before: dict[str, str], after: dict[str, str]
) -> list[keep_avoid.Flag]:
    original = _project(tmp_path / "original")
    stage = tmp_path / "stage"
    for root, files in ((original, before), (stage, after)):
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
    changes = [
        {"path": rel, "change": "modified" if rel in before else "added"}
        for rel in after
    ]
    return keep_avoid.check(original, stage, changes)


def test_moving_an_exported_value_is_flagged(tmp_path: Path) -> None:
    flags = _stage(
        tmp_path,
        {"player/player.gd": "extends Node\n@export var jump_height := 1.2\n"},
        {"player/player.gd": "extends Node\n@export var jump_height := 1.5\n"},
    )
    assert [f.kind for f in flags] == ["keep"]
    assert flags[0].line == "Jump height 1.2 m"
    assert flags[0].what == "jump_height 1.2 → 1.5 (the brief pins 1.2 m)"
    assert flags[0].path == "player/player.gd"
    assert "Keep line “Jump height 1.2 m”" in flags[0].text()


def test_a_scene_override_and_a_project_setting_are_found(tmp_path: Path) -> None:
    scene = '[gd_scene format=3]\n\n[node name="Player" type="Node3D"]\n'
    flags = _stage(
        tmp_path,
        {
            "level.tscn": scene,
            "project.godot": "[physics]\n3d/default_gravity=9.8\n",
        },
        {
            "level.tscn": scene + "jump_height = 2.0\n",
            "project.godot": "[physics]\n3d/default_gravity=20.0\n",
        },
    )
    assert {f.line for f in flags} == {
        "Jump height 1.2 m",
        "Gravity 9.8 (physics/3d/default_gravity)",
    }


def test_the_subject_of_a_pin_can_be_the_files_path(tmp_path: Path) -> None:
    flags = _stage(
        tmp_path,
        {
            "player/player.gd": "var speed = 11.0\n",
            "enemy/crab.gd": "var speed = 3.0\n",
        },
        {
            "player/player.gd": "var speed = 14.0\n",
            "enemy/crab.gd": "var speed = 5.0\n",
        },
    )
    assert [(f.line, f.path) for f in flags] == [
        ("Player speed: 11 m/s", "player/player.gd")
    ]


def test_a_named_target_is_the_only_place_looked_at(tmp_path: Path) -> None:
    flags = _stage(
        tmp_path,
        {
            "player/dash.gd": "const dash_seconds = 0.2\n",
            "fx/dash.gd": "var dash_time = 1.0\n",
        },
        {
            "player/dash.gd": "const dash_seconds = 0.4\n",
            "fx/dash.gd": "var dash_time = 2.0\n",
        },
    )
    assert [(f.line, f.path) for f in flags] == [
        ("Dash time 0.2 s (res://player/dash.gd:dash_seconds)", "player/dash.gd")
    ]


def test_moving_back_to_the_pin_or_other_values_is_fine(tmp_path: Path) -> None:
    assert not _stage(
        tmp_path,
        {"player/player.gd": "var jump_height := 0.9\nvar coins := 3\n"},
        {"player/player.gd": "var jump_height := 1.2\nvar coins := 9\n"},
    )


def test_avoid_words_in_what_a_change_adds(tmp_path: Path) -> None:
    flags = _stage(
        tmp_path,
        {"player/player.gd": "extends Node\n"},
        {
            "player/player.gd": "extends Node\nvar rifle_ammo := 30\n",
            "puzzles/bridge_puzzle.gd": "var timer := Timer.new()\n",
        },
    )
    assert {(f.line, f.what) for f in flags} == {
        ("Guns (gun, rifle, pistol)", "the change adds “rifle”"),
        ("Timers in puzzles", "the change adds “timer puzzle”"),
    }


def test_words_already_there_are_not_added(tmp_path: Path) -> None:
    text = "extends Node\n# no guns here: the old gun stays\n"
    assert not _stage(tmp_path, {"a.gd": text}, {"a.gd": text + "var x := 1\n"})


def test_a_brief_edit_is_not_a_crossing(tmp_path: Path) -> None:
    assert not _stage(
        tmp_path,
        {"design/brief.md": BRIEF},
        {"design/brief.md": BRIEF.replace("Jump height 1.2 m", "Jump height 2 m")},
    )


def test_the_agents_own_summary_counts(tmp_path: Path) -> None:
    original = _project(tmp_path / "original")
    stage = tmp_path / "stage"
    (stage / "chest.gd").parent.mkdir(parents=True)
    (stage / "chest.gd").write_text("extends Node\n", encoding="utf-8")
    flags = keep_avoid.check(
        original,
        stage,
        [{"path": "chest.gd", "change": "added"}],
        said="Added a loot box that opens for coins",
    )
    assert [(f.line, f.path) for f in flags] == [("Loot boxes (loot box)", "")]


def test_no_brief_no_flags(tmp_path: Path) -> None:
    (tmp_path / "s").mkdir()
    (tmp_path / "s/a.gd").write_text("var gun = 1\n")
    assert (
        keep_avoid.check(
            tmp_path, tmp_path / "s", [{"path": "a.gd", "change": "added"}]
        )
        == []
    )


# --- In a turn -----------------------------------------------------------------------------


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    return Session(tmp_path, monkeypatch)


def test_a_crossing_holds_the_accept_and_shows_the_line(session: Session) -> None:
    _project(session.root)
    (session.root / "player.gd").write_text("var jump_height := 1.2\n")
    auto_accept.set_enabled(session.root, True)
    session.turn.begin("Make the jump higher")
    session.stage({"player.gd": "var jump_height := 1.8\n"})
    result = session.run(lambda: session.turn.submit("Higher jump"))
    assert result.status == "in_review"
    held = next(e for e in session.events if e["kind"] == "auto_accept_held")
    assert held["reasons"][0] == (
        "it crosses the game brief's Keep/Avoid lines (Keep: Jump height 1.2 m)"
    )
    assert any("Keep/Avoid" in note for note in result.notes)
    preview = next(e for e in session.events if e.get("kind") == "file_preview")
    assert preview["keep_avoid"][0]["line"] == "Jump height 1.2 m"
    warned = [n for n in preview["review_notes"] if n["severity"] == "warn"]
    assert warned and "Jump height 1.2 m" in warned[0]["text"]


def test_a_clean_change_is_still_kept(session: Session) -> None:
    _project(session.root)
    auto_accept.set_enabled(session.root, True)
    session.turn.begin("Add a lamp")
    session.stage({"lamp.gd": "extends Node\nvar glow := 2.0\n"})
    result = session.run(lambda: session.turn.submit("A lamp"))
    assert result.status == "accepted"


def test_held_names_the_crossed_lines() -> None:
    assert auto_accept.held(
        changes=[{"path": "a.gd"}], crossed=["Avoid: Guns", "Keep: Jump height 1.2 m"]
    ) == [
        "it crosses the game brief's Keep/Avoid lines (Avoid: Guns; Keep: Jump height 1.2 m)"
    ]
