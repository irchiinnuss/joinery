"""``joinery remote serve``: the per-user Joinery Remote server (ADR 0093).

The one network-facing piece, and it listens on 127.0.0.1 only: ``tailscale serve``
carries it to the phone. It serves the phone page and a small JSON + Server-Sent
Events API, pairs devices, checks every request's device token, scopes, Host and
Origin, and passes the closed set of actions (``joinery.remote.actions``) to the
projects' chat hosts over their local channels. There is no file browser, no shell,
no settings and no path parameter anywhere: pictures are served by change and index.

API (all JSON; mutating requests need ``X-Joinery-Remote: 1`` and a matching Origin):

* ``POST /api/pair`` ``{code, name, platform?}`` → ``{request_id, secret}``; then
  ``POST /api/pair/status`` ``{request_id, secret}`` → ``{state}`` until the machine
  answers; ``allowed`` sets the device cookie and returns the token once.
* ``GET /api/me``; ``GET /api/projects``; ``GET /api/projects/{id}/chats``.
* ``GET /api/projects/{id}/events?since=N&started=T`` (SSE): ``hello``, then ``event``
  with ``id`` = seq, ``notice`` for :mod:`joinery.remote.seams` notices, ``gone``.
* ``POST /api/projects/{id}/actions`` ``{kind, ...}`` → ``{ok, text}``.
* ``GET /api/projects/{id}/changes/{tx}``: the change's review (marks it opened).
* ``GET /api/projects/{id}/media/{tx}/{index}/{before|after}``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sse_starlette import EventSourceResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from joinery import chats
from joinery.remote import audit
from joinery.remote.actions import REFUSED, Refused, admit, needs_opening
from joinery.remote.awake import KeepAwake
from joinery.remote.hosts import Host, HostGone, discover, request, subscribe
from joinery.remote.media import MEDIA_TYPES, checked_media, scrub
from joinery.remote.paths import known
from joinery.remote.push import (
    ALL_KINDS,
    DEFAULT_KINDS,
    Vapid,
    WebPushSink,
    checked_subscription,
)
from joinery.remote.seams import (
    HostLauncher,
    NoLauncher,
    Notice,
    NotificationSink,
    notice_for,
)
from joinery.remote.store import (
    ABSOLUTE_DAYS,
    DAY,
    PairRefused,
    Store,
    device_name,
    digest,
)

COOKIE = "joinery_remote"
CSRF_HEADER = "x-joinery-remote"
#: Pairing posts one peer may make a minute (wrong codes also count to the lock).
PAIR_PER_MINUTE = 10
#: Pairing status polls one peer may make a minute (the page polls every 2 s).
STATUS_PER_MINUTE = 60
#: How often the server looks for hosts, and a stream checks its device is still allowed.
DISCOVER_SECONDS = 2.0
RECHECK_SECONDS = 5.0
BODY_LIMIT = 64_000
#: A saved chat's newest turns a phone is sent.
HISTORY_TURNS = 100
#: The page's own files, by URL: nothing else is ever served from disk.
PAGE_FILES = (
    "index.html",
    "app.css",
    "sw.js",
    "manifest.webmanifest",
    "icon.svg",
    "icon-192.png",
    "apple-touch-icon.png",
    "js/app.js",
    "js/api.js",
    "js/state.js",
    "js/view.js",
)
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}
STATIC = {
    ("/" if name == "index.html" else "/" + name): (
        name,
        TYPES[name[name.rindex(".") :]],
    )
    for name in PAGE_FILES
}
SECURITY_HEADERS = [
    (
        b"content-security-policy",
        (
            b"default-src 'self'; img-src 'self' blob:; media-src 'self'; "
            b"frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        ),
    ),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"cache-control", b"no-store"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
]


class Guard:
    """Every request: a Host this server is for (no DNS rebinding), security headers on
    every answer, and for anything that changes state, a matching Origin and the
    custom header a cross-site form can't send (CSRF)."""

    def __init__(self, app: ASGIApp, server: RemoteServer) -> None:
        self.app = app
        self.server = server

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        refusal = self.server.refuse_request(str(scope.get("method", "GET")), headers)

        async def secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], *SECURITY_HEADERS]
            await send(message)

        if refusal is None:
            refusal = _too_long(headers)
        if refusal is not None:
            await JSONResponse({"error": refusal[1]}, status_code=refusal[0])(
                scope, receive, secured
            )
            return
        received = 0

        async def capped() -> Message:
            # A body is never held whole past BODY_LIMIT, chunked or not, before any
            # check of who sent it.
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > BODY_LIMIT:
                    raise TooLong(TOO_LONG)
            return message

        await self.app(scope, capped, secured)


TOO_LONG = "That's too long."


def _too_long(headers: dict[str, str]) -> tuple[int, str] | None:
    """A request that says it is longer than BODY_LIMIT is refused before it is read."""
    length = headers.get("content-length")
    if length is None:
        return None
    if not length.strip().isdigit() or int(length) > BODY_LIMIT:
        return (413, TOO_LONG)
    return None


class RemoteServer:
    def __init__(
        self,
        store: Store,
        *,
        port: int,
        home: Path | None = None,
        launcher: HostLauncher | None = None,
        sinks: list[NotificationSink] | None = None,
    ) -> None:
        self.store = store
        self.port = port
        self.home = home
        self.launcher: HostLauncher = launcher or NoLauncher()
        self.sinks: list[NotificationSink] = list(sinks or [])
        #: Tokens of pairing requests the machine may yet allow; never on disk.
        self._pair_tokens: dict[str, tuple[str, float]] = {}
        self._pair_posts: dict[str, deque[float]] = {}
        self._status_posts: dict[str, deque[float]] = {}
        #: What the server follows of each host: its pending reviews and its state.
        self.previews: dict[str, dict[str, dict[str, Any]]] = {}
        self.states: dict[str, dict[str, Any]] = {}
        #: Changes a device has opened, which may then be accepted from it.
        self.opened: dict[str, set[str]] = {}
        self._watchers: dict[str, asyncio.Task[None]] = {}
        self._listeners: set[asyncio.Queue[Notice]] = set()
        self._discovery: asyncio.Task[None] | None = None
        #: Open event streams per host: a host a phone watches is never idle.
        self.watching: dict[str, int] = {}
        self.awake = KeepAwake()
        #: Ends the server (uvicorn's should_exit), set by :func:`run`.
        self.on_disabled: Callable[[], None] | None = None

    # --- Checks -------------------------------------------------------------------------

    def origins(self) -> set[str]:
        allowed = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}
        origin = str(self.store.config().get("origin", "") or "").rstrip("/")
        if origin:
            allowed.add(origin)
        return allowed

    def refuse_request(
        self, method: str, headers: dict[str, str]
    ) -> tuple[int, str] | None:
        hosts = {urlsplit(origin).netloc for origin in self.origins()}
        hosts |= {urlsplit(origin).hostname or "" for origin in self.origins()}
        if headers.get("host", "") not in hosts:
            return (421, "This server doesn't answer for that name.")
        if not self.store.config().get("enabled"):
            # Turned off (or a login item started it while off): nothing is served.
            return (503, "Remote is off on the machine.")
        if method in {"GET", "HEAD"}:
            return None
        if headers.get("origin", "") not in self.origins():
            return (403, "Wrong origin.")
        if headers.get(CSRF_HEADER) != "1":
            return (403, "Missing the Joinery Remote header.")
        return None

    def device(self, request: Request, scope: str = "watch") -> dict[str, Any]:
        token = request.cookies.get(COOKIE, "")
        bearer = request.headers.get("authorization", "")
        if not token and bearer.lower().startswith("bearer "):
            token = bearer[7:].strip()
        device = self.store.authenticate(token)
        if device is None:
            raise Unauthorised("Pair this device first.")
        if scope not in device.get("scopes", []):
            raise Forbidden(f"This device may not do that ({scope} is not allowed).")
        return device

    def host_for(self, device: dict[str, Any], project_id: str) -> Host:
        allowed = device.get("projects") or []
        if allowed and project_id not in allowed:
            raise NotFound("No such project.")
        for host in discover(self.home):
            if host.id == project_id:
                return host
        raise NotFound("That project isn't open on the machine.")

    # --- Following hosts ----------------------------------------------------------------

    async def follow(self) -> None:
        """Watch every advertised host: its reviews, its state and its notices."""
        while True:
            if not self.store.config().get("enabled"):
                # Remote was turned off: the server ends, and a login item that
                # starts it again finds it off and exits at once.
                if self.on_disabled is not None:
                    self.on_disabled()
                return
            hosts = {host.id: host for host in discover(self.home)}
            for host_id, host in hosts.items():
                task = self._watchers.get(host_id)
                if task is None or task.done():
                    self._watchers[host_id] = asyncio.create_task(self._watch(host))
            for host_id in [h for h in self._watchers if h not in hosts]:
                self._watchers.pop(host_id).cancel()
                self.previews.pop(host_id, None)
                self.states.pop(host_id, None)
            tick = getattr(self.launcher, "tick", None)
            if tick is not None:
                await tick(self.idle_for)
            self.awake.follow(bool(self.store.config().get("keep_awake")))
            await asyncio.sleep(DISCOVER_SECONDS)

    def idle_for(self, host_id: str) -> float:
        """Seconds a host has had nothing running, nothing waiting and no phone
        watching (0 while any of them holds)."""
        state = self.states.get(host_id)
        if state is None:
            return 0.0
        if state.get("working") or state.get("waiting") or state.get("todo"):
            return 0.0
        if self.watching.get(host_id):
            return 0.0
        return time.time() - float(
            state.get("last") or state.get("since") or time.time()
        )

    def quiet(self, host_id: str) -> bool:
        """The user is at the open editor of this project: pushes wait."""
        return bool(self.states.get(host_id, {}).get("focused"))

    async def _watch(self, host: Host) -> None:
        previews = self.previews.setdefault(host.id, {})
        state = self.states.setdefault(
            host.id,
            {
                "working": False,
                "waiting": [],
                "todo": False,
                "last": 0.0,
                "since": time.time(),
                "focused": False,
            },
        )
        replayed = 0
        try:
            async for item in subscribe(host, 0):
                if item.get("kind") == "hello":
                    replayed = int(item.get("seq", 0) or 0)
                    continue
                event = item.get("event")
                if not isinstance(event, dict):
                    continue
                self._track(previews, state, event)
                # The catch-up replays the past: only what happens now notifies.
                notice = notice_for(host, event)
                if notice is not None and int(item.get("seq", 0)) > replayed:
                    self.publish(notice)
        except (HostGone, OSError, ValueError):
            pass

    def _track(
        self,
        previews: dict[str, dict[str, Any]],
        state: dict[str, Any],
        event: dict[str, Any],
    ) -> None:
        kind = str(event.get("kind", ""))
        tx = str(event.get("transaction_id", ""))
        if kind == "presence":
            state["focused"] = bool(event.get("focused"))
            return
        state["last"] = time.time()
        if kind == "file_preview" and tx:
            previews[tx] = event
        elif kind in {"review_media", "review_notes"} and tx in previews:
            previews[tx] = {**previews[tx], **event, "kind": "file_preview"}
        elif kind == "file_decision" and tx:
            previews.pop(tx, None)
        elif kind == "execution":
            state["working"] = True
        elif kind == "done":
            state["working"] = False
        elif kind == "todo":
            state["todo"] = event.get("state") == "waiting"
        state["waiting"] = sorted(previews)

    def publish(self, notice: Notice) -> None:
        for sink in self.sinks:
            with contextlib.suppress(Exception):
                sink.notify(notice)
        for queue in [*self._listeners]:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(notice)

    # --- Pairing ------------------------------------------------------------------------

    def _limited(
        self, peer: str, book: dict[str, deque[float]] | None = None, limit: int = 0
    ) -> bool:
        book = self._pair_posts if book is None else book
        limit = limit or PAIR_PER_MINUTE
        now = time.time()
        posts = book.setdefault(peer, deque(maxlen=limit))
        while posts and posts[0] < now - 60:
            posts.popleft()
        if len(posts) >= limit:
            return True
        posts.append(now)
        return False

    async def pair(self, request: Request) -> Response:
        peer = _peer(request)
        if self._limited(peer):
            audit.record("pair", "refused", home=self.home, peer=peer, reason="rate")
            return JSONResponse({"error": "Too many tries. Wait a minute."}, 429)
        body = await _body(request)
        token = secrets.token_urlsafe(32)
        secret = secrets.token_urlsafe(24)
        name = device_name(str(body.get("name", "")))
        try:
            made = self.store.redeem(
                str(body.get("code", "")),
                name,
                str(body.get("platform", "")),
                digest(token),
                digest(secret),
                peer=peer,
            )
        except PairRefused as exc:
            audit.record(
                "pair", "refused", home=self.home, peer=peer, reason=str(exc), name=name
            )
            return JSONResponse({"error": str(exc)}, 403)
        self._pair_tokens[made["id"]] = (token, float(made["expires"]))
        audit.record("pair", "ok", home=self.home, peer=peer, name=name, step="asked")
        return JSONResponse(
            {"request_id": made["id"], "secret": secret, "state": "waiting"}
        )

    async def pair_status(self, request: Request) -> Response:
        if self._limited(_peer(request), self._status_posts, STATUS_PER_MINUTE):
            return JSONResponse({"error": "Too many tries. Wait a minute."}, 429)
        body = await _body(request)
        request_id = str(body.get("request_id", ""))
        known = self.store.request_state(request_id, str(body.get("secret", "")))
        if known["state"] != "allowed":
            if known["state"] in {"denied", "expired"}:
                self._pair_tokens.pop(request_id, None)
            return JSONResponse({"state": known["state"]})
        held = self._pair_tokens.pop(request_id, None)
        if held is None:
            # Delivered once already, or the server restarted meanwhile.
            return JSONResponse({"state": "allowed", "device_id": known["device_id"]})
        token = held[0]
        answer = JSONResponse(
            {"state": "allowed", "device_id": known["device_id"], "token": token}
        )
        answer.set_cookie(
            COOKIE,
            token,
            max_age=int(ABSOLUTE_DAYS * DAY),
            path="/",
            secure=True,
            httponly=True,
            samesite="strict",
        )
        return answer

    # --- Reading ------------------------------------------------------------------------

    async def me(self, request: Request) -> Response:
        return JSONResponse(self.device(request))

    async def projects(self, request: Request) -> Response:
        device = self.device(request)
        allowed = device.get("projects") or []
        shown = []
        for host in discover(self.home):
            if allowed and host.id not in allowed:
                continue
            state = self.states.get(host.id, {})
            shown.append(
                {
                    **host.public(),
                    "working": bool(state.get("working")),
                    "waiting": len(state.get("waiting", [])),
                    "todo": bool(state.get("todo")),
                    "last": float(state.get("last", 0)),
                    "open": True,
                }
            )
        # Projects a host served here before: the helper can open them again.
        running = {project["id"] for project in shown}
        can_open = not isinstance(self.launcher, NoLauncher)
        for identifier, entry in known(self.home).items():
            if identifier in running or (allowed and identifier not in allowed):
                continue
            shown.append(
                {
                    "id": identifier,
                    "name": str(entry.get("name", "")),
                    "agent": str(entry.get("agent", "")),
                    "editor": False,
                    "open": False,
                    "can_open": can_open,
                    "last": float(entry.get("seen", 0)),
                }
            )
        return JSONResponse({"projects": shown})

    async def open_project(self, request: Request) -> Response:
        """Start a project's headless host (the always-on helper), for a phone."""
        device = self.device(request, "chat")
        identifier = request.path_params["project"]
        allowed = device.get("projects") or []
        entry = known(self.home).get(identifier)
        if entry is None or (allowed and identifier not in allowed):
            raise NotFound("No such project.")
        if isinstance(self.launcher, NoLauncher):
            return JSONResponse(
                {
                    "ok": False,
                    "text": "Open it in Joinery on your Mac, or turn on Keep Remote "
                    "available in Settings → Remote.",
                },
                409,
            )
        try:
            host = await self.launcher.ensure_host(Path(str(entry["project"])))
        except (OSError, ValueError) as exc:
            audit.record(
                "open", "error", device=device, home=self.home, reason=str(exc)
            )
            return JSONResponse({"ok": False, "text": str(exc)}, 409)
        audit.record(
            "open",
            "ok",
            device=device,
            project=Path(str(entry["project"])),
            home=self.home,
        )
        return JSONResponse(
            {"ok": host is not None, "project": host.public() if host else None}
        )

    # --- Push -------------------------------------------------------------------------

    async def push_info(self, request: Request) -> Response:
        device = self.device(request)
        mine = [s for d, s in self.store.push_targets() if d["id"] == device["id"]]
        return JSONResponse(
            {
                "public_key": Vapid(self.home).public_key(),
                "subscribed": bool(mine),
                "kinds": mine[0].get("kinds", list(DEFAULT_KINDS))
                if mine
                else list(DEFAULT_KINDS),
            }
        )

    async def push_subscribe(self, request: Request) -> Response:
        device = self.device(request)
        body = await _body(request)
        try:
            subscription = checked_subscription(body.get("subscription"))
        except (TypeError, ValueError) as exc:
            audit.record(
                "push", "refused", device=device, home=self.home, reason=str(exc)
            )
            return JSONResponse({"ok": False, "text": str(exc)}, 400)
        kinds = [k for k in body.get("kinds", DEFAULT_KINDS) if k in ALL_KINDS] or list(
            DEFAULT_KINDS
        )
        self.store.add_push(str(device["id"]), subscription, kinds)
        audit.record("push", "ok", device=device, home=self.home, step="on")
        return JSONResponse({"ok": True, "kinds": kinds})

    async def push_unsubscribe(self, request: Request) -> Response:
        device = self.device(request)
        body = await _body(request)
        self.store.remove_push(str(body.get("endpoint", "")), str(device["id"]))
        audit.record("push", "ok", device=device, home=self.home, step="off")
        return JSONResponse({"ok": True})

    async def chat_list(self, request: Request) -> Response:
        device = self.device(request)
        host = self.host_for(device, request.path_params["project"])
        listing = await asyncio.to_thread(chats.listing, host.project)
        keep = ("id", "title", "provider", "updated", "created", "turns", "busy")
        return JSONResponse(
            {"chats": [{k: c[k] for k in keep if k in c} for c in listing]}
        )

    async def chat_history(self, request: Request) -> Response:
        """A saved chat's turns ({request, reply, changes, tasks, outcome})."""
        device = self.device(request)
        host = self.host_for(device, request.path_params["project"])
        try:
            _, entries = await asyncio.to_thread(
                chats.read, host.project, request.path_params["chat"]
            )
        except (OSError, ValueError) as exc:
            raise NotFound(str(exc) or "No such chat.") from exc
        turns = chats.turns(entries)[-HISTORY_TURNS:]
        return JSONResponse({"turns": scrub(turns, str(host.project))})

    async def change(self, request: Request) -> Response:
        device = self.device(request)
        host = self.host_for(device, request.path_params["project"])
        tx = request.path_params["tx"]
        preview = self.previews.get(host.id, {}).get(tx)
        if preview is None:
            raise NotFound("That change isn't waiting.")
        self.opened.setdefault(str(device["id"]), set()).add(tx)
        return JSONResponse({**preview, "needs_opening": needs_opening(preview)})

    async def media(self, request: Request) -> Response:
        device = self.device(request)
        host = self.host_for(device, request.path_params["project"])
        tx = request.path_params["tx"]
        side = request.path_params["side"]
        try:
            index = int(request.path_params["index"])
            answer = await request_host(
                host,
                {"op": "media", "transaction_id": tx, "index": index, "side": side},
            )
            if not answer.get("ok"):
                raise ValueError(str(answer.get("text", "No such picture.")))
            # Checked here too: whatever the host said, only that change's own files.
            path = checked_media(host.project, tx, Path(str(answer.get("path", ""))))
        except (ValueError, HostGone) as exc:
            raise NotFound(str(exc) or "No such picture.") from exc
        return FileResponse(
            path,
            media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
            headers={"cache-control": "private, max-age=300"},
        )

    async def events(self, request: Request) -> Response:
        device = self.device(request)
        host = self.host_for(device, request.path_params["project"])
        try:
            since = max(0, int(request.query_params.get("since", "0")))
            started = float(request.query_params.get("started", "0"))
        except ValueError:
            since, started = 0, 0.0
        if started and abs(started - host.started) > 0.001:
            since = 0  # Another host since: the device catches up from the start.
        listener: asyncio.Queue[Notice] = asyncio.Queue(maxsize=100)
        self._listeners.add(listener)
        self.watching[host.id] = self.watching.get(host.id, 0) + 1

        async def stream() -> AsyncIterator[dict[str, str]]:
            events = subscribe(host, since)
            pending: asyncio.Task[dict[str, Any]] | None = None
            noticed: asyncio.Task[Notice] | None = None
            checked = time.monotonic()
            try:
                while True:
                    if pending is None:
                        pending = asyncio.ensure_future(anext(events))
                    if noticed is None:
                        noticed = asyncio.ensure_future(listener.get())
                    done, _ = await asyncio.wait(
                        {pending, noticed},
                        timeout=RECHECK_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if time.monotonic() - checked >= RECHECK_SECONDS:
                        checked = time.monotonic()
                        if not self.store.active(str(device["id"])):
                            yield {"event": "gone", "data": '{"reason":"revoked"}'}
                            return
                    if noticed in done:
                        notice = noticed.result()
                        noticed = None
                        if notice.project_id == host.id:
                            yield {
                                "event": "notice",
                                "data": json.dumps(notice.as_dict()),
                            }
                    if pending in done:
                        try:
                            item = pending.result()
                        except (StopAsyncIteration, HostGone, OSError, ValueError):
                            yield {"event": "gone", "data": '{"reason":"closed"}'}
                            return
                        pending = None
                        if item.get("kind") == "hello":
                            yield {"event": "hello", "data": json.dumps(item)}
                        else:
                            yield {
                                "event": "event",
                                "id": str(item.get("seq", "")),
                                "data": json.dumps(item.get("event", {})),
                            }
            finally:
                for task in (pending, noticed):
                    if task is not None:
                        task.cancel()
                self._listeners.discard(listener)
                self.watching[host.id] = max(0, self.watching.get(host.id, 1) - 1)
                with contextlib.suppress(Exception):
                    await events.aclose()

        return EventSourceResponse(stream(), ping=15)

    # --- Acting -------------------------------------------------------------------------

    async def action(self, request: Request) -> Response:
        device = self.device(request, "watch")
        project_id = request.path_params["project"]
        body = await _body(request)
        kind = str(body.get("kind", ""))[:40]
        try:
            host = self.host_for(device, project_id)
        except NotFound:
            audit.record(
                kind or "?", "refused", device=device, home=self.home, reason="project"
            )
            raise
        try:
            action = admit(body, device.get("scopes", []))
        except Refused as exc:
            audit.record(
                kind or "?",
                "refused",
                device=device,
                project=host.project,
                home=self.home,
                reason=str(exc),
            )
            return JSONResponse({"ok": False, "text": str(exc) or REFUSED}, 403)
        tx = str(action.get("transaction_id", ""))
        if action["kind"] == "decision" and action.get("accept"):
            preview = self.previews.get(host.id, {}).get(tx)
            opened = tx in self.opened.get(str(device["id"]), set())
            if preview is not None and needs_opening(preview) and not opened:
                audit.record(
                    "decision",
                    "refused",
                    device=device,
                    project=host.project,
                    home=self.home,
                    transaction_id=tx,
                    reason="not opened",
                )
                return JSONResponse(
                    {
                        "ok": False,
                        "text": "Open the change first: it has notes to read.",
                    },
                    409,
                )
        payload = {
            "op": "action",
            "action": action,
            "device": {
                "id": device["id"],
                "name": device["name"],
                "scopes": device.get("scopes", []),
            },
        }
        try:
            answer = await request_host(host, payload)
        except HostGone as exc:
            audit.record(
                action["kind"],
                "error",
                device=device,
                project=host.project,
                home=self.home,
                reason=str(exc),
            )
            return JSONResponse({"ok": False, "text": str(exc)}, 502)
        result = (
            "ok"
            if answer.get("ok")
            else "refused"
            if answer.get("refused")
            else "error"
        )
        audit.record(
            action["kind"],
            result,
            device=device,
            project=host.project,
            home=self.home,
            transaction_id=tx,
            accept=action.get("accept") if action["kind"] == "decision" else None,
            reason=str(answer.get("text", "")) if result != "ok" else "",
        )
        self.store.touch(str(device["id"]), action["kind"])
        shown = {
            "ok": bool(answer.get("ok")),
            "text": str(answer.get("text", "") or ""),
        }
        if isinstance(answer.get("event"), dict):
            shown["event"] = answer["event"]
        return JSONResponse(shown, 200 if shown["ok"] else 409)

    # --- App ----------------------------------------------------------------------------

    def app(self) -> ASGIApp:
        def guarded(
            handler: Callable[[Request], Awaitable[Response]],
        ) -> Callable[[Request], Awaitable[Response]]:
            async def run(request: Request) -> Response:
                try:
                    return await handler(request)
                except Problem as exc:
                    return JSONResponse({"error": str(exc)}, exc.status)

            return run

        routes = [
            Route(path, _static(path), methods=["GET", "HEAD"]) for path in STATIC
        ]
        routes += [
            Route("/api/pair", guarded(self.pair), methods=["POST"]),
            Route("/api/pair/status", guarded(self.pair_status), methods=["POST"]),
            Route("/api/me", guarded(self.me)),
            Route("/api/projects", guarded(self.projects)),
            Route("/api/push", guarded(self.push_info)),
            Route(
                "/api/push/subscribe", guarded(self.push_subscribe), methods=["POST"]
            ),
            Route(
                "/api/push/unsubscribe",
                guarded(self.push_unsubscribe),
                methods=["POST"],
            ),
            Route(
                "/api/projects/{project}/open",
                guarded(self.open_project),
                methods=["POST"],
            ),
            Route("/api/projects/{project}/chats", guarded(self.chat_list)),
            Route("/api/projects/{project}/chats/{chat}", guarded(self.chat_history)),
            Route("/api/projects/{project}/events", guarded(self.events)),
            Route(
                "/api/projects/{project}/actions",
                guarded(self.action),
                methods=["POST"],
            ),
            Route("/api/projects/{project}/changes/{tx}", guarded(self.change)),
            Route(
                "/api/projects/{project}/media/{tx}/{index}/{side}",
                guarded(self.media),
            ),
        ]

        @contextlib.asynccontextmanager
        async def lifespan(_app: Starlette) -> AsyncIterator[None]:
            self._discovery = asyncio.create_task(self.follow())
            try:
                yield
            finally:
                self._discovery.cancel()
                for task in self._watchers.values():
                    task.cancel()
                await asyncio.gather(
                    self._discovery, *self._watchers.values(), return_exceptions=True
                )
                self.awake.stop()
                closing = getattr(self.launcher, "close", None)
                if closing is not None:
                    await closing()

        return Guard(Starlette(routes=routes, lifespan=lifespan), self)


#: Replaced in tests.
request_host = request


class Problem(Exception):
    status = 400


class Unauthorised(Problem):
    status = 401


class Forbidden(Problem):
    status = 403


class NotFound(Problem):
    status = 404


class TooLong(Problem):
    status = 413


async def _body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > BODY_LIMIT:
        raise TooLong(TOO_LONG)
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        raise Problem("Send JSON.") from None
    if not isinstance(data, dict):
        raise Problem("Send a JSON object.")
    return data


def _peer(request: Request) -> str:
    """Who asked: the tailnet login ``tailscale serve`` adds, else the socket's address."""
    client = request.client.host if request.client else ""
    login = request.headers.get("tailscale-user-login", "")
    # Only the local serve proxy may say who it is.
    if login and client in {"127.0.0.1", "::1"}:
        return login[:100]
    return client


def _static(path: str) -> Callable[[Request], Awaitable[Response]]:
    name, media_type = STATIC[path]

    async def serve(_request: Request) -> Response:
        data = (
            resources.files("joinery.remote")
            .joinpath("static", *name.split("/"))
            .read_bytes()
        )
        return Response(data, media_type=media_type)

    return serve


def run(
    store: Store, port: int, home: Path | None = None, *, helper: bool = False
) -> int:
    """Serve on 127.0.0.1:``port`` until stopped. ``helper``: the always-on helper,
    which starts a headless host for a project no editor has open."""
    import uvicorn

    from joinery.remote.helper import HeadlessLauncher

    launcher: HostLauncher = HeadlessLauncher(home) if helper else NoLauncher()
    server = RemoteServer(store, port=port, home=home, launcher=launcher)
    server.sinks.append(WebPushSink(store, home, quiet=server.quiet))
    config = uvicorn.Config(
        server.app(),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        proxy_headers=False,
        server_header=False,
        lifespan="on",
    )
    serving = uvicorn.Server(config)
    server.on_disabled = lambda: setattr(serving, "should_exit", True)
    serving.run()
    return 0
