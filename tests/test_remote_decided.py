"""Which paired device a decision came from (ADR 0093): named for that decision only."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bridge.stdio import deciding_from


def lane(tx_id: str = "tx1") -> Any:
    return SimpleNamespace(transaction=SimpleNamespace(id=tx_id, decided_from=""))


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
