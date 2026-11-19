"""Undo revert: a reverted change goes back as it was accepted, never over edits."""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from joinery import cli, timeline
from joinery.transactions import FileTransaction, RevertConflict


@pytest.fixture(autouse=True)
def _isolated_git(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No user or system git config: identity and hooks come only from the test."""
    home = tmp_path_factory.mktemp("git-home")
    config = home / "gitconfig"
    config.write_text("[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))


needs_git = pytest.mark.skipif(
    timeline.git_executable() is None, reason="git is not installed"
)


def _has_lfs() -> bool:
    if timeline.git_executable() is None:
        return False
    found = subprocess.run(["git", "lfs", "version"], capture_output=True, check=False)
    return found.returncode == 0


def git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True, check=True
    ).stdout


def make_project(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Yard"\n'
    )
    (path / "main.gd").write_text("extends Node\n")
    return path


def started(path: Path, lfs: bool = False) -> Path:
    project = make_project(path)
    git(project, "init", "-q")
    git(project, "config", "user.name", "Ada Lovelace")
    git(project, "config", "user.email", "ada@example.com")
    if lfs:
        (project / ".gitattributes").write_text(
            "*.glb filter=lfs diff=lfs merge=lfs -text\n"
            "*.blend filter=lfs diff=lfs merge=lfs -text\n"
        )
    timeline.start(project)
    return project


def accept(
    project: Path, summary: str, edits: dict[str, bytes | None]
) -> FileTransaction:
    tx = FileTransaction(project)
    tx.begin(summary)
    for path, data in edits.items():
        target = tx.stage / path
        if data is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    tx.review()
    tx.accept()
    return tx


def snapshot(project: Path, paths: list[str]) -> dict[str, bytes | None]:
    return {
        path: (project / path).read_bytes() if (project / path).is_file() else None
        for path in paths
    }


# --- The hedge's case: a lockfile, recipes, preimage and models ------------------

HEDGE_BEFORE: dict[str, bytes] = {
    "joinery.lock": b'[[asset]]\npath = "art/props/hedge.glb"\nrecipe = "3a7c"\n',
    "recipes/3a7c.toml": b'tool = "blender"\nsource = "art/sources/hedge.blend"\n',
    "art/props/hedge.glb": b"glTF\x02\x00flat hedge" + bytes(range(256)) * 8,
    "art/sources/hedge.blend": b"BLENDER-v400 flat" + bytes(range(256)) * 16,
}
HEDGE_CHANGE: dict[str, bytes | None] = {
    "joinery.lock": b'[[asset]]\npath = "art/props/hedge.glb"\nrecipe = "be2e"\n',
    "recipes/3a7c.toml": None,
    "recipes/be2e.py": b"# leaves sticking out\nleaves = 200\n",
    "recipes/be2e.toml": b'tool = "blender"\nscript = "recipes/be2e.py"\n',
    "recipes/history/3a7c.toml": HEDGE_BEFORE["recipes/3a7c.toml"],
    "recipes/inputs/d5c7.blend": HEDGE_BEFORE["art/sources/hedge.blend"],
    "art/props/hedge.glb": b"glTF\x02\x00leafy hedge" + bytes(range(255, -1, -1)) * 9,
    "art/sources/hedge.blend": b"BLENDER-v400 leafy" + bytes(range(255, -1, -1)) * 17,
}
HEDGE_PATHS = sorted({*HEDGE_BEFORE, *HEDGE_CHANGE})
SUMMARY = "Hedge has small leaves sticking out of its top and sides"


def hedge(project: Path, lfs: bool = False) -> FileTransaction:
    for path, data in HEDGE_BEFORE.items():
        (project / path).parent.mkdir(parents=True, exist_ok=True)
        (project / path).write_bytes(data)
    if (project / ".git").is_dir():
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "Hedge")
    return accept(project, SUMMARY, HEDGE_CHANGE)


# --- Clean undo -------------------------------------------------------------------


def test_undo_revert_puts_the_change_back_as_accepted(tmp_path: Path) -> None:
    project = make_project(tmp_path / "game")
    tx = hedge(project)
    accepted = snapshot(project, HEDGE_PATHS)
    tx.revert()
    assert snapshot(project, HEDGE_PATHS) == {
        **dict.fromkeys(HEDGE_PATHS),
        **HEDGE_BEFORE,
    }
    assert tx.redo.is_dir()

    loaded = FileTransaction.load(project, tx.id)
    loaded.unrevert()

    assert snapshot(project, HEDGE_PATHS) == accepted
    again = FileTransaction.load(project, tx.id)
    assert again.state == "accepted" and again.revertible
    assert [step["state"] for step in again.history] == [
        "accepted",
        "reverted",
        "accepted",
    ]
    assert again.history[-1]["by"] == "undo revert"
    assert not again.redo.exists()


def test_a_revert_of_an_undone_revert_works_again_and_again(tmp_path: Path) -> None:
    project = make_project(tmp_path / "game")
    tx = hedge(project)
    accepted = snapshot(project, HEDGE_PATHS)
    for _ in range(2):
        FileTransaction.load(project, tx.id).revert()
        assert (project / "recipes/3a7c.toml").read_bytes() == HEDGE_BEFORE[
            "recipes/3a7c.toml"
        ]
        assert not (project / "recipes/be2e.py").exists()
        FileTransaction.load(project, tx.id).unrevert()
        assert snapshot(project, HEDGE_PATHS) == accepted
    states = [step["state"] for step in FileTransaction.load(project, tx.id).history]
    assert states == ["accepted", "reverted", "accepted", "reverted", "accepted"]


def test_only_a_reverted_change_can_be_put_back(tmp_path: Path) -> None:
    project = make_project(tmp_path / "game")
    tx = hedge(project)
    with pytest.raises(ValueError, match="Only a reverted change"):
        tx.unrevert()


# --- Conflicts ----------------------------------------------------------------------


def test_a_file_edited_since_the_revert_is_never_replaced_silently(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path / "game")
    tx = hedge(project)
    accepted = snapshot(project, HEDGE_PATHS)
    tx.revert()
    mine = b"glTF\x02\x00my own sculpted hedge"
    (project / "art/props/hedge.glb").write_bytes(mine)
    reverted = snapshot(project, HEDGE_PATHS)

    with pytest.raises(RevertConflict) as caught:
        FileTransaction.load(project, tx.id).unrevert()
    assert caught.value.paths == ["art/props/hedge.glb"]
    assert "art/props/hedge.glb was changed since the revert" in str(caught.value)
    assert snapshot(project, HEDGE_PATHS) == reverted
    assert FileTransaction.load(project, tx.id).state == "reverted"

    # Replacing them saves the edit: a later Revert brings it back.
    FileTransaction.load(project, tx.id).unrevert(overwrite=True)
    assert snapshot(project, HEDGE_PATHS) == accepted
    FileTransaction.load(project, tx.id).revert()
    assert (project / "art/props/hedge.glb").read_bytes() == mine


def test_an_unreadable_lock_edited_since_is_a_conflict(tmp_path: Path) -> None:
    project = make_project(tmp_path / "game")
    tx = hedge(project)
    tx.revert()
    (project / "joinery.lock").write_bytes(b"[[asset]\nnot a lock")
    with pytest.raises(RevertConflict) as caught:
        FileTransaction.load(project, tx.id).unrevert()
    assert caught.value.paths == ["joinery.lock"]
    assert (project / "joinery.lock").read_bytes() == b"[[asset]\nnot a lock"


def test_a_lock_another_change_added_to_since_is_merged_entry_by_entry(
    tmp_path: Path,
) -> None:
    """The hedge's case: later changes added their own assets to joinery.lock after
    the revert. Its entries go back beside theirs; theirs are never dropped."""
    project = make_project(tmp_path / "game")
    tx = hedge(project)
    tx.revert()
    later = HEDGE_BEFORE["joinery.lock"] + (
        b'\n[[asset]]\npath = "art/props/trimmer.glb"\nrecipe = "77aa"\n'
    )
    (project / "joinery.lock").write_bytes(later)

    FileTransaction.load(project, tx.id).unrevert()

    lock = tomllib.loads((project / "joinery.lock").read_text())
    recipes = {entry["path"]: entry["recipe"] for entry in lock["asset"]}
    assert recipes == {"art/props/hedge.glb": "be2e", "art/props/trimmer.glb": "77aa"}
    assert (project / "recipes/be2e.py").read_bytes() == HEDGE_CHANGE["recipes/be2e.py"]
    # Reverted again, the lock is as it was just before: the trimmer's entry stays.
    FileTransaction.load(project, tx.id).revert()
    assert (project / "joinery.lock").read_bytes() == later


def test_the_command_line_names_the_conflict_and_its_choices(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path / "game")
    tx = hedge(project)
    tx.revert()
    (project / "recipes/3a7c.toml").write_bytes(b"tool = 'mine'\n")
    base = ["transactions", "unrevert", "--project", str(project), "--id", tx.id]
    assert cli.main(base) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["conflict"] == ["recipes/3a7c.toml"]
    assert out["choices"] == ["keep", "overwrite"]
    assert (project / "recipes/3a7c.toml").read_bytes() == b"tool = 'mine'\n"

    assert cli.main([*base, "--overwrite"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["transactions"][0]["state"] == "accepted"
    assert not (project / "recipes/3a7c.toml").exists()


def test_without_its_copies_or_a_timeline_nothing_changes(tmp_path: Path) -> None:
    project = make_project(tmp_path / "game")
    tx = hedge(project)
    tx.revert()
    shutil.rmtree(tx.redo)
    reverted = snapshot(project, HEDGE_PATHS)
    with pytest.raises(ValueError, match="Timeline doesn't either"):
        FileTransaction.load(project, tx.id).unrevert()
    assert snapshot(project, HEDGE_PATHS) == reverted
    assert FileTransaction.load(project, tx.id).state == "reverted"


# --- With the Timeline --------------------------------------------------------------


@needs_git
def test_undo_revert_is_its_own_timeline_entry(tmp_path: Path) -> None:
    project = started(tmp_path / "game")
    tx = hedge(project)
    tx.revert()
    revert_commit = git(project, "rev-parse", "HEAD").strip()
    listed = timeline.entries(project)["entries"]
    assert listed[0]["commit"] == revert_commit
    assert listed[0]["reverted_change"] == tx.id

    FileTransaction.load(project, tx.id).unrevert()
    assert git(project, "log", "-1", "--format=%s").strip() == "Restore: " + SUMMARY
    assert git(project, "log", "-1", "--format=%b").strip() == timeline.RESTORED_LINE
    assert git(project, "status", "--porcelain", "--", *HEDGE_PATHS) == ""
    # Put back, its revert entry no longer offers Undo revert.
    assert all("reverted_change" not in e for e in timeline.entries(project)["entries"])


@needs_git
@pytest.mark.skipif(not _has_lfs(), reason="git lfs is not installed")
def test_an_older_revert_comes_back_from_the_timeline_with_lfs(
    tmp_path: Path,
) -> None:
    """The hedge's revert ran before reverts kept their copies: the accepted
    lockfile, recipes, preimage and LFS models come from the Timeline."""
    project = started(tmp_path / "game", lfs=True)
    tx = hedge(project)
    accepted = snapshot(project, HEDGE_PATHS)
    assert git(project, "lfs", "ls-files", "-n").split() == [
        "art/props/hedge.glb",
        "art/sources/hedge.blend",
        "recipes/inputs/d5c7.blend",
    ]
    tx.revert()
    # As an older revert left it: no copies, no entry kept in its journal.
    shutil.rmtree(tx.redo)
    raw = json.loads(tx.journal.read_text())
    raw.pop("history")
    tx.journal.write_text(json.dumps(raw))
    revert_commit = git(project, "rev-parse", "HEAD").strip()
    shown = timeline.entries(project)["entries"]
    assert shown[0]["commit"] == revert_commit
    assert shown[0]["reverted_change"] == tx.id

    FileTransaction.load(project, tx.id).unrevert()

    assert snapshot(project, HEDGE_PATHS) == accepted
    assert git(project, "status", "--porcelain", "--", *HEDGE_PATHS) == ""
    assert git(project, "log", "-1", "--format=%s").strip() == "Restore: " + SUMMARY
    # The models stay LFS objects in the new entry.
    pointer = git(project, "cat-file", "blob", "HEAD:art/props/hedge.glb")
    assert pointer.startswith("version https://git-lfs.github.com/spec/")
    # And it can be reverted again.
    FileTransaction.load(project, tx.id).revert()
    assert (project / "art/props/hedge.glb").read_bytes() == HEDGE_BEFORE[
        "art/props/hedge.glb"
    ]


@needs_git
def test_a_revert_already_reverted_by_hand_is_just_marked_accepted(
    tmp_path: Path,
) -> None:
    """The user ran `git revert` of the revert before Undo revert existed."""
    project = started(tmp_path / "game")
    tx = hedge(project)
    accepted = snapshot(project, HEDGE_PATHS)
    tx.revert()
    shutil.rmtree(tx.redo)
    git(project, "revert", "--no-edit", "HEAD")
    head = git(project, "rev-parse", "HEAD").strip()
    assert snapshot(project, HEDGE_PATHS) == accepted

    FileTransaction.load(project, tx.id).unrevert()

    loaded = FileTransaction.load(project, tx.id)
    assert loaded.state == "accepted"
    assert snapshot(project, HEDGE_PATHS) == accepted
    # Nothing to commit: the files already were as accepted.
    assert git(project, "rev-parse", "HEAD").strip() == head
    # Its earlier files came from the Timeline, so Revert still works.
    assert loaded.revertible
    loaded.revert()
    assert (project / "joinery.lock").read_bytes() == HEDGE_BEFORE["joinery.lock"]
