"""Paired devices, pairing codes and the audit log (joinery.remote.store/audit)."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

from joinery.remote import audit
from joinery.remote.store import (
    DAY,
    FAILURE_SECONDS,
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


def test_wrong_codes_stop_the_waiting_code_but_lock_nobody_out(store: Store) -> None:
    """Guessing gains nothing (the code it aims at stops), and the wrong guesses of
    anyone at all never keep the owner from pairing with a new code."""
    made = store.new_code(now=NOW)
    assert store.pairing_state(made["pairing_id"], now=NOW) == "waiting"
    for _ in range(MAX_FAILURES):
        with pytest.raises(PairRefused):
            store.redeem("WRONGCODE1", "P", "", digest("t"), digest("s"), now=NOW)
    # The code that waited is stopped, and the machine can tell.
    assert store.pairing_state(made["pairing_id"], now=NOW + 1) == "stopped"
    with pytest.raises(PairRefused, match="stopped"):
        store.redeem(made["code"], "P", "", digest("t"), digest("s"), now=NOW + 1)
    # A new code works at once: nothing is locked.
    fresh = store.new_code(now=NOW + 2)
    assert store.redeem(fresh["code"], "P", "", digest("t"), digest("s"), now=NOW + 2)
    # Wrong codes spread out over more than the window stop nothing.
    other = store.new_code(now=NOW + 3)
    for step in range(MAX_FAILURES):
        when = NOW + 3 + step * (FAILURE_SECONDS / 2)
        with pytest.raises(PairRefused):
            store.redeem("WRONGCODE1", "P", "", digest("t"), digest("s"), now=when)
    assert store.pairing_state(other["pairing_id"], now=NOW + 4) != "stopped"


def test_settings_hears_a_code_was_stopped(
    store: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from joinery.cli import main

    monkeypatch.setenv("JOINERY_REMOTE_HOME", str(store.home))
    made = store.new_code()
    for _ in range(MAX_FAILURES):
        with pytest.raises(PairRefused):
            store.redeem("WRONGCODE1", "P", "", digest("t"), digest("s"))
    assert main(["remote", "requests", "--pairing", made["pairing_id"], "--json"]) == 0
    said = json.loads(capsys.readouterr().out)
    assert said["pairing"] == "stopped" and "stopped working" in said["text"]


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


def test_cli_changes_what_a_device_may_do(
    store: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Settings → Remote's picker runs `joinery remote scopes`."""
    from joinery.cli import main

    monkeypatch.setenv("JOINERY_REMOTE_HOME", str(store.home))
    token = paired(store, now=time.time())
    device = store.authenticate(token)
    assert device is not None
    assert main(["remote", "scopes", device["id"], "watch,chat", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["scopes"] == ["watch", "chat"]
    assert main(["remote", "scopes", device["id"], "root", "--json"]) == 1
    assert "watch, chat and decide" in capsys.readouterr().out
    assert [e["action"] for e in audit.entries(home=store.home)][-1] == "scopes"
