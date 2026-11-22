"""The Joinery Remote server (joinery.remote.server, ADR 0093), driven as an ASGI app
against a real host channel."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from bridge.remote_channel import RemoteChannel
from joinery.remote import audit
from joinery.remote.paths import project_id
from joinery.remote.server import COOKIE, RemoteServer
from joinery.remote.store import Store, digest

PORT = 47615
LOCAL = f"127.0.0.1:{PORT}"
ORIGIN = f"http://{LOCAL}"


class Answer:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


async def call(
    app: Any,
    method: str,
    path: str,
    *,
    body: Any = None,
    token: str = "",
    headers: dict[str, str] | None = None,
    until: Callable[[bytes], bool] | None = None,
) -> Answer:
    """One request to ``app``; a stream is read ``until`` it says so, then disconnected."""
    path, _, query = path.partition("?")
    sent: dict[str, str] = {"host": LOCAL}
    if method == "POST":
        sent |= {
            "origin": ORIGIN,
            "x-joinery-remote": "1",
            "content-type": "application/json",
        }
    if token:
        sent["cookie"] = f"{COOKIE}={token}"
    sent |= headers or {}
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [(k.encode(), v.encode()) for k, v in sent.items() if v is not None],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", PORT),
        "scheme": "http",
        "http_version": "1.1",
        "root_path": "",
    }
    data = json.dumps(body).encode() if body is not None else b""
    started: dict[str, Any] = {}
    chunks: list[bytes] = []
    finished = asyncio.Event()
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": data, "more_body": False}
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            started.update(message)
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
            if until is not None and until(b"".join(chunks)):
                finished.set()
            if not message.get("more_body"):
                finished.set()

    await asyncio.wait_for(app(scope, receive, send), 15)
    head = {k.decode(): v.decode() for k, v in started.get("headers", [])}
    return Answer(int(started.get("status", 0)), head, b"".join(chunks))


class Fixture:
    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.home.mkdir(mode=0o700)
        self.store = Store(self.home)
        self.store.set_config(enabled=True, port=PORT)
        self.project = tmp_path / "game"
        (self.project / ".joinery/transactions/tx1/stage/art").mkdir(parents=True)
        self.server = RemoteServer(self.store, port=PORT, home=self.home)
        self.app = self.server.app()
        self.channel = RemoteChannel(
            self.project,
            "claude",
            editor=False,
            to_editor=lambda e: None,
            home=self.home,
        )
        self.actions: list[dict[str, Any]] = []
        self.pid = project_id(self.project)

    def device(self, scopes: tuple[str, ...] = ("watch", "chat", "decide")) -> str:
        made = self.store.new_code(scopes)
        token = "tok-" + made["pairing_id"]
        request = self.store.redeem(
            made["code"], "Ana's iPhone", "iOS", digest(token), digest("s")
        )
        self.store.answer(request["id"], True)
        return token

    async def host_loop(self) -> None:
        while True:
            action, origin = await self.channel.inbox.get()
            self.actions.append({**action, "from": origin.device_name})
            origin.done(True, "Sent.")


@asynccontextmanager
async def running(tmp_path: Path) -> AsyncIterator[Fixture]:
    fixture = Fixture(tmp_path)
    await fixture.channel.start()
    loop = asyncio.create_task(fixture.host_loop())
    try:
        yield fixture
    finally:
        loop.cancel()
        await fixture.channel.close()


def run(tmp_path: Path, test: Callable[[Fixture], Awaitable[None]]) -> None:
    async def body() -> None:
        async with running(tmp_path) as fixture:
            await test(fixture)

    asyncio.run(asyncio.wait_for(body(), 30))


def test_security_headers_and_host_check(tmp_path: Path) -> None:
    async def test(f: Fixture) -> None:
        page = await call(f.app, "GET", "/")
        assert page.status == 200 and b"Joinery Remote" in page.body
        assert "default-src 'self'" in page.headers["content-security-policy"]
        assert page.headers["x-frame-options"] == "DENY"
        assert page.headers["referrer-policy"] == "no-referrer"
        # DNS rebinding: another name pointed at 127.0.0.1 gets nothing.
        rebound = await call(f.app, "GET", "/", headers={"host": "evil.example"})
        assert rebound.status == 421
        assert (await call(f.app, "GET", "/api/projects")).status == 401

    run(tmp_path, test)


def test_tailnet_name_is_allowed_once_configured(tmp_path: Path) -> None:
    async def test(f: Fixture) -> None:
        name = "mac.tail1234.ts.net"
        assert (await call(f.app, "GET", "/", headers={"host": name})).status == 421
        f.store.set_config(origin=f"https://{name}")
        assert (await call(f.app, "GET", "/", headers={"host": name})).status == 200

    run(tmp_path, test)


def test_mutations_need_origin_and_header(tmp_path: Path) -> None:
    async def test(f: Fixture) -> None:
        token = f.device()
        action = {"kind": "prompt", "text": "hi"}
        path = f"/api/projects/{f.pid}/actions"
        cross = await call(
            f.app,
            "POST",
            path,
            body=action,
            token=token,
            headers={"origin": "https://evil"},
        )
        assert cross.status == 403
        bare = await call(
            f.app,
            "POST",
            path,
            body=action,
            token=token,
            headers={"x-joinery-remote": ""},
        )
        assert bare.status == 403
        assert f.actions == []
        ok = await call(f.app, "POST", path, body=action, token=token)
        assert ok.status == 200 and ok.json() == {"ok": True, "text": "Sent."}
        assert f.actions == [{"kind": "prompt", "text": "hi", "from": "Ana's iPhone"}]

    run(tmp_path, test)


def test_pairing_end_to_end(tmp_path: Path) -> None:
    async def test(f: Fixture) -> None:
        made = f.store.new_code()
        asked = await call(
            f.app,
            "POST",
            "/api/pair",
            body={"code": made["code"], "name": "Ana's iPhone"},
        )
        assert asked.status == 200
        request = asked.json()
        status = {"request_id": request["request_id"], "secret": request["secret"]}
        waiting = await call(f.app, "POST", "/api/pair/status", body=status)
        assert waiting.json() == {"state": "waiting"}
        # The machine confirms.
        [pending] = f.store.requests()
        f.store.answer(pending["id"], True)
        allowed = await call(f.app, "POST", "/api/pair/status", body=status)
        token = allowed.json()["token"]
        cookie = allowed.headers["set-cookie"]
        assert (
            "HttpOnly" in cookie and "SameSite=strict" in cookie and "Secure" in cookie
        )
        # Handed over once only.
        again = await call(f.app, "POST", "/api/pair/status", body=status)
        assert "token" not in again.json()
        me = await call(f.app, "GET", "/api/me", token=token)
        assert me.json()["name"] == "Ana's iPhone"
        projects = await call(f.app, "GET", "/api/projects", token=token)
        [project] = projects.json()["projects"]
        assert project["id"] == f.pid and project["name"] == "game"
        assert str(f.project) not in projects.body.decode()

    run(tmp_path, test)


def test_wrong_codes_and_rate_limit(tmp_path: Path) -> None:
    async def test(f: Fixture) -> None:
        waiting = f.store.new_code()
        # Wrong codes from anyone (here with made-up tailnet logins, which any local
        # process can send) stop the waiting code but lock nobody out.
        for i in range(5):
            wrong = await call(
                f.app,
                "POST",
                "/api/pair",
                body={"code": "AAAAAAAAAA"},
                headers={"tailscale-user-login": f"spoof{i}@x"},
            )
            assert wrong.status == 403
        stopped = await call(f.app, "POST", "/api/pair", body={"code": waiting["code"]})
        assert stopped.status == 403 and "stopped" in stopped.json()["error"]
        assert f.store.pairing_state(waiting["pairing_id"]) == "stopped"
        made = f.store.new_code()
        fresh = await call(f.app, "POST", "/api/pair", body={"code": made["code"]})
        assert fresh.status == 200
        # Ten posts a minute from one peer (two so far).
        for _ in range(8):
            await call(f.app, "POST", "/api/pair", body={"code": "x"})
        limited = await call(f.app, "POST", "/api/pair", body={"code": "x"})
        assert limited.status == 429
        refused = [e for e in audit.entries(home=f.home) if e["action"] == "pair"]
        assert [e["result"] for e in refused].count("ok") == 1  # The fresh code.
        assert refused[-1].get("reason") == "rate"

    run(tmp_path, test)


def test_scopes_and_refusals_are_audited(tmp_path: Path) -> None:
    async def test(f: Fixture) -> None:
        watcher = f.device(("watch",))
        path = f"/api/projects/{f.pid}/actions"
        denied = await call(
            f.app, "POST", path, body={"kind": "prompt", "text": "hi"}, token=watcher
        )
        assert denied.status == 403
        full = f.device()
        approve = await call(
            f.app,
            "POST",
            path,
            body={"kind": "art_approval", "sha256": "x"},
            token=full,
        )
        assert approve.status == 403 and "/art" in approve.json()["text"]
        shell = await call(
            f.app, "POST", path, body={"kind": "shell", "cmd": "ls"}, token=full
        )
        assert shell.status == 403
        assert f.actions == []
        entries = audit.entries(project=f.project)
        assert [(e["action"], e["result"]) for e in entries] == [
            ("prompt", "refused"),
            ("art_approval", "refused"),
            ("shell", "refused"),
        ]
        unknown = await call(
            f.app,
            "POST",
            "/api/projects/nope/actions",
            body={"kind": "cancel"},
            token=full,
        )
        assert unknown.status == 404

    run(tmp_path, test)


def test_revoked_device_is_shut_out(tmp_path: Path) -> None:
    async def test(f: Fixture) -> None:
        token = f.device()
        device = f.store.authenticate(token)
        assert device is not None
        f.store.revoke(device["id"])
        assert (await call(f.app, "GET", "/api/projects", token=token)).status == 401

    run(tmp_path, test)


def test_accept_needs_the_change_opened(tmp_path: Path) -> None:
    async def test(f: Fixture) -> None:
        token = f.device()
        f.server.previews[f.pid] = {
            "tx1": {
                "kind": "file_preview",
                "transaction_id": "tx1",
                "review_notes": [{"severity": "warn", "text": "Too dark"}],
            }
        }
        path = f"/api/projects/{f.pid}/actions"
        accept = {"kind": "decision", "transaction_id": "tx1", "accept": True}
        held = await call(f.app, "POST", path, body=accept, token=token)
        assert held.status == 409 and "Open the change" in held.json()["text"]
        # Discard needs no reading.
        discard = {**accept, "accept": False}
        assert (
            await call(f.app, "POST", path, body=discard, token=token)
        ).status == 200
        opened = await call(
            f.app, "GET", f"/api/projects/{f.pid}/changes/tx1", token=token
        )
        assert opened.json()["needs_opening"] is True
        assert (await call(f.app, "POST", path, body=accept, token=token)).status == 200
        assert [a["accept"] for a in f.actions] == [False, True]

    run(tmp_path, test)


def test_media_by_change_and_index_only(tmp_path: Path) -> None:
    async def test(f: Fixture) -> None:
        token = f.device()
        picture = f.project / ".joinery/transactions/tx1/stage/art/a.png"
        picture.write_bytes(b"\x89PNG\r\n")
        (f.project / "secret.png").write_bytes(b"secret")
        f.channel.publish(
            {
                "kind": "file_preview",
                "transaction_id": "tx1",
                "media": [
                    {"after": str(picture)},
                    {"after": str(f.project / "secret.png")},
                ],
            }
        )
        base = f"/api/projects/{f.pid}/media/tx1"
        shown = await call(f.app, "GET", f"{base}/0/after", token=token)
        assert shown.status == 200 and shown.body == b"\x89PNG\r\n"
        assert shown.headers["content-type"] == "image/png"
        for bad in ("1/after", "0/before", "x/after", "0/..%2f..%2fsecret"):
            answer = await call(f.app, "GET", f"{base}/{bad}", token=token)
            assert answer.status == 404, bad
        other = await call(
            f.app, "GET", f"/api/projects/{f.pid}/media/..%2f/0/after", token=token
        )
        assert other.status == 404
        assert (await call(f.app, "GET", f"{base}/0/after")).status == 401

    run(tmp_path, test)


def test_event_stream(tmp_path: Path) -> None:
    async def test(f: Fixture) -> None:
        token = f.device()
        f.channel.publish({"kind": "progress", "text": "Working"})
        f.channel.publish({"kind": "editor_action", "payload": {"secret": 1}})
        stream = await call(
            f.app,
            "GET",
            f"/api/projects/{f.pid}/events?since=0",
            token=token,
            until=lambda body: b"Working" in body,
        )
        text = stream.body.decode()
        assert stream.status == 200
        assert "event: hello" in text and "event: event" in text and "id: 1" in text
        assert "editor_action" not in text

    run(tmp_path, test)


@pytest.mark.parametrize(
    "path", ["/api/files", "/api/shell", "/api/settings", "/etc/passwd"]
)
def test_nothing_else_is_served(tmp_path: Path, path: str) -> None:
    async def test(f: Fixture) -> None:
        token = f.device()
        assert (await call(f.app, "GET", path, token=token)).status == 404

    run(tmp_path, test)


def test_nothing_is_served_while_remote_is_off(tmp_path: Path) -> None:
    """After `joinery remote stop`, a server still running (or one a login item started)
    answers no device and ends itself."""

    async def test(f: Fixture) -> None:
        token = f.device()
        f.store.set_config(enabled=False)
        assert (await call(f.app, "GET", "/api/projects", token=token)).status == 503
        opened = await call(
            f.app, "POST", f"/api/projects/{f.pid}/open", body={}, token=token
        )
        assert opened.status == 503
        ended: list[bool] = []
        f.server.on_disabled = lambda: ended.append(True)
        await asyncio.wait_for(f.server.follow(), 5)
        assert ended == [True]

    run(tmp_path, test)


def test_serve_exits_at_once_while_remote_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The login item runs `joinery remote serve --helper` at every login."""
    from joinery.cli import main
    from joinery.remote import server

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("JOINERY_REMOTE_HOME", str(home))
    Store(home).set_config(enabled=False)

    def listens(*_a: Any, **_kw: Any) -> int:
        raise AssertionError("nothing may listen while Remote is off")

    monkeypatch.setattr(server, "run", listens)
    assert main(["remote", "serve", "--helper"]) == 0


async def post_chunks(app: Any, chunks: int, length: str | None) -> tuple[int, int]:
    """POST /api/pair with ``chunks`` 16 kB pieces; (status, pieces read)."""
    headers = [
        (b"host", LOCAL.encode()),
        (b"origin", ORIGIN.encode()),
        (b"x-joinery-remote", b"1"),
    ]
    if length is not None:
        headers.append((b"content-length", length.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/pair",
        "raw_path": b"/api/pair",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", PORT),
        "scheme": "http",
        "http_version": "1.1",
        "root_path": "",
    }
    read = 0
    status = 0

    async def receive() -> dict[str, Any]:
        nonlocal read
        read += 1
        if read > chunks:
            return {"type": "http.disconnect"}
        return {
            "type": "http.request",
            "body": b"x" * 16_000,
            "more_body": read < chunks,
        }

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])

    await asyncio.wait_for(app(scope, receive, send), 10)
    return status, read


def test_long_bodies_are_refused_before_they_are_held(tmp_path: Path) -> None:
    """Before any device is known: a Content-Length over the limit is refused unread,
    and a chunked body is cut off at the limit, not read whole first."""

    async def test(f: Fixture) -> None:
        status, read = await post_chunks(f.app, 10_000, str(160_000_000))
        assert status == 413 and read == 0
        status, read = await post_chunks(f.app, 10_000, None)
        assert status == 413 and read <= 5

    run(tmp_path, test)


def test_pairing_status_polls_are_limited(tmp_path: Path) -> None:
    async def test(f: Fixture) -> None:
        status = {"request_id": "r_x", "secret": "s"}
        answers = [
            (await call(f.app, "POST", "/api/pair/status", body=status)).status
            for _ in range(61)
        ]
        assert answers[:60] == [200] * 60 and answers[60] == 429

    run(tmp_path, test)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda store, device: store.revoke(device), "revoked"),
        (lambda store, device: store.set_scopes(device, ["chat"]), "scope"),
        (lambda store, device: store.set_projects(device, ["other"]), "project"),
    ],
)
def test_an_open_stream_ends_at_once_when_the_device_may_no_longer_watch(
    tmp_path: Path, change: Callable[[Store, str], Any], reason: str
) -> None:
    async def test(f: Fixture) -> None:
        token = f.device()
        device = f.store.authenticate(token)
        assert device is not None

        async def later() -> None:
            await asyncio.sleep(0.3)
            change(f.store, device["id"])

        changing = asyncio.create_task(later())
        started = asyncio.get_running_loop().time()
        stream = await call(
            f.app,
            "GET",
            f"/api/projects/{f.pid}/events?since=0",
            token=token,
            until=lambda body: b"event: gone" in body,
        )
        took = asyncio.get_running_loop().time() - started
        await changing
        assert "event: gone" in stream.body.decode()
        assert f'"reason": "{reason}"' in stream.body.decode()
        assert took < 2.5

    run(tmp_path, test)
