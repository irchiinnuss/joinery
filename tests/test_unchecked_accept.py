"""A change a run kept before its checks ran compiles its scripts before an Accept."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from joinery import submission
from joinery.cli import main
from joinery.runner import Result
from joinery.transactions import FileTransaction

TEST = "tests/test_progression.gd"
SCRIPT = "game/doors.gd"
GAME_UNKNOWN = (
    "SCRIPT ERROR: Compile Error: Identifier not found: Game\n"
    "          at: GDScript::reload (res://tests/test_progression.gd:148)\n"
    'ERROR: Failed to load script "res://tests/test_progression.gd" with error '
    '"Compilation failed".'
)


@pytest.fixture
def kept(tmp_path: Path) -> FileTransaction:
    (tmp_path / "project.godot").write_text(
        'config_version=5\n\n[autoload]\n\nGame="*res://game/game.gd"\n'
    )
    tx = FileTransaction(tmp_path)
    tx.begin("Keycards and doors")
    (tx.stage / "tests").mkdir()
    (tx.stage / TEST).write_text("extends SceneTree\n\nfunc _init() -> void:\n\tGame\n")
    (tx.stage / "game").mkdir()
    (tx.stage / SCRIPT).write_text("extends Node\n\nfunc _ready() -> void:\n\tGame\n")
    tx.unchecked = True
    tx.review()
    tx.hold("joinery run")
    return tx


def worker(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, Result]
) -> list[list[str]]:
    ran: list[list[str]] = []

    async def run(argv: list[str], _root: Path, _seconds: int, **_k: Any) -> Result:
        ran.append(argv)
        return answers.get(argv[-1], Result(True, "", "", 0.0))

    monkeypatch.setattr(submission, "worker_run", run)
    return ran


def accept(tx: FileTransaction, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    code = main(["transactions", "accept", "--project", str(tx.project), "--id", tx.id])
    data: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert (code == 0) == ("error" not in data)
    return data


def test_a_test_that_does_not_compile_keeps_the_change_waiting(
    kept: FileTransaction,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The game script names the autoload too, but the game adds it before
    # loading that script: only the SceneTree test fails.
    ran = worker(
        monkeypatch,
        {
            "res://" + TEST: Result(False, GAME_UNKNOWN, "", 0.0),
            "res://" + SCRIPT: Result(
                False, GAME_UNKNOWN.replace(TEST, SCRIPT), "", 0.0
            ),
        },
    )
    refused = accept(kept, capsys)["error"]
    assert "stopped before Joinery checked it" in refused
    assert f"res://{TEST}" in refused and "Identifier not found: Game" in refused
    assert SCRIPT not in refused
    assert "Game is an autoload" in refused
    assert "--import" in ran[0]  # the stage's classes are known first
    assert ["--check-only", "-s"] == ran[1][4:6]
    again = FileTransaction.load(kept.project, kept.id)
    assert again.state == "review" and again.unchecked
    assert not (kept.project / TEST).exists()
    # Fixed (it compiles now): accepted, and checked once only.
    worker(monkeypatch, {})
    done = accept(again, capsys)
    assert done["state"] == "accepted"
    assert (kept.project / TEST).is_file()


def test_without_the_worker_an_unchecked_change_is_not_accepted(
    kept: FileTransaction,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    down = Result(False, "", "Cannot connect to the Docker daemon", 0.0)
    worker(monkeypatch, {"--joinery-import-only": down})
    refused = accept(kept, capsys)["error"]
    assert "couldn't be checked now" in refused and "Start Docker" in refused
    assert FileTransaction.load(kept.project, kept.id).state == "review"


def test_a_checked_change_is_accepted_without_compiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    tx = FileTransaction(tmp_path)
    tx.begin("A script")
    (tx.stage / "a.gd").write_text("extends Node\n")
    tx.review()
    ran = worker(monkeypatch, {})
    assert accept(tx, capsys)["state"] == "accepted"
    assert ran == []
