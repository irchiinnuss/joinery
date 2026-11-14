"""`joinery share build` keeps the export settings it needs as a change for review."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from joinery import share_build
from joinery.transactions import list_transactions
from tests.test_share import PROJECT


def test_export_settings_wait_for_review_and_the_project_is_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text(PROJECT)
    seen: list[tuple[bool, str]] = []

    def fake_build(project: Path, *args: Any) -> dict[str, Any]:
        presets = project / share_build.PRESETS
        seen.append((presets.is_file(), (project / "project.godot").read_text()))
        return {"targets": {}, "changes": args[3]}

    monkeypatch.setattr(share_build, "_build", fake_build)
    report = share_build.build(project, ["web"], play=False)
    # The export ran with the settings in place...
    assert seen[0][0]
    # ...and afterwards the project is as it was; the settings wait as a change.
    assert not (project / share_build.PRESETS).exists()
    assert (project / "project.godot").read_text() == PROJECT
    waiting = [tx for tx in list_transactions(project) if tx.state == "review"]
    assert len(waiting) == 1 and waiting[0].held
    assert waiting[0].summary == share_build.EXPORT_CHANGE
    assert report["change"]["id"] == waiting[0].id
    assert "export_presets.cfg" in report["change"]["files"]
    assert (waiting[0].stage / share_build.PRESETS).is_file()

    # The same build again reuses the waiting change rather than piling up another.
    share_build.build(project, ["web"], play=False)
    again = [tx for tx in list_transactions(project) if tx.state == "review"]
    assert [tx.id for tx in again] == [waiting[0].id]

    # Accepted, the next build needs no change at all.
    again[0].accept()
    report = share_build.build(project, ["web"], play=False)
    assert "change" not in report
    assert (project / share_build.PRESETS).is_file()
