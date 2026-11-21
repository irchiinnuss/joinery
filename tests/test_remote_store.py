"""Paired devices, pairing codes and the audit log (joinery.remote.store/audit)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from joinery.remote import audit
from joinery.remote.store import (
    DAY,
    LOCK_SECONDS,
    MAX_FAILURES,
    PAIR_SECONDS,
    PairRefused,
    Store,
    digest,
    enabled,
)

NOW = 1_800_000_000.0


@pytest.fixture
def store(tmp_path: Path) -> Store:
    home = tmp_path / "remote"
    home.mkdir(mode=0o700)
    return Store(home)


def paired(store: Store, now: float = NOW, scopes: tuple[str, ...] = ()) -> str:
    """A device paired at ``now``; returns its token."""
    made = store.new_code(scopes or ("watch", "chat", "decide"), now=now)
    token = "token-" + made["pairing_id"]
    request = store.redeem(
        made["code"], "Ana's iPhone", "iOS", digest(token), digest("s"), now=now
    )
    store.answer(request["id"], True, now=now)
    return token


def test_code_is_single_use_and_machine_confirmed(store: Store) -> None:
    made = store.new_code(now=NOW)
    assert len(made["code"].replace("-", "")) == 10
    request = store.redeem(
        made["code"].lower(), "Phone", "", digest("t"), digest("s"), now=NOW
    )
    # Nothing is paired until the machine says so.
    assert store.devices() == []
    assert store.authenticate("t", now=NOW) is None
    assert [r["id"] for r in store.requests(made["pairing_id"], now=NOW)] == [
        request["id"]
    ]
    with pytest.raises(PairRefused, match="already used"):
        store.redeem(made["code"], "Other", "", digest("u"), digest("s"), now=NOW)
    store.answer(request["id"], True, now=NOW)
    assert store.authenticate("t", now=NOW)["name"] == "Phone"
    with pytest.raises(ValueError):
        store.answer(request["id"], True, now=NOW)


def test_denied_request_pairs_nothing(store: Store) -> None:
    made = store.new_code(now=NOW)
    request = store.redeem(made["code"], "P", "", digest("t"), digest("s"), now=NOW)
    store.answer(request["id"], False, now=NOW)
    assert store.authenticate("t", now=NOW) is None
    assert store.request_state(request["id"], "s")["state"] == "denied"
    # The secret is needed to see a request's state.
    assert store.request_state(request["id"], "wrong")["state"] == "unknown"


def test_code_expires_after_five_minutes(store: Store) -> None:
    made = store.new_code(now=NOW)
    with pytest.raises(PairRefused, match="expired"):
        store.redeem(
            made["code"], "P", "", digest("t"), digest("s"), now=NOW + PAIR_SECONDS
        )
    made = store.new_code(now=NOW)
    request = store.redeem(made["code"], "P", "", digest("t"), digest("s"), now=NOW)
    with pytest.raises(ValueError, match="expired"):
        store.answer(request["id"], True, now=NOW + PAIR_SECONDS + 1)


def test_wrong_codes_lock_pairing(store: Store) -> None:
    made = store.new_code(now=NOW)
    for _ in range(MAX_FAILURES):
        with pytest.raises(PairRefused):
            store.redeem("WRONGCODE1", "P", "", digest("t"), digest("s"), now=NOW)
    # Locked now, even for the right code.
    with pytest.raises(PairRefused, match="locked"):
        store.redeem(made["code"], "P", "", digest("t"), digest("s"), now=NOW + 1)
    assert store.locked_until(now=NOW + 1) == NOW + LOCK_SECONDS
    later = NOW + LOCK_SECONDS + 1
    fresh = store.new_code(now=later)
    assert store.redeem(fresh["code"], "P", "", digest("t"), digest("s"), now=later)


def test_only_hashes_are_stored(store: Store) -> None:
    token = paired(store)
    raw = store.path.read_text()
    assert token not in raw
    assert digest(token) in raw
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert "token_sha256" not in json.dumps(store.devices())


def test_expiry_unused_and_absolute(store: Store) -> None:
    token = paired(store)
    assert store.authenticate(token, now=NOW + 29 * DAY) is not None
    # Seen on day 29: good until day 59 unused, but never past day 90.
    assert store.authenticate(token, now=NOW + 58 * DAY) is not None
    assert store.authenticate(token, now=NOW + 87 * DAY) is not None
    assert store.authenticate(token, now=NOW + 90 * DAY) is None
    other = paired(store, now=NOW)
    assert store.authenticate(other, now=NOW + 31 * DAY) is None


def test_revoke_and_forget(store: Store) -> None:
    token = paired(store)
    device = store.authenticate(token, now=NOW)
    assert device is not None
    store.revoke(device["id"], now=NOW)
    assert store.authenticate(token, now=NOW) is None
    assert not store.active(device["id"], now=NOW)
    assert store.devices(now=NOW)[0]["state"] == "revoked"
    paired(store)
    assert store.forget_all() == 2
    assert store.devices() == []


def test_scopes_are_kept_and_editable(store: Store) -> None:
    token = paired(store, scopes=("watch",))
    device = store.authenticate(token, now=NOW)
    assert device is not None and device["scopes"] == ["watch"]
    store.set_scopes(device["id"], ["watch", "chat"])
    assert store.authenticate(token, now=NOW)["scopes"] == ["watch", "chat"]
    with pytest.raises(ValueError):
        store.set_scopes(device["id"], ["root"])
    with pytest.raises(ValueError):
        store.new_code(("admin",))


def test_enabled(store: Store) -> None:
    assert not enabled(store.home)
    store.set_config(enabled=True)
    assert enabled(store.home)
    assert not enabled(store.home / "missing")


def test_audit(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "game"
    (project / ".joinery").mkdir(parents=True)
    audit.record(
        "decision",
        "ok",
        device={"id": "d_1", "name": "Ana's iPhone"},
        project=project,
        home=home,
        transaction_id="tx1",
        reason="",
    )
    audit.record("pair", "refused", home=home, reason="wrong code")
    mine = audit.entries(home=home)
    assert [e["action"] for e in mine] == ["decision", "pair"]
    assert mine[0]["device_name"] == "Ana's iPhone"
    assert "reason" not in mine[0]
    assert [e["action"] for e in audit.entries(project=project)] == ["decision"]
    assert stat.S_IMODE(os.stat(home / audit.FILE).st_mode) == 0o600
