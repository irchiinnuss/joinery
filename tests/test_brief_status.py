"""The brief's features marked built, missing, broken or not checked, from evidence only."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from joinery import brief, brief_status, playtest, replays
from joinery.cli import main

BRIEF = """# Game brief - Lamplight

## 17. Scope and milestones
| Milestone | Done when | Target |
|---|---|---|
| Prototype | The keeper reaches the lighthouse (reach: lighthouse) | _Not decided yet._ |
| Release | _Not decided yet._ | _Not decided yet._ |

## 18. Features
One feature per line.

- [ ] The harbour has a crane (reach: crane)
- [x] Lamps light up (test: res://tests/test_lamp.gd)
- Storm plays back the same (replay: first_storm)
- No errors in the first minute (no errors: 60 s)
- A boat to row (test: res://tests/test_boat.gd)
- Fishing for fun

## 19. Keep
None yet.
"""


def _project(root: Path, text: str = BRIEF) -> Path:
    (root / "design").mkdir(parents=True, exist_ok=True)
    (root / "design/brief.md").write_text(text, encoding="utf-8")
    (root / "project.godot").write_text("config_version=5\n")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests/test_lamp.gd").write_text("extends SceneTree\n")
    (root / replays.FOLDER).mkdir(parents=True, exist_ok=True)
    (root / replays.FOLDER / "first_storm.replay").write_text("{}\n")
    return root


def _report(
    *, crane: bool | None, lighthouse: bool, errors: bool = False
) -> dict[str, Any]:
    reach = [
        {
            "target": "lighthouse",
            "found": True,
            "reached": lighthouse,
            "t": 31.0 if lighthouse else None,
            "closest": 0.0 if lighthouse else 6.0,
            "node": "Lighthouse",
        }
    ]
    if crane is not None:
        reach.append({"target": "crane", "found": crane, "reached": False, "t": None})
    items = (
        [{"kind": "script_error", "title": "Script error", "detail": "nil", "t": 3.0}]
        if errors
        else []
    )
    return {"seed": 4242, "played_seconds": 60.0, "items": items, "reach": reach}


def test_feature_lines_and_milestones_are_parsed() -> None:
    parsed = brief.parse(BRIEF)
    found = [(f.where, f.kind, f.target, f.text) for f in parsed.features]
    assert found == [
        (
            "milestone",
            "reach",
            "lighthouse",
            "Prototype: The keeper reaches the lighthouse",
        ),
        ("features", "reach", "crane", "The harbour has a crane"),
        ("features", "test", "res://tests/test_lamp.gd", "Lamps light up"),
        ("features", "replay", "first_storm", "Storm plays back the same"),
        ("features", "no_errors", "", "No errors in the first minute"),
        ("features", "test", "res://tests/test_boat.gd", "A boat to row"),
        ("features", "", "", "Fishing for fun"),
    ]
    assert parsed.features[4].seconds == 60.0
    assert (
        parsed.features[1].line
        == BRIEF.splitlines().index("- [ ] The harbour has a crane (reach: crane)") + 1
    )
    # The id follows the words: a changed line is a new line.
    changed = brief.parse(BRIEF.replace("has a crane", "has two cranes"))
    assert changed.features[1].id != parsed.features[1].id
    assert changed.features[2].id == parsed.features[2].id


def test_nothing_known_is_not_checked_and_absent_files_are_missing(
    tmp_path: Path,
) -> None:
    found = {m.text: m for m in brief_status.marks(_project(tmp_path))}
    assert found["Fishing for fun"].status == "not_checked"
    assert found["Fishing for fun"].evidence == brief_status.NOTHING_TO_CHECK
    assert found["The harbour has a crane"].status == "not_checked"
    assert found["The harbour has a crane"].checked == ""
    assert found["A boat to row"].status == "missing"
    assert (
        "res://tests/test_boat.gd isn't in the project"
        in found["A boat to row"].evidence
    )
    assert found["Lamps light up"].status == "not_checked"


def test_a_playtest_run_is_evidence_with_its_time(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run = project / playtest.FOLDER / "20261115-140200-abc123"
    run.mkdir(parents=True)
    (run / "report.json").write_text(json.dumps(_report(crane=False, lighthouse=True)))
    found = {m.text: m for m in brief_status.marks(project)}
    built = found["Prototype: The keeper reaches the lighthouse"]
    assert built.status == "built" and built.source == "playtest" and built.checked
    assert "The bot reached Lighthouse at" in built.evidence
    assert "20261115-140200-abc123" in built.evidence
    crane = found["The harbour has a crane"]
    assert crane.status == "missing" and "named like" in crane.evidence
    assert found["No errors in the first minute"].status == "built"


def test_check_gathers_fresh_evidence_and_keeps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    asked: list[playtest.Options] = []

    async def play(options: playtest.Options) -> dict[str, Any]:
        asked.append(options)
        return _report(crane=None, lighthouse=False, errors=True)

    monkeypatch.setattr("joinery.done_when._play", play)
    ran: list[str] = []

    def test(root: Path, rel: str) -> tuple[bool, str]:
        ran.append(rel)
        return True, "ok"

    def replay(root: Path, path: Path) -> replays.Outcome:
        return replays.Outcome(
            path.stem, "diverged", "the lamp state differs", frame=120, seconds=2.0
        )

    found = {
        m.text: m
        for m in __import__("asyncio").run(
            brief_status.check(project, test=test, replay=replay)
        )
    }
    assert len(asked) == 1 and asked[0].project == project and asked[0].seed == 4242
    assert sorted(asked[0].reach) == ["crane", "lighthouse"]
    assert ran == ["tests/test_lamp.gd"]
    assert found["Lamps light up"].status == "built"
    assert found["Storm plays back the same"].status == "broken"
    assert "diverged at frame 120" in found["Storm plays back the same"].evidence
    assert found["Prototype: The keeper reaches the lighthouse"].status == "broken"
    assert found["No errors in the first minute"].status == "broken"
    assert found["The harbour has a crane"].status == "missing"
    assert found["Fishing for fun"].status == "not_checked"
    # Kept: the next read shows the same marks and when they were found.
    again = {m.text: m for m in brief_status.marks(project)}
    assert again["Lamps light up"].status == "built" and again["Lamps light up"].checked
    assert again["Storm plays back the same"].status == "broken"
    # A changed line is a new line: not checked again.
    (project / "design/brief.md").write_text(
        BRIEF.replace("Lamps light up", "Lamps light up at dusk")
    )
    changed = {m.text: m for m in brief_status.marks(project)}
    assert changed["Lamps light up at dusk"].status == "not_checked"


def test_a_newer_playtest_wins_over_an_older_check(tmp_path: Path) -> None:
    project = _project(tmp_path)
    parsed = brief.parse(BRIEF)
    crane = parsed.features[1]
    (project / ".joinery").mkdir(exist_ok=True)
    (project / brief_status.STORE).write_text(
        json.dumps(
            {
                "items": {
                    crane.id: {
                        "status": "broken",
                        "evidence": "old",
                        "checked": "2026-11-01T10:00:00+00:00",
                        "source": "playtest",
                    }
                }
            }
        )
    )
    assert {m.text: m for m in brief_status.marks(project)}[
        crane.text
    ].evidence == "old"
    run = project / playtest.FOLDER / "later"
    run.mkdir(parents=True)
    report = _report(crane=True, lighthouse=True)
    report["reach"][1].update({"reached": True, "t": 12.0, "node": "Crane"})
    (run / "report.json").write_text(json.dumps(report))
    os.utime(run / "report.json", (1_800_000_000, 1_800_000_000))
    mark = {m.text: m for m in brief_status.marks(project)}[crane.text]
    assert mark.status == "built" and "reached Crane" in mark.evidence


def test_the_cli_prints_marks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    assert main(["brief", "--project", str(project), "--status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["headline"] == "1 missing · 6 not checked"
    assert data["features"][0]["word"] == "not checked"
    assert main(["brief", "--project", str(project), "--status"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("Brief features: 1 missing · 6 not checked")
    assert "missing      A boat to row  (test: res://tests/test_boat.gd" in text


def test_the_cli_without_a_brief(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["brief", "--project", str(tmp_path), "--status", "--json"]) == 1
    assert "no game brief" in json.loads(capsys.readouterr().out)["error"]
