"""Remote on the tailnet (ADR 0093): what Settings shows, what the QR code carries and what
the server accepts all follow what ``tailscale serve`` actually serves. A fake
``tailscale`` program stands in for the real one; nothing touches the network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from remote_tailscale_fake import NAME, calls, install, serve_config
from test_remote_server import Fixture, call, run

from joinery.cli import main
from joinery.remote import tailscale
from joinery.remote.store import Store

PORT = 47615


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "remote"
    folder.mkdir(mode=0o700)
    monkeypatch.setenv("JOINERY_REMOTE_HOME", str(folder))
    Store(folder).set_config(enabled=True, port=PORT)
    return folder


def fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: Any
) -> tuple[Path, str]:
    folder = tmp_path / "fake-tailscale"
    program = install(folder, **kwargs)
    monkeypatch.setenv(tailscale.PROGRAM_ENV, program)
    return folder, program


def cli(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    code = main(["remote", *args, "--json"])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


# --- Reading `tailscale serve status --json` ------------------------------------------------


def test_served_reads_the_serve_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, program = fake(tmp_path, monkeypatch, serve=serve_config(PORT))
    assert tailscale.served(PORT, program) == {
        "known": True,
        "origin": f"https://{NAME}",
        "funnel": False,
    }
    # Another port is somebody else's server.
    assert tailscale.served(PORT + 1, program)["origin"] == ""


def test_served_names_another_https_port_and_a_foreground_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, program = fake(tmp_path, monkeypatch)
    foreground = {"Foreground": {"session": serve_config(PORT, https=8443)}}
    (folder / "serve.json").write_text(json.dumps(foreground))
    assert tailscale.served(PORT, program)["origin"] == f"https://{NAME}:8443"


def test_served_refuses_funnel_and_a_sub_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, program = fake(tmp_path, monkeypatch, serve=serve_config(PORT, funnel=True))
    assert tailscale.served(PORT, program) == {
        "known": True,
        "origin": "",
        "funnel": True,
    }
    mounted = serve_config(PORT)
    site = mounted["Web"][f"{NAME}:443"]
    site["Handlers"] = {"/remote": site["Handlers"]["/"]}
    (folder / "serve.json").write_text(json.dumps(mounted))
    assert tailscale.served(PORT, program)["origin"] == ""


def test_served_says_unknown_without_tailscale(tmp_path: Path) -> None:
    assert tailscale.served(PORT, "")["known"] is False
    assert tailscale.served(PORT, str(tmp_path / "missing"))["known"] is False


# --- What Settings → Remote shows ------------------------------------------------------------


def test_status_offers_serve_until_tailscale_serves_it(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tailscale runs but nothing is served: the next step is "Put it on my tailnet",
    not an address that would refuse the phone."""
    folder, _ = fake(tmp_path, monkeypatch)
    code, data = cli(capsys, "status")
    assert code == 0
    assert data["served"] is False and data["url"] == f"http://127.0.0.1:{PORT}"
    assert Store(home).config()["origin"] == ""
    # `tailscale serve` was set up by hand, outside Joinery: seen and stored.
    (folder / "serve.json").write_text(json.dumps(serve_config(PORT)))
    code, data = cli(capsys, "status")
    assert data["served"] is True and data["url"] == f"https://{NAME}"
    assert Store(home).config()["origin"] == f"https://{NAME}"
    # Served no more: the stored name goes too.
    (folder / "serve.json").write_text("{}")
    code, data = cli(capsys, "status")
    assert data["served"] is False and Store(home).config()["origin"] == ""


def test_status_says_funnel_is_refused(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake(tmp_path, monkeypatch, serve=serve_config(PORT, funnel=True))
    _, data = cli(capsys, "status")
    assert data["served"] is False and data["funnel"] is True
    assert Store(home).config()["origin"] == ""


def test_put_it_on_my_tailnet_stores_what_is_served(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder, _ = fake(tmp_path, monkeypatch)
    code, data = cli(capsys, "tailscale", "--run", "--yes")
    assert code == 0 and data["ran"] is True and data["origin"] == f"https://{NAME}"
    assert calls(folder)[-2] == [
        "serve",
        "--bg",
        "--https=443",
        f"http://127.0.0.1:{PORT}",
    ]
    assert Store(home).config()["origin"] == f"https://{NAME}"


# --- The QR code -------------------------------------------------------------------------------


def test_pairing_waits_for_a_served_address(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder, _ = fake(tmp_path, monkeypatch)
    code, data = cli(capsys, "pair", "--no-wait")
    assert code == 1 and "tailnet" in data["error"]
    assert Store(home).read()["pairings"] == []
    # On this machine only, when asked.
    code, data = cli(capsys, "pair", "--no-wait", "--local")
    assert code == 0 and data["url"].startswith(f"http://127.0.0.1:{PORT}/#pair=")
    (folder / "serve.json").write_text(json.dumps(serve_config(PORT)))
    code, data = cli(capsys, "pair", "--no-wait")
    assert code == 0 and data["url"].startswith(f"https://{NAME}/#pair=")


# --- What the server accepts -------------------------------------------------------------------


def test_the_address_shown_is_one_the_server_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end: the address Settings shows is served, and a phone's request through
    ``tailscale serve`` (Host passed on unchanged, the page's Origin, X-Forwarded-* and
    the identity headers added) pairs."""
    folder, _ = fake(tmp_path / "ts", monkeypatch, serve=serve_config(PORT))

    async def test(f: Fixture) -> None:
        monkeypatch.setenv("JOINERY_REMOTE_HOME", str(f.home))
        _, data = cli(capsys, "status")
        url = data["url"]
        assert url == f"https://{NAME}"
        host = url.removeprefix("https://")
        proxied = {
            "host": host,
            "x-forwarded-host": host,
            "x-forwarded-proto": "https",
            "x-forwarded-for": "100.101.102.103",
            "tailscale-user-login": "ana@example.com",
        }
        page = await call(f.app, "GET", "/", headers=proxied)
        assert page.status == 200
        made = f.store.new_code()
        asked = await call(
            f.app,
            "POST",
            "/api/pair",
            body={"code": made["code"], "name": "Ana's iPhone"},
            headers={**proxied, "origin": url},
        )
        assert asked.status == 200, asked.body
        # Any other name or origin is still refused.
        other = await call(f.app, "GET", "/", headers={"host": "evil.ts.net"})
        assert other.status == 421
        cross = await call(
            f.app,
            "POST",
            "/api/pair",
            body={"code": "x"},
            headers={**proxied, "origin": "https://evil.ts.net"},
        )
        assert cross.status == 403

    run(tmp_path, test)
    assert calls(folder)
