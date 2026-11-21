"""Where later parts of Joinery Remote plug in (ADR 0093).

* :class:`HostLauncher`: start a headless chat host for a project that has none open,
  so a phone works with no editor open. The always-on helper implements it with
  ``joinery.headless`` (``joinery chat --keep-reviews``, the host ``joinery run``
  starts), taking the project's ``ProjectLease`` like any host. The server calls it only
  when no host advertises the project, so it never starts one beside an editor's.
* :class:`NotificationSink`: told when Journeyman finishes, needs the user, has a change
  ready or hits an error, for every project the server follows, whether or not a phone
  has the page open. Web Push implements it; :func:`notice_for` decides what counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from joinery.remote.hosts import Host

NoticeKind = Literal["finished", "needs_you", "change_ready", "error"]
POLICY_STOP = "Stopped by project policy"


@dataclass(frozen=True)
class Notice:
    kind: NoticeKind
    project_id: str
    project: str
    #: Plain, short, without the change's or the chat's content: what a lock screen may show.
    text: str
    transaction_id: str = ""
    chat_id: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "project_id": self.project_id,
            "project": self.project,
            "text": self.text,
            "transaction_id": self.transaction_id,
            "chat_id": self.chat_id,
        }


class NotificationSink(Protocol):
    def notify(self, notice: Notice) -> None: ...


class HostLauncher(Protocol):
    async def ensure_host(self, project: Path) -> Host | None:
        """A running host for ``project``: started headless when none is open, else None."""
        ...


class NoLauncher:
    """Until the always-on helper is installed: only projects open somewhere are reachable."""

    async def ensure_host(self, project: Path) -> Host | None:
        return None


def notice_for(host: Host, event: dict[str, Any]) -> Notice | None:
    """The notification an event from ``host`` makes, if any."""
    kind = str(event.get("kind", ""))
    name = host.name
    chat = str(event.get("chat_id", ""))
    tx = str(event.get("transaction_id", ""))

    def made(what: NoticeKind, text: str) -> Notice:
        return Notice(what, host.id, name, text, transaction_id=tx, chat_id=chat)

    if kind == "file_preview":
        return made("change_ready", f"A change waits in {name}.")
    if kind == "todo" and event.get("state") == "waiting":
        return made("needs_you", f"A to-do waits for Go in {name}.")
    if kind in {"provider_switch", "art_gate"}:
        return made("needs_you", f"Journeyman needs you in {name}.")
    if kind == "message" and event.get("level") == "warn":
        if str(event.get("text", "")).startswith(POLICY_STOP):
            return made("needs_you", f"Journeyman stopped in {name}: project policy.")
        return made("error", f"Something went wrong in {name}.")
    if kind == "done" and event.get("ok"):
        return made("finished", f"Journeyman finished in {name}.")
    if kind == "connection" and event.get("state") == "error":
        return made("error", f"Journeyman lost its connection in {name}.")
    return None
