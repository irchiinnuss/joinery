"""Keeping the Remote server running at this version (joinery.remote.service)."""

from __future__ import annotations

import json
import os
import plistlib
import sys
from pathlib import Path
from typing import Any

import pytest

from joinery import __version__
from joinery.remote import login_item, service
from joinery.remote.store import Store


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    monkeypatch.delenv(service.NO_SERVER_ENV, raising=False)
    monkeypatch.setenv(login_item.DIR_ENV, str(tmp_path / "login"))
    monkeypatch.setenv(login_item.NO_LOAD_ENV, "1")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    made = Store(home)
    made.set_config(enabled=True, port=47999)
    return made


class Spawns:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.argv: list[list[str]] = []
        self.killed: list[int] = []
        monkeypatch.setattr(service.subprocess, "Popen", self.popen)
        monkeypatch.setattr(service.os, "kill", self.kill)

    def kill(self, pid: int, sig: int) -> None:
        # Signal 0 is the liveness look (ProcessWatch); anything else would stop it.
        if sig:
            self.killed.append(pid)

    def popen(self, argv: list[str], **_kw: Any) -> None:
        self.argv.append(argv)


def running(store: Store, version: str) -> None:
    # This test's own pid reads as alive; os.kill is a stand-in, so nothing is signalled.
    (store.home / service.SERVER_FILE).write_text(
        json.dumps({"pid": os.getpid(), "port": 47999, "version": version})
    )


def test_starts_the_server_when_none_runs(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = Spawns(monkeypatch)
    service.ensure_server(store, wait=False)
    [argv] = spawns.argv
    assert argv[-3:] == ["serve", "--port", "47999"]


def test_leaves_a_current_server_alone(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = Spawns(monkeypatch)
    running(store, __version__)
    assert service.ensure_server(store, wait=False)["running"]
    assert spawns.argv == [] and spawns.killed == []


def test_replaces_a_server_from_before_an_update(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = Spawns(monkeypatch)
    running(store, "0.0.1")
    monkeypatch.setattr(service.time, "sleep", lambda _s: None)
    service.ensure_server(store, wait=False)
    assert spawns.killed[:1] == [os.getpid()]
    assert len(spawns.argv) == 1


def test_never_from_tests_or_the_helpers_hosts(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = Spawns(monkeypatch)
    monkeypatch.setenv("JOINERY_REMOTE_HELPER", "1")
    service.host_wants_server(store.home)
    monkeypatch.delenv("JOINERY_REMOTE_HELPER")
    monkeypatch.setenv(service.NO_SERVER_ENV, "1")
    service.ensure_server(store, wait=False)
    assert spawns.argv == []


def test_the_login_item_runs_the_installed_app_through_uv(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "Joinery.app/Contents/Resources/joinery"
    monkeypatch.setenv("JOINERY_HOME", str(app))
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/cache/python-1")
    monkeypatch.setattr(login_item.shutil, "which", lambda _n: "/opt/homebrew/bin/uv")
    login_item.install(tmp_path / "log")
    assert login_item.command()[:6] == [
        "/opt/homebrew/bin/uv",
        "run",
        "--frozen",
        "--quiet",
        "--project",
        str(app),
    ]
    assert not login_item.refresh(tmp_path / "log")
    # An update: the app's environment moved; the login item follows.
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/cache/python-2")
    assert login_item.refresh(tmp_path / "log")
    if sys.platform == "darwin":
        plist = plistlib.loads(Path(login_item.status()["path"]).read_bytes())
        assert (
            plist["EnvironmentVariables"]["UV_PROJECT_ENVIRONMENT"] == "/cache/python-2"
        )
        assert "PATH" in plist["EnvironmentVariables"]


def test_a_checkout_runs_its_own_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOINERY_HOME", raising=False)
    assert login_item.command()[:3] == [sys.executable, "-m", "joinery.remote.cli"]


def test_tailscale_asks_for_https_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tailnet without HTTPS: Tailscale prints a link and waits; the user gets it."""
    import subprocess

    from joinery.remote import tailscale

    def waits(*_a: Any, **_kw: Any) -> None:
        raise subprocess.TimeoutExpired(
            ["tailscale"],
            30,
            output=b"Serve is not enabled on your tailnet.\n"
            b"To enable, visit:\n\n https://login.tailscale.com/f/serve?node=abc\n",
        )

    monkeypatch.setattr(tailscale.subprocess, "run", waits)
    ok, said = tailscale.serve(47615, "tailscale")
    assert not ok and said.endswith("https://login.tailscale.com/f/serve?node=abc")
