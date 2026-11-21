"""The closed set of things a paired device may ask a chat host to do (ADR 0093).

Everything not listed here is refused with :data:`REFUSED` and audited. The server checks
an action on arrival and the host checks it again before dispatch, so a kind that is not
listed never reaches the host's input loop. Only the listed fields pass, with their types
checked and their sizes capped: a device can't add ``auto`` to a decision, targets, an
editor context or attachments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Scope = Literal["watch", "chat", "decide"]
#: What a device may do: read (watch), direct the work (chat), keep or drop changes (decide).
SCOPES: tuple[Scope, ...] = ("watch", "chat", "decide")
#: How the host carries an action out. ``relay``: with an editor attached, the sidebar
#: does it as if clicked (its own state, queue and live scene edits); headless, the host
#: dispatches it. ``direct``: dispatched by the host's input loop like an editor line.
#: ``reply``: answered to the device alone, nothing else sees it.
Route = Literal["relay", "direct", "reply"]

REFUSED = "Not available remotely. Use the editor."
#: Longest message text a device may send (characters).
TEXT_LIMIT = 32_000
#: Longest id or short field (transaction, to-do, chat, task, agent ids).
ID_LIMIT = 200

#: Things that stay at the editor, with where to do them.
EDITOR_ONLY = {
    "art_approval": "Approve the art direction at the editor (/art → Approve).",
    "auto_accept": "Turn automatic Accept on or off in the editor's Settings.",
    "errors_mode": "Change how errors are fixed in the editor's Settings.",
    "mixer_save": "Save the mix in the editor.",
    "cut_write": "Write the polish list in the editor.",
    "tidy_remove": "Remove unused files in the editor.",
    "editor_result": REFUSED,
    "shutdown": REFUSED,
}


class Refused(ValueError):
    """An action a device may not send: not listed, outside its scopes, or malformed."""


@dataclass(frozen=True)
class Rule:
    scope: Scope
    #: Field name → (type, required).
    fields: dict[str, tuple[type, bool]]
    route: Route


ACTIONS: dict[str, Rule] = {
    # Send a message in the open chat (or the chat named).
    "prompt": Rule("chat", {"text": (str, True), "chat_id": (str, False)}, "relay"),
    # A message for another chat, run beside the open one (ADR 0036).
    "chat_task": Rule(
        "chat",
        {"text": (str, True), "chat_id": (str, False), "ref": (str, False)},
        "direct",
    ),
    # Stop; with all, the tasks too; with task_id, that task only.
    "cancel": Rule("chat", {"all": (bool, False), "task_id": (str, False)}, "relay"),
    "cancel_tasks": Rule("chat", {}, "direct"),
    # Go or Cancel on a to-do, as Journeyman left it.
    "todo_go": Rule("chat", {"todo_id": (str, True)}, "relay"),
    "todo_cancel": Rule("chat", {"todo_id": (str, True)}, "relay"),
    # Accept or Discard a change waiting for review.
    "decision": Rule(
        "decide", {"transaction_id": (str, True), "accept": (bool, True)}, "relay"
    ),
    # "Continue with <agent>" after a provider limit (ADR 0080); "" declines.
    "provider_switch": Rule("chat", {"agent": (str, False)}, "direct"),
    # Read the art direction (never approve it).
    "art_review": Rule("watch", {}, "reply"),
}


def admit(action: Any, scopes: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """``action`` cleaned to its listed fields; raises :class:`Refused` otherwise."""
    if not isinstance(action, dict):
        raise Refused("An action is a JSON object.")
    kind = action.get("kind")
    if not isinstance(kind, str) or kind not in ACTIONS:
        raise Refused(
            EDITOR_ONLY.get(kind, REFUSED) if isinstance(kind, str) else REFUSED
        )
    rule = ACTIONS[kind]
    if rule.scope not in scopes:
        raise Refused(f"This device may not do that ({rule.scope} is not allowed).")
    clean: dict[str, Any] = {"kind": kind}
    for name, (kind_of, required) in rule.fields.items():
        if name not in action or action[name] is None:
            if required:
                raise Refused(f"Missing {name}.")
            continue
        value = action[name]
        if kind_of is bool:
            if not isinstance(value, bool):
                raise Refused(f"{name} is true or false.")
        elif not isinstance(value, str):
            raise Refused(f"{name} is text.")
        elif len(value) > (TEXT_LIMIT if name == "text" else ID_LIMIT):
            raise Refused(f"{name} is too long.")
        clean[name] = value
    if "text" in rule.fields and not str(clean.get("text", "")).strip():
        raise Refused("Write a message first.")
    return clean


def route(action: dict[str, Any]) -> Route:
    """How the host carries out an admitted ``action``."""
    if action["kind"] == "cancel" and action.get("task_id"):
        # One task's Stop: its card is the host's task event, not the sidebar's turn.
        return "direct"
    return ACTIONS[action["kind"]].route


def needs_opening(preview: dict[str, Any]) -> bool:
    """A change that carries something to read before keeping it: a reviewer's warn or
    block, a failed check, a crossed Keep/Avoid line, an unmet done-when, an art gate
    or a departure from the request. A device must open it before its Accept."""
    if any(
        isinstance(note, dict) and note.get("severity") in {"warn", "block"}
        for note in preview.get("review_notes") or []
    ):
        return True
    if preview.get("failed_checks") or preview.get("keep_avoid"):
        return True
    if preview.get("art_gate") or preview.get("not_as_asked"):
        return True
    return any(
        isinstance(result, dict) and result.get("status") != "pass"
        for result in preview.get("done_when") or []
    )
