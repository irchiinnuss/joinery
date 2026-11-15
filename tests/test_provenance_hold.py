"""Assets without provenance leave a held change; the gate itself never relaxes."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from joinery import provenance_hold


def _lock(root: Path, *paths: str) -> None:
    blocks = []
    for path in paths:
        digest = hashlib.sha256((root / path).read_bytes()).hexdigest()
        blocks.append(
            f'\n[[asset]]\npath = "{path}"\nsha256 = "{digest}"\n'
            'origin = "human-import"\nimported_by = "user"\nlicence = "own work"\n'
        )
    (root / "joinery.lock").write_text("version = 1\n" + "".join(blocks))


def test_blocked_files_go_back_to_the_projects_version(tmp_path: Path) -> None:
    project, stage = tmp_path / "project", tmp_path / "stage"
    (project / "art").mkdir(parents=True)
    (project / "art/wall.png").write_bytes(b"old wall")
    _lock(project, "art/wall.png")
    shutil.copytree(project, stage)
    # The change re-rendered the wall and made a gem; neither got a record.
    (stage / "art/wall.png").write_bytes(b"half a new wall")
    (stage / "art/new").mkdir()
    (stage / "art/new/gem.png").write_bytes(b"half a gem")
    (stage / "art/new/gem.png.import").write_text("[remap]\n")
    blocked = ["art/new/gem.png", "art/wall.png"]
    assert provenance_hold.block(stage, project, blocked) == []
    assert (stage / "art/wall.png").read_bytes() == b"old wall"
    assert not (stage / "art/new").exists()
    assert "art/wall.png" in (stage / "joinery.lock").read_text()
    line = provenance_hold.note(blocked)
    assert line.startswith("Blocked, not part of this change: art/new/gem.png")
    assert "Redo exactly these renders" in line


def test_a_stage_that_still_fails_is_reported(tmp_path: Path) -> None:
    project, stage = tmp_path / "project", tmp_path / "stage"
    project.mkdir()
    (project / "joinery.lock").write_text("version = 1\n")
    shutil.copytree(project, stage)
    (stage / "art").mkdir()
    (stage / "art/gem.png").write_bytes(b"gem")
    (stage / "art/other.png").write_bytes(b"also unrecorded")
    remaining = provenance_hold.block(stage, project, ["art/gem.png"])
    assert remaining == ["art/other.png: no joinery.lock entry"]
