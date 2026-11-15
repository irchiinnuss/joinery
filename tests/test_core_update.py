"""`joinery update-core`: Joinery-owned game files updated as one reviewed change,
never over a file the user or an agent edited."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from joinery import blueprints, core_update
from joinery.bootstrap import update_addon
from joinery.transactions import FileTransaction

REPLAYS = "core/replays/replays.gd"
STATE = "core/replays/replay_state.gd"
SCRIPT = core_update.BLOCKOUT_SCRIPT
OLD = b"# the version an older Joinery installed\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _installed_as(project: Path, rel: str, data: bytes) -> None:
    """As if Joinery had installed `data` at `rel`, and nobody touched it since."""
    (project / rel).parent.mkdir(parents=True, exist_ok=True)
    (project / rel).write_bytes(data)
    path = project / blueprints.MANIFEST
    manifest = json.loads(path.read_text())
    manifest["units"][core_update.CORE_UNIT]["files"][rel] = _sha(data)
    path.write_text(json.dumps(manifest))


@pytest.fixture
def game(tmp_path: Path) -> Path:
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    blueprints.apply(project, "blank")
    # Two core files are older copies; the user edited one more.
    _installed_as(project, REPLAYS, OLD)
    _installed_as(project, STATE, OLD)
    (project / STATE).write_bytes(OLD + b"# my own fix\n")
    # The blockout script: installed by an older blockout, recorded there.
    (project / SCRIPT).parent.mkdir(parents=True)
    (project / SCRIPT).write_bytes(OLD)
    core_update.remember(project, project, {SCRIPT: OLD})
    return project


def test_unedited_files_update_and_edited_ones_are_listed_and_kept(game: Path) -> None:
    found = core_update.plan(game)
    assert found.update == [REPLAYS, SCRIPT]
    assert found.edited == [STATE] and found.create == []
    tx = core_update.stage(game, found=found)
    assert tx is not None and tx.state == "review"
    assert tx.summary == core_update.SUMMARY
    # One change for review; nothing in the project moved yet.
    assert sorted(c["path"] for c in tx.changes) == sorted([SCRIPT, REPLAYS])
    assert (game / REPLAYS).read_bytes() == OLD
    tx.accept()
    assert (game / REPLAYS).read_bytes() == found.files[REPLAYS]
    assert (game / SCRIPT).read_bytes() == found.files[SCRIPT]
    assert (game / STATE).read_bytes() == OLD + b"# my own fix\n"
    # The new versions are recorded as installed: the core in the game
    # manifest, the blockout script in core-files.json.
    assert core_update.installed(game)[REPLAYS] == _sha(found.files[REPLAYS])
    assert core_update.installed(game)[SCRIPT] == _sha(found.files[SCRIPT])
    again = core_update.plan(game)
    assert again.update == [] and again.edited == [STATE]
    assert core_update.stage(game) is None


def test_force_replaces_edited_files_too_and_reject_leaves_them(game: Path) -> None:
    tx = core_update.stage(game, force=True)
    assert tx is not None and STATE in [c["path"] for c in tx.changes]
    tx.reject()
    assert (game / STATE).read_bytes() == OLD + b"# my own fix\n"
    assert (game / REPLAYS).read_bytes() == OLD


def test_a_file_installed_before_hashes_were_kept_counts_as_edited(
    game: Path,
) -> None:
    (game / core_update.RECORD).unlink()
    found = core_update.plan(game)
    # No record and no git history: Joinery can't tell, so it keeps the file.
    assert SCRIPT in found.edited


def test_without_a_record_the_first_commit_is_the_installed_version(
    game: Path,
) -> None:
    (game / core_update.RECORD).unlink()
    git = ["git", "-C", str(game), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", SCRIPT], check=True)
    subprocess.run([*git, "commit", "-qm", "blockout"], check=True)
    assert SCRIPT in core_update.plan(game).update
    # Edited and committed since: kept.
    (game / SCRIPT).write_bytes(OLD + b"func complete() -> void:\n\tpass\n")
    subprocess.run([*git, "commit", "-qam", "an exit switch"], check=True)
    assert SCRIPT in core_update.plan(game).edited


def test_a_deleted_file_stays_deleted_and_a_new_one_is_added(game: Path) -> None:
    (game / REPLAYS).unlink()
    manifest = json.loads((game / blueprints.MANIFEST).read_text())
    files = manifest["units"][core_update.CORE_UNIT]["files"]
    gone = next(rel for rel in files if rel.endswith(".gd") and rel != REPLAYS)
    del files[gone]
    (game / blueprints.MANIFEST).write_text(json.dumps(manifest))
    (game / gone).unlink()
    found = core_update.plan(game)
    assert REPLAYS in found.deleted and gone in found.create


def test_source_folders_get_their_gdignore_unless_the_game_loads_from_them(
    game: Path,
) -> None:
    for folder in ("recipes", "art/sources/maps", "audio/sources"):
        (game / folder).mkdir(parents=True)
    (game / "audio/sources/hit.wav").write_bytes(b"RIFF")
    (game / "hit.gd").write_text('const HIT = preload("res://audio/sources/hit.wav")\n')
    found = core_update.plan(game)
    assert "recipes/.gdignore" in found.create
    assert "art/sources/.gdignore" in found.create
    assert "audio/sources/.gdignore" not in found.files
    assert found.unmarked == {"audio/sources": ["audio/sources/hit.wav"]}
    assert any(
        line.startswith("Leaves audio/sources/ without a .gdignore: the game loads ")
        for line in core_update._words(found, False)
    )
    tx = core_update.stage(game, found=found)
    assert tx is not None
    tx.accept()
    assert (game / "art/sources/.gdignore").read_bytes() == b""
    again = core_update.plan(game)
    assert "art/sources/.gdignore" not in again.create


def test_the_command_lists_and_stages(
    game: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = argparse.Namespace(project=game, check=True, force=False, json=True)
    assert core_update.main(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["update"] == [REPLAYS, SCRIPT] and data["edited"] == [STATE]
    assert "transaction_id" not in data
    args.check = False
    assert core_update.main(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert FileTransaction.load(game, data["transaction_id"]).state == "review"
    assert any("pass --force" in line for line in data["words"])


def test_the_add_on_update_offers_it_once(
    game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("joinery.bootstrap.editor_running", lambda _project: False)
    update_addon(game)
    (game / "addons/joinery/plugin.gd").write_text("# an older add-on\n")
    done = update_addon(game)
    assert done["state"] == "updated" and done["core"] == "staged"
    assert done["core_kept"] == STATE
    tx = FileTransaction.load(game, done["core_transaction"])
    assert tx.summary == core_update.SUMMARY and tx.state == "review"
    # The next refresh finds it still waiting and doesn't stage another.
    (game / "addons/joinery/plugin.gd").write_text("# an older add-on\n")
    assert update_addon(game)["core"] == "pending"


def test_the_blockout_records_the_script_it_writes(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    project = tmp_path / "project"
    stage.mkdir()
    project.mkdir()
    core_update.remember(project, project, {"other.gd": b"x"})
    core_update.remember(stage, project, {SCRIPT: b"script"})
    data = json.loads((stage / core_update.RECORD).read_text())
    # The stage's record keeps what the project's had (accept carries it in).
    assert data["files"] == {"other.gd": _sha(b"x"), SCRIPT: _sha(b"script")}


def test_feature_overlay_files_update_too_and_edited_ones_are_kept(
    tmp_path: Path,
) -> None:
    project = tmp_path / "fps"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    blueprints.apply(project, "first-person-3d", ["behaviour"])
    agent = "features/behaviour/behaviour_agent.gd"
    checks = "tests/features/behaviour/behaviour_checks.gd"
    path = project / blueprints.MANIFEST
    manifest = json.loads(path.read_text())
    for rel in (agent, checks):
        (project / rel).write_bytes(OLD)
        manifest["units"]["behaviour"]["files"][rel] = _sha(OLD)
    path.write_text(json.dumps(manifest))
    (project / checks).write_bytes(OLD + b"# my own check\n")
    found = core_update.plan(project)
    assert agent in found.update and checks in found.edited
    tx = core_update.stage(project, found=found)
    assert tx is not None
    tx.accept()
    assert (project / agent).read_bytes() == found.files[agent]
    assert (project / checks).read_bytes() == OLD + b"# my own check\n"
    # Recorded under the feature's own unit, so the next update knows it.
    units = json.loads(path.read_text())["units"]
    assert units["behaviour"]["files"][agent] == _sha(found.files[agent])
    assert agent not in units[core_update.CORE_UNIT]["files"]
    assert core_update.plan(project).update == []
