"""A change in review is still accepted after another process accepted one first."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery.cli import _transaction_decision
from joinery.rebase import lock_moved
from joinery.transactions import FileTransaction

LOCK_A = 'version = 1\n\n[[asset]]\npath = "art/a.png"\nsha256 = "aa"\norigin = "tool-created"\ntool = "krita"\nrecipe = "recipes/a.toml"\n'
LOCK_B = 'version = 1\n\n[[asset]]\npath = "art/b.png"\nsha256 = "bb"\norigin = "tool-created"\ntool = "krita"\nrecipe = "recipes/b.toml"\n'


def _staged(project: Path, name: str, lock: str) -> FileTransaction:
    tx = FileTransaction(project)
    tx.begin(f"Paint {name}")
    (tx.stage / "art").mkdir(exist_ok=True)
    (tx.stage / f"art/{name}.png").write_text(name)
    (tx.stage / "joinery.lock").write_text(lock)
    tx.review()
    return tx


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.lock").write_text("version = 1\n")
    return tmp_path


def test_the_cli_accepts_a_change_whose_lock_another_accept_moved(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _staged(project, "a", LOCK_A)
    second = _staged(project, "b", LOCK_B)
    first.accept()
    assert lock_moved(second)
    assert _transaction_decision(project, second.id, True) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "accepted"
    lock = (project / "joinery.lock").read_text()
    assert 'path = "art/a.png"' in lock and 'path = "art/b.png"' in lock


def test_an_unmoved_lock_is_not_moved(project: Path) -> None:
    assert not lock_moved(_staged(project, "a", LOCK_A))
