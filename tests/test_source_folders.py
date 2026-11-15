"""Source-only folders get a .gdignore, unless the game loads a file from one."""

from __future__ import annotations

from pathlib import Path

from joinery import source_folders


def test_marks_source_folders_but_never_one_the_game_loads_from(tmp_path: Path) -> None:
    for folder in ("recipes", "art/sources/maps/door", "audio/sources", "art/ui"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "audio/sources/hit.wav").write_bytes(b"RIFF")
    # A script loads a raw render straight from audio/sources/.
    (tmp_path / "game").mkdir()
    (tmp_path / "game/hud.gd").write_text(
        'extends Node\nvar hit := preload("res://audio/sources/hit.wav")\n'
    )
    # A recipe's own script naming sources counts for nothing: Godot skips recipes/.
    (tmp_path / "recipes/door.gd").write_text('"res://art/sources/maps/door/x.png"')
    assert source_folders.missing(tmp_path) == [
        "recipes/.gdignore",
        "art/sources/.gdignore",
    ]
    written, held = source_folders.mark(tmp_path)
    assert written == ["recipes/.gdignore", "art/sources/.gdignore"]
    assert held == {"audio/sources": ["audio/sources/hit.wav"]}
    assert (tmp_path / "art/sources/.gdignore").read_text() == ""
    assert not (tmp_path / "audio/sources/.gdignore").exists()
    assert not (tmp_path / "art/ui/.gdignore").exists()
    # Marked once: a second pass writes nothing.
    assert source_folders.mark(tmp_path)[0] == []


def test_source_folder_and_ignored_folder(tmp_path: Path) -> None:
    assert source_folders.source_folder("res://art/sources/a.blend") == "art/sources"
    assert source_folders.source_folder("art/sourcesx/a.png") == ""
    assert source_folders.source_folder("art/models/a.glb") == ""
    (tmp_path / "art/sources/maps").mkdir(parents=True)
    (tmp_path / "art/sources/.gdignore").write_text("")
    assert source_folders.ignored_folder(tmp_path, "art/sources/maps/a.png") == (
        "art/sources"
    )
    assert source_folders.ignored_folder(tmp_path, "art/models/a.glb") == ""
