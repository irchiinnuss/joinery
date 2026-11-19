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


def test_joinerys_own_folder_is_hidden_from_godots_language_server(
    tmp_path: Path,
) -> None:
    from joinery import addon_refresh
    from joinery.transactions import FileTransaction

    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "player.gd").write_text("class_name Player\nextends Node\n")
    marker = tmp_path / ".joinery/.gdignore"
    # A change keeps copies of the game's scripts in .joinery/: Godot must not
    # take them for the project's classes.
    FileTransaction(tmp_path).begin("Faster player")
    assert marker.is_file()
    # An older project gets it as its editor opens (the add-on check).
    marker.unlink()
    addon_refresh.state(tmp_path)
    assert marker.is_file() and marker.read_text() == ""
    # Not a Godot project: nothing is written.
    elsewhere = tmp_path / "notes"
    elsewhere.mkdir()
    source_folders.hide_state(elsewhere)
    assert not (elsewhere / ".joinery").exists()


def _language_server_scripts(folder: Path, root: str = "res://") -> list[str]:
    """Godot's GDScript language server's scan (gdscript_workspace.cpp,
    list_script_files): every .gd, hidden folders too, unless a folder holds a
    .gdignore."""
    if (folder / ".gdignore").exists():
        return []
    found: list[str] = []
    for entry in sorted(folder.iterdir()):
        if entry.is_dir():
            found += _language_server_scripts(entry, f"{root}{entry.name}/")
        elif entry.name.endswith(".gd"):
            found.append(root + entry.name)
    return found


def test_no_copy_of_a_script_joinery_keeps_reaches_godots_language_server(
    tmp_path: Path,
) -> None:
    from joinery.transactions import FileTransaction

    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "game").mkdir()
    (tmp_path / "game/mower.gd").write_text("class_name PushMower\nextends Node\n")
    change = FileTransaction(tmp_path)
    change.begin("Louder mower")
    # The copies a change, an add-on update and a blueprint keep.
    for copy in (
        change.snapshot / "game/mower.gd",
        tmp_path / ".joinery/addon-backups/1794-bd47/chat_rewind.gd",
        tmp_path / ".joinery/backup/20261115-111423/game/mower.gd",
    ):
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text("class_name PushMower\nextends Node\n")
    source_folders.hide_state(tmp_path)
    assert _language_server_scripts(tmp_path) == ["res://game/mower.gd"]
    # Each copy folder has its own marker: losing the outer one exposes nothing.
    (tmp_path / ".joinery/.gdignore").unlink()
    assert _language_server_scripts(tmp_path) == ["res://game/mower.gd"]
    for name in source_folders.SCRIPT_COPIES:
        assert (tmp_path / ".joinery" / name / ".gdignore").is_file()
    # The stage is still a whole project Godot can open: no marker at its root.
    assert not (change.stage / ".gdignore").exists()
