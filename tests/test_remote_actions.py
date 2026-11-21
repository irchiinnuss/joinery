"""Joinery Remote's closed set of actions (joinery.remote.actions, ADR 0093)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from joinery.remote.actions import (
    ACTIONS,
    EDITOR_ONLY,
    REFUSED,
    SCOPES,
    TEXT_LIMIT,
    Refused,
    admit,
    needs_opening,
    route,
)

ALL = list(SCOPES)
ROOT = Path(__file__).resolve().parents[1]

VALID = {
    "prompt": {"kind": "prompt", "text": "Make the hedge taller"},
    "chat_task": {"kind": "chat_task", "text": "Fix the menu", "chat_id": "c1"},
    "cancel": {"kind": "cancel"},
    "cancel_tasks": {"kind": "cancel_tasks"},
    "todo_go": {"kind": "todo_go", "todo_id": "t1"},
    "todo_cancel": {"kind": "todo_cancel", "todo_id": "t1"},
    "decision": {"kind": "decision", "transaction_id": "tx1", "accept": True},
    "provider_switch": {"kind": "provider_switch", "agent": "codex"},
    "art_review": {"kind": "art_review"},
}


def host_kinds() -> set[str]:
    """Every kind the chat host's input loop dispatches on."""
    source = (ROOT / "bridge" / "stdio.py").read_text(encoding="utf-8")
    return set(re.findall(r'data\.get\("kind"\) == "([a-z_]+)"', source))


def test_every_listed_action_is_admitted() -> None:
    assert set(VALID) == set(ACTIONS)
    for kind, action in VALID.items():
        assert admit(action, ALL)["kind"] == kind


def test_every_other_host_kind_is_refused() -> None:
    others = host_kinds() - set(ACTIONS)
    # The loop's own kinds are found (the test would pass vacuously otherwise).
    assert {"art_approval", "shutdown", "editor_result", "mixer_save"} <= others
    for kind in others:
        with pytest.raises(Refused):
            admit({"kind": kind}, ALL)


def test_editor_only_things_say_where_to_do_them() -> None:
    with pytest.raises(Refused, match="/art"):
        admit({"kind": "art_approval", "sha256": "x"}, ALL)
    with pytest.raises(Refused, match=re.escape(REFUSED)):
        admit({"kind": "rm -rf /"}, ALL)
    assert "art_approval" in EDITOR_ONLY


@pytest.mark.parametrize("bad", [None, [], "prompt", {"text": "hi"}, {"kind": 3}])
def test_malformed_actions_are_refused(bad: object) -> None:
    with pytest.raises(Refused):
        admit(bad, ALL)


def test_only_listed_fields_pass() -> None:
    cleaned = admit(
        {
            "kind": "decision",
            "transaction_id": "tx1",
            "accept": True,
            "auto": True,
            "decided_from": "spoofed",
        },
        ALL,
    )
    assert cleaned == {"kind": "decision", "transaction_id": "tx1", "accept": True}
    prompt = admit(
        {
            "kind": "prompt",
            "text": "hi",
            "targets": [{"id": "x"}],
            "editor_context": {"scene": "res://a.tscn"},
            "attachments": ["/etc/passwd"],
        },
        ALL,
    )
    assert prompt == {"kind": "prompt", "text": "hi"}


def test_field_types_and_sizes_are_checked() -> None:
    with pytest.raises(Refused):
        admit({"kind": "decision", "transaction_id": "tx", "accept": "yes"}, ALL)
    with pytest.raises(Refused):
        admit({"kind": "decision", "transaction_id": 5, "accept": True}, ALL)
    with pytest.raises(Refused):
        admit({"kind": "decision", "accept": True}, ALL)
    with pytest.raises(Refused):
        admit({"kind": "prompt", "text": "x" * (TEXT_LIMIT + 1)}, ALL)
    with pytest.raises(Refused):
        admit({"kind": "prompt", "text": "   "}, ALL)
    with pytest.raises(Refused):
        admit({"kind": "todo_go", "todo_id": "t" * 500}, ALL)


def test_scopes() -> None:
    with pytest.raises(Refused, match="decide"):
        admit(VALID["decision"], ["watch", "chat"])
    with pytest.raises(Refused, match="chat"):
        admit(VALID["prompt"], ["watch"])
    assert admit(VALID["art_review"], ["watch"])["kind"] == "art_review"
    with pytest.raises(Refused):
        admit(VALID["art_review"], [])


def test_routes() -> None:
    assert route(admit(VALID["prompt"], ALL)) == "relay"
    assert route(admit(VALID["decision"], ALL)) == "relay"
    assert route(admit({"kind": "cancel", "task_id": "t1"}, ALL)) == "direct"
    assert route(admit(VALID["chat_task"], ALL)) == "direct"
    assert route(admit(VALID["art_review"], ALL)) == "reply"


def test_needs_opening() -> None:
    assert not needs_opening({"review_notes": [{"severity": "info", "text": "ok"}]})
    assert needs_opening({"review_notes": [{"severity": "warn", "text": "dark"}]})
    assert needs_opening({"failed_checks": [{"text": "lint"}]})
    assert needs_opening({"keep_avoid": [{"reason": "x"}]})
    assert needs_opening({"done_when": [{"status": "inconclusive"}]})
    assert not needs_opening({"done_when": [{"status": "pass"}]})
    assert needs_opening({"not_as_asked": ["Used blue"]})
