"""Web Push, the one thing Remote sends off the machine (ADR 0093): its key survives two
servers starting at once, and a push goes only where the subscription says: no
redirect followed, no proxy from the environment. Only loopback servers are used."""

from __future__ import annotations

import threading
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from joinery.remote import push
from joinery.remote.push import KEY_FILE, Vapid


def test_a_key_made_meanwhile_is_kept_not_a_crash(tmp_path: Path) -> None:
    first = Vapid(tmp_path)
    before = (tmp_path / KEY_FILE).read_bytes()
    # Another server got there between the look and the write.
    Vapid._create(tmp_path / KEY_FILE)
    assert (tmp_path / KEY_FILE).read_bytes() == before
    assert Vapid(tmp_path).public_key() == first.public_key()


def test_servers_starting_together_share_one_key(tmp_path: Path) -> None:
    keys: list[str] = []
    failures: list[BaseException] = []
    start = threading.Barrier(8)

    def server() -> None:
        try:
            start.wait()
            keys.append(Vapid(tmp_path).public_key())
        except BaseException as exc:  # noqa: BLE001 - reported below
            failures.append(exc)

    threads = [threading.Thread(target=server) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert len(set(keys)) == 1
    assert [p.name for p in tmp_path.iterdir()] == [KEY_FILE]


@contextmanager
def serving(status: int, location: str = "") -> Iterator[tuple[str, list[str]]]:
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            seen.append(self.path)
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


def test_a_redirect_is_not_followed() -> None:
    with serving(201) as (elsewhere, hit):
        with serving(307, f"{elsewhere}/stolen") as (service, asked):
            status = push._open(
                urllib.request.Request(f"{service}/push", data=b"x", method="POST")
            )
        assert status == 307
        assert asked == ["/push"] and hit == []


def test_no_proxy_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    with serving(201) as (proxy, through_proxy), serving(201) as (service, direct):
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
            monkeypatch.setenv(name, proxy)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        status = push._open(
            urllib.request.Request(f"{service}/push", data=b"x", method="POST")
        )
    assert status == 201
    assert direct == ["/push"] and through_proxy == []
