"""/cut: one polish list from every check, proposed cuts with reasons, one reviewed change."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from bridge import cut as cut_bridge
from joinery import brief, checks_core, cut, playtest
from joinery.cli import main
from joinery.transactions import FileTransaction

BRIEF = """# Game brief — Lamplight

## 17. Scope and milestones
- Team size: one

| Milestone | Done when | Target |
|---|---|---|
| Prototype | _Not decided yet._ | _Not decided yet._ |

- Out of scope: _Not decided yet._

## 18. Features
One feature per line.

- The harbour has a crane (reach: crane)
- Lamps light up (test: res://tests/test_lamp.gd)
- A boat to row (test: res://tests/test_boat.gd)
- Fishing at the pier (reach: pier)
- Storm waves
- Jumping between roofs (test: res://tests/test_roofs.gd)

## 19. Keep
- Jumping feels light
- Jump height 1.2 m

## 20. Avoid
None yet.

## 22. Facts (checked)
```toml
schema = 1
goal = "sell"
target_date = "2027-01-14"
```
"""


def _project(root: Path, text: str = BRIEF) -> Path:
    (root / "design").mkdir(parents=True, exist_ok=True)
    (root / "design/brief.md").write_text(text, encoding="utf-8")
    (root / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Lamplight"\n'
    )
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests/test_lamp.gd").write_text("extends SceneTree\n")
    run = root / playtest.FOLDER / "20261115-140200-abc123"
    run.mkdir(parents=True)
    report = {
        "seed": 4242,
        "played_seconds": 60.0,
        "items": [
            {
                "kind": "crash",
                "severity": "critical",
                "title": "Crash",
                "detail": "fishing rod is null",
                "where": "res://pier/fishing.gd:12",
                "t": 41.0,
            },
            {
                "kind": "pause",
                "severity": "medium",
                "title": "Pause menu didn't open",
                "detail": "",
            },
        ],
        "reach": [
            {"target": "crane", "found": False, "reached": False},
            {
                "target": "pier",
                "found": True,
                "reached": False,
                "closest": 4.0,
                "node": "Pier",
            },
        ],
    }
    (run / "report.json").write_text(json.dumps(report))
    return root


def _access(root: Path) -> None:
    checks_core.save(
        root,
        checks_core.Report(
            kind="access",
            project=str(root),
            findings=[
                checks_core.Finding(
                    id="a1",
                    area="text",
                    path="ui/hud.tscn",
                    subject="Score",
                    check="text-size",
                    severity="medium",
                    measured="11 px",
                    target="16 px",
                    message="The score text is small",
                )
            ],
        ),
    )


def _plan(root: Path) -> cut.Plan:
    return cut.plan(root, today=date(2026, 11, 15))


def test_the_polish_list_gathers_every_source_in_priority_order(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _access(project)
    found = _plan(project)
    sources = [(r.priority, r.source, r.title) for r in found.rows]
    assert sources[0] == ("high", "playtest", "Crash")
    assert ("high", "brief", "Fix a broken feature: Fishing at the pier") in sources
    assert ("medium", "access", "The score text is small") in sources
    assert ("medium", "playtest", "Pause menu didn't open") in sources
    assert ("low", "brief", "Nothing checks “Storm waves”") in sources
    order = [cut.PRIORITY_ORDER[p] for p, _, _ in sources]
    assert order == sorted(order)
    assert "playtest 20261115-140200-abc123" in found.read
    assert "accessibility check" in found.read and "design check" in found.read
    crash = found.rows[0]
    assert crash.where == "res://pier/fishing.gd:12 at 0:41" and "null" in crash.detail


def test_cuts_have_reasons_and_never_touch_keep(tmp_path: Path) -> None:
    found = _plan(_project(tmp_path))
    reasons = {c.text: c.reason for c in found.cuts}
    assert reasons == {
        "The harbour has a crane": "scope",
        "A boat to row": "scope",
        "Fishing at the pier": "broken",
    }
    # Missing, but a Keep line covers it.
    assert found.kept == ["Jumping between roofs"]
    assert all("Jumping" not in c.text for c in found.cuts)
    assert "Lamps light up" not in reasons  # not checked and nothing points at it


def test_a_risky_feature_is_one_the_worst_findings_name(tmp_path: Path) -> None:
    text = BRIEF.replace("- Storm waves", "- Fishing rod")
    found = _plan(_project(tmp_path, text))
    risky = next(c for c in found.cuts if c.text == "Fishing rod")
    assert risky.reason == "risk" and risky.why == "playtest: Crash"


def test_a_ship_estimate_only_when_selling(tmp_path: Path) -> None:
    found = _plan(_project(tmp_path))
    assert found.ship is not None
    assert found.ship.freeze == "2026-12-27" and found.ship.days_left == 60
    assert found.ship.text.startswith(
        "Freeze new content by 2026-12-27 (70% of the 60 days left)"
    )
    hobby = _plan(
        _project(tmp_path / "hobby", BRIEF.replace('goal = "sell"', 'goal = "hobby"'))
    )
    assert hobby.ship is None
    undated = _plan(
        _project(
            tmp_path / "undated", BRIEF.replace('target_date = "2027-01-14"\n', "")
        )
    )
    assert undated.ship is not None and undated.ship.text.startswith("Set target_date")


def test_writing_moves_cuts_to_out_of_scope_and_deletes_nothing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    found = _plan(project)
    chosen = cut.choose(found, [c.id for c in found.cuts if c.reason == "scope"])
    stage = tmp_path / "stage"
    (stage / "design").mkdir(parents=True)
    (stage / "design/brief.md").write_text(BRIEF)
    written = cut.apply(stage, found, chosen, today=date(2026, 11, 15))
    assert written == ["design/polish.md", "design/brief.md"]
    after = (stage / "design/brief.md").read_text()
    assert "- The harbour has a crane (reach: crane)" not in after
    assert (
        "- Out of scope:\n  - The harbour has a crane (cut 2026-11-15: promised but not built)\n"
        in after
    )
    assert "  - A boat to row (cut 2026-11-15: promised but not built)" in after
    assert "- Fishing at the pier (reach: pier)" in after  # not chosen, stays
    assert "_Not decided yet._\n\n## 18" not in after.split("Out of scope")[1][:40]
    parsed = brief.parse(after)
    assert [f.text for f in parsed.features if f.where == "features"] == [
        "Lamps light up",
        "Fishing at the pier",
        "Storm waves",
        "Jumping between roofs",
    ]
    polish = (stage / "design/polish.md").read_text()
    assert polish.startswith("# Polish list — Lamplight\n")
    assert (
        "## High" in polish
        and "- [ ] Crash — res://pier/fishing.gd:12 at 0:41 (playtest)" in polish
    )
    assert (
        "## Cut from the brief" in polish
        and "A boat to row: promised but not built" in polish
    )
    assert (
        "## Kept (never cut)\n\n- Jumping between roofs: a Keep line covers it"
        in polish
    )
    assert "## Ship estimate" in polish


def test_a_keep_feature_or_stale_id_is_refused(tmp_path: Path) -> None:
    found = _plan(_project(tmp_path))
    roofs = next(
        f for f in brief.parse(BRIEF).features if f.text == "Jumping between roofs"
    )
    with pytest.raises(ValueError, match="Keep features are never cut"):
        cut.choose(found, [roofs.id])


def test_the_cli_reports_and_stages_one_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    assert main(["cut", "--project", str(project)]) == 0
    text = capsys.readouterr().out
    assert text.startswith("Lamplight: ")
    assert "Proposed cuts (nothing is cut until you accept the change):" in text
    assert "Never cut (Keep): Jumping between roofs" in text
    assert main(["cut", "--project", str(project), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {c["reason"] for c in data["cuts"]} == {"scope", "broken"}
    broken = next(c["id"] for c in data["cuts"] if c["reason"] == "broken")
    assert (
        main(["cut", "--project", str(project), "--write", "--cut", broken, "--json"])
        == 0
    )
    staged = json.loads(capsys.readouterr().out)
    assert staged["state"] == "review" and staged["cuts"] == [broken]
    assert sorted(staged["files"]) == ["design/brief.md", "design/polish.md"]
    # Nothing moved in the project until the change is accepted.
    assert not (project / "design/polish.md").exists()
    assert "Fishing at the pier" in (project / "design/brief.md").read_text()
    tx = FileTransaction.load(project, staged["transaction_id"])
    tx.reject()


def test_the_polish_list_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    assert (
        main(
            [
                "cut",
                "--project",
                str(project),
                "--write",
                "--no-cuts",
                "--yes",
                "--json",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["state"] == "accepted" and data["files"] == ["design/polish.md"]
    assert (project / "design/polish.md").is_file()
    assert (project / "design/brief.md").read_text() == BRIEF


def test_without_a_brief_it_still_lists_findings(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "design/brief.md").unlink()
    found = _plan(project)
    assert found.rows and not found.cuts and found.ship is None
    assert any("no design/brief.md" in s for s in found.skipped)


class _Crew:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []

    async def start_scripted(
        self, title: str, request: str, calls: list[Any], **kw: Any
    ) -> Any:
        self.started.append({"title": title, "request": request, **kw})
        return None


def test_the_host_writes_only_the_cuts_the_user_kept(tmp_path: Path) -> None:
    project = _project(tmp_path)
    found = _plan(project)
    crane = next(c.id for c in found.cuts if c.text == "The harbour has a crane")
    crew = _Crew()
    asyncio.run(cut_bridge.start(crew, project, {"cuts": [crane]}, prompt=None))  # type: ignore[arg-type]
    started = crew.started[0]
    assert started["title"] == "Polish list and 1 cut from the brief"
    assert started["targets"] == ["design/polish.md", "design/brief.md"]
    stage = tmp_path / "stage"
    (stage / "design").mkdir(parents=True)
    (stage / "design/brief.md").write_text(BRIEF)
    started["edit"](stage)
    after = (stage / "design/brief.md").read_text()
    assert "The harbour has a crane (cut" in after and "A boat to row (test:" in after
    with pytest.raises(ValueError):
        asyncio.run(cut_bridge.start(crew, project, {"cuts": ["nope"]}, prompt=None))  # type: ignore[arg-type]
