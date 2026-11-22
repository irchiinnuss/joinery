"""The always-on helper (joinery.remote.helper, handoff, login_item, awake; ADR 0093).

A real chat host (tests/godot/scripted_host.py, the real ``bridge.stdio`` loop) is started
headless for a phone, idles out, and hands its project to an editor that opens it.
"""

from __future__ import annotations

import asyncio
import json
import os
import plistlib
import sys
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from joinery.remote import awake, hosts, login_item
from joinery.remote.handoff import BUSY, take_over
from joinery.remote.helper import HeadlessLauncher
from joinery.remote.paths import project_id, remember
from joinery.remote.seams import NoLauncher
from joinery.remote.server import RemoteServer
from joinery.remote.store import Store, digest

ROOT = Path(__file__).resolve().parents[1]
DEVICE = {"id": "d_1", "name": "Ana's iPhone", "scopes": ["watch", "chat", "decide"]}
SCRIPT = {"turns": {"Keep going": [{"hang": True}], "*": [{"message": "Done."}]}}


def run(test: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(asyncio.wait_for(test(), 90))


def setup(tmp_path: Path) -> tuple[Path, Path, HeadlessLauncher]:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    Store(home).set_config(enabled=True)
    project = tmp_path / "Harbour"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    script = tmp_path / "turns.json"
    script.write_text(json.dumps(SCRIPT))

    def command(where: Path, agent: str) -> list[str]:
        return [
            sys.executable,
            str(ROOT / "tests/godot/scripted_host.py"),
            "--project",
            str(where),
            "--script",
            str(script),
            "--agent",
            agent,
        ]

    return home, project, HeadlessLauncher(home, command=command, start_seconds=30)


def act(action: dict[str, Any]) -> dict[str, Any]:
    return {"op": "action", "action": action, "device": DEVICE}


def test_a_project_never_used_here_is_not_started(tmp_path: Path) -> None:
    _home, project, launcher = setup(tmp_path)

    async def body() -> None:
        with pytest.raises(ValueError, match="Open this project in Joinery once"):
            await launcher.ensure_host(project)
        with pytest.raises(ValueError, match="isn't on this Mac"):
            await launcher.ensure_host(tmp_path / "gone")

    run(body)


def test_starts_idles_out_and_keeps_reviews(tmp_path: Path) -> None:
    home, project, launcher = setup(tmp_path)
    remember(project, "codex", home)

    async def body() -> None:
        host = await launcher.ensure_host(project)
        assert host is not None and host.helper and not host.editor
        assert host.agent == "codex"
        # Asked again: the same host, never a second one beside it.
        again = await launcher.ensure_host(project)
        assert again is not None and again.pid == host.pid
        answer = await hosts.request(host, act({"kind": "prompt", "text": "Hello"}))
        assert answer["ok"]
        # Busy hosts and watched ones stay; an idle one goes.
        await launcher.tick(lambda _id: 0.0)
        assert hosts.find(host.id, home) is not None
        await launcher.tick(lambda _id: 3600.0)
        assert hosts.find(host.id, home) is None
        assert launcher.children == {}

    run(body)


def test_an_editor_takes_the_project_over(tmp_path: Path) -> None:
    home, project, launcher = setup(tmp_path)
    remember(project, "claude", home)
    said: list[dict[str, Any]] = []

    async def body() -> None:
        host = await launcher.ensure_host(project)
        assert host is not None
        # Nothing at work: it lets go at once.
        assert await take_over(project, said.append, home, wait=20)
        assert hosts.find(host.id, home) is None
        assert said == []
        # A phone's request still running: the editor is told, and waits for it.
        host = await launcher.ensure_host(project)
        assert host is not None
        await hosts.request(host, act({"kind": "prompt", "text": "Keep going"}))
        await asyncio.sleep(0.5)
        taking = asyncio.create_task(take_over(project, said.append, home, wait=30))
        await asyncio.sleep(1.5)
        assert said == [{"kind": "handover", "state": "waiting", "text": BUSY}]
        assert hosts.find(host.id, home) is not None
        await hosts.request(host, act({"kind": "cancel"}))
        assert await taking
        assert hosts.find(host.id, home) is None
        assert said[-1]["state"] == "done"
        await launcher.close()

    run(body)


def editor_input() -> tuple[asyncio.StreamReader, Any]:
    from bridge.early_input import EarlyInput

    reader = asyncio.StreamReader()
    return reader, EarlyInput(reader)


def line(data: dict[str, Any]) -> bytes:
    return (json.dumps(data) + "\n").encode()


def test_the_editor_can_stop_the_phones_request_and_stays_heard(
    tmp_path: Path,
) -> None:
    """The editor's host reads the editor while it waits: "Stop it now" stops the phone's
    request, every other line waits for the loop in order, and a phone starts nothing
    new once the editor is taking over."""
    home, project, launcher = setup(tmp_path)
    remember(project, "claude", home)
    said: list[dict[str, Any]] = []

    async def body() -> None:
        host = await launcher.ensure_host(project)
        assert host is not None
        await hosts.request(host, act({"kind": "prompt", "text": "Keep going"}))
        await asyncio.sleep(0.5)
        reader, early = editor_input()
        taking = asyncio.create_task(
            take_over(project, said.append, home, editor=early)
        )
        await asyncio.sleep(1.5)
        assert said[0]["state"] == "waiting"
        refused = await hosts.request(host, act({"kind": "prompt", "text": "More"}))
        assert refused["ok"] is False and "taking this project over" in refused["text"]
        reader.feed_data(line({"kind": "editor_focus", "focused": True}))
        reader.feed_data(line({"kind": "handover_stop"}))
        assert await asyncio.wait_for(taking, 30)
        assert hosts.find(host.id, home) is None
        assert [e["state"] for e in said] == ["waiting", "stopping", "done"]
        # The editor's own line is kept for its loop; the stop is not.
        assert json.loads(await early.readline())["kind"] == "editor_focus"
        assert early._kept == deque()
        await launcher.close()

    run(body)


def test_the_editor_closing_ends_the_wait(tmp_path: Path) -> None:
    home, project, launcher = setup(tmp_path)
    remember(project, "claude", home)

    async def body() -> None:
        host = await launcher.ensure_host(project)
        assert host is not None
        await hosts.request(host, act({"kind": "prompt", "text": "Keep going"}))
        await asyncio.sleep(0.5)
        reader, early = editor_input()
        taking = asyncio.create_task(
            take_over(project, lambda e: None, home, editor=early)
        )
        await asyncio.sleep(1.0)
        reader.feed_eof()
        assert await asyncio.wait_for(taking, 10)
        assert early.ended
        # The phone's request was never stopped by it.
        assert hosts.find(host.id, home) is not None
        await launcher.close()

    run(body)


def test_an_editors_host_is_never_released(tmp_path: Path) -> None:
    home, project, _ = setup(tmp_path)

    async def body() -> None:
        from bridge.remote_channel import RemoteChannel

        channel = RemoteChannel(
            project, "codex", editor=True, to_editor=lambda e: None, home=home
        )
        await channel.start()
        [host] = hosts.discover(home)
        answer = await hosts.request(host, {"op": "release"})
        assert answer["ok"] is False
        assert not await take_over(project, lambda e: None, home, wait=1)
        await channel.close()

    run(body)


def test_remote_off_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "never"
    monkeypatch.setenv("JOINERY_REMOTE_HOME", str(missing))
    assert not asyncio.run(take_over(tmp_path, lambda e: None, None, wait=1))
    assert not missing.exists()


def test_the_server_opens_known_projects_only_with_the_helper(tmp_path: Path) -> None:
    home, project, launcher = setup(tmp_path)
    remember(project, "codex", home)
    store = Store(home)
    made = store.new_code()
    request = store.redeem(made["code"], "P", "", digest("tok"), digest("s"))
    store.answer(request["id"], True)
    from tests.test_remote_server import call

    async def body() -> None:
        plain = RemoteServer(store, port=47615, home=home).app()
        listed = await call(plain, "GET", "/api/projects", token="tok")
        [entry] = listed.json()["projects"]
        assert entry["open"] is False and entry["can_open"] is False
        refused = await call(
            plain, "POST", f"/api/projects/{entry['id']}/open", token="tok"
        )
        assert refused.status == 409
        helped = RemoteServer(store, port=47615, home=home, launcher=launcher).app()
        opened = await call(
            helped, "POST", f"/api/projects/{entry['id']}/open", token="tok"
        )
        assert opened.status == 200 and opened.json()["project"]["id"] == project_id(
            project
        )
        unknown = await call(helped, "POST", "/api/projects/nope/open", token="tok")
        assert unknown.status == 404
        await launcher.close()

    run(body)


def test_idle_and_quiet() -> None:
    server = RemoteServer(Store.__new__(Store), port=1)
    server.states["h"] = {"working": False, "waiting": [], "todo": False, "last": 1.0}
    assert server.idle_for("h") > 1000
    server.states["h"]["working"] = True
    assert server.idle_for("h") == 0.0
    server.states["h"]["working"] = False
    server.watching["h"] = 1
    assert server.idle_for("h") == 0.0
    assert server.idle_for("unknown") == 0.0
    server._track({}, server.states["h"], {"kind": "presence", "focused": True})
    assert server.quiet("h")
    assert isinstance(server.launcher, NoLauncher)


def test_login_item(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(login_item.DIR_ENV, str(tmp_path))
    monkeypatch.setenv(login_item.NO_LOAD_ENV, "1")
    done = login_item.install(tmp_path / "server.log")
    assert done["installed"]
    target = Path(done["path"])
    if sys.platform == "darwin":
        plist = plistlib.loads(target.read_bytes())
        assert plist["Label"] == "dev.joinery.remote"
        assert plist["ProgramArguments"][-2:] == ["serve", "--helper"]
        assert plist["RunAtLoad"] is True
    else:
        assert "serve --helper" in target.read_text()
    assert not login_item.uninstall()["installed"]
    assert not target.exists()


def test_keep_awake(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[list[str]] = []

    class Process:
        def __init__(self, argv: list[str], **_kw: Any) -> None:
            started.append(argv)
            self.alive = True

        def poll(self) -> int | None:
            return None if self.alive else 0

        def terminate(self) -> None:
            self.alive = False

        def wait(self, _timeout: float = 0) -> int:
            return 0

    monkeypatch.setattr(awake.subprocess, "Popen", Process)
    monkeypatch.setattr(awake.shutil, "which", lambda name: "/usr/bin/" + name)
    keep = awake.KeepAwake()
    if os.name == "nt":
        pytest.skip("Windows asks the system directly")
    keep.follow(True)
    keep.follow(True)
    assert len(started) == 1
    if sys.platform == "darwin":
        assert started[0][:2] == ["caffeinate", "-s"]
    keep.follow(False)
    assert not keep.on
