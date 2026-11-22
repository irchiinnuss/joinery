"""Which paired device a decision came from (ADR 0093): named for that decision only."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridge.stdio import deciding_from


def lane(tx_id: str = "tx1") -> Any:
    return SimpleNamespace(
        transaction=SimpleNamespace(id=tx_id, decided_from="", decided_via="")
    )


def test_a_failed_decision_leaves_no_device_behind() -> None:
    """A phone's Accept that fails must not be credited to the next decision."""
    gateway = lane()
    data = {"transaction_id": "tx1", "decided_from": "Ana's iPhone"}
    with pytest.raises(ValueError), deciding_from(gateway, data):
        assert gateway.transaction.decided_from == "Ana's iPhone"
        raise ValueError("The scene isn't saved.")
    assert gateway.transaction.decided_from == ""


def test_the_device_is_named_while_its_decision_runs_only() -> None:
    gateway = lane()
    data = {"transaction_id": "tx1", "decided_from": "Ana's iPhone"}
    with deciding_from(gateway, data):
        assert gateway.transaction.decided_from == "Ana's iPhone"
    assert gateway.transaction.decided_from == ""
    # Another change than the one in review is never named.
    other = lane("tx2")
    with deciding_from(other, data):
        assert other.transaction.decided_from == ""


def test_the_journal_says_decided_via_the_device_id(tmp_path: Path) -> None:
    """A name can be reused or changed: the history keeps `decided_via:
    remote:<device id>` beside it, for an Accept and a Discard alike."""
    from joinery.transactions import FileTransaction

    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    for accept in (True, False):
        tx = FileTransaction(project)
        tx.begin("Taller hedge")
        (tx.stage / "hedge.gd").write_text(f"extends Node # {accept}\n")
        tx.review()
        gateway = SimpleNamespace(transaction=tx)
        data = {
            "transaction_id": tx.id,
            "decided_from": "Ana's iPhone",
            "decided_via": "remote:d_1a2b3c",
        }
        with deciding_from(gateway, data):
            if accept:
                tx.accept()
            else:
                tx.reject()
        entry = tx.history[-1]
        assert entry["from"] == "Ana's iPhone"
        assert entry["decided_via"] == "remote:d_1a2b3c"
        assert tx.decided_via == ""


def test_only_a_device_id_is_kept_as_decided_via() -> None:
    gateway = lane()
    data = {"transaction_id": "tx1", "decided_via": "remote:../../etc"}
    with deciding_from(gateway, data):
        assert gateway.transaction.decided_via == ""
