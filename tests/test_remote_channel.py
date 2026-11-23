"""A chat host's Remote channel (bridge.remote_channel, ADR 0093)."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from bridge.remote_channel import Inputs, Origin, RemoteChannel
from joinery.remote import hosts
from joinery.remote.media import checked_media
from joinery.remote.paths import project_id

ALL = ["watch", "chat", "decide"]


def run(test: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(asyncio.wait_for(test(), 20))


def make(tmp_path: Path, editor: bool = False) -> tuple[RemoteChannel, list[Any]]:
    project = tmp_path / "game"
    (project / ".joinery" / "transactions" / "tx1" / "stage" / "art").mkdir(
        parents=True
    )
    sent: list[dict[str, Any]] = []
    channel = RemoteChannel(
        project,
        "codex",
        editor=editor,
        to_editor=sent.append,
        replies={"art_review": lambda: {"kind": "art_review", "approved": True}},
        home=tmp_path / "home",
    )
    return channel, sent


def act(action: dict[str, Any], scopes: list[str] = ALL) -> dict[str, Any]:
    return {
        "op": "action",
        "action": action,
        "device": {"id": "d_1", "name": "Ana's iPhone", "scopes": scopes},
    }


def test_advertises_privately_and_cleans_up(tmp_path: Path) -> None:
    async def body() -> None:
        channel, _ = make(tmp_path)
        await channel.start()
        assert channel.advert is not None
        assert channel.advert.name == project_id(channel.project) + ".json"
        # Windows keeps it private with an ACL instead; the mode bits say nothing there.
        assert os.name == "nt" or stat.S_IMODE(os.stat(channel.advert).st_mode) == 0o600
        assert channel.directory is not None
        assert (
            os.name == "nt" or stat.S_IMODE(os.stat(channel.directory).st_mode) == 0o700
        )
        [host] = hosts.discover(tmp_path / "home")
        assert host.public() == {
            "id": host.id,
            "name": "game",
            "agent": "codex",
            "editor": False,
            "started": channel.started,
        }
        await channel.close()
        assert not channel.advert.exists()
        assert hosts.discover(tmp_path / "home") == []

    run(body)


def test_wrong_token_is_refused(tmp_path: Path) -> None:
    async def body() -> None:
        channel, _ = make(tmp_path)
        await channel.start()
        [host] = hosts.discover(tmp_path / "home")
        forged = hosts.Host(**{**host.__dict__, "token": "nope"})
        answer = await hosts.request(forged, {"op": "state"})
        assert answer["ok"] is False
        assert "authentication" in answer["text"]
        await channel.close()

    run(body)


def test_events_are_projected(tmp_path: Path) -> None:
    channel, _ = make(tmp_path)
    after = channel.project / ".joinery/transactions/tx1/stage/art/a.png"
    assert channel.projected({"kind": "editor_action", "payload": {}}) is None
    assert channel.projected({"kind": "remote_action"}) is None
    shown = channel.projected(
        {
            "kind": "file_preview",
            "transaction_id": "tx1",
            "summary": "Taller hedge",
            "media": [
                {"path": "art/a.png", "media": "image", "after": str(after)},
                {"path": "art/m.blend", "before_model": "/tmp/x/m.glb"},
            ],
            "attachments": [str(channel.project / "art/ref.png"), "/Users/me/secret"],
            "text": "/variants 3 a taller hedge",
        }
    )
    assert shown is not None
    assert shown["media"][0] == {
        "path": "art/a.png",
        "media": "image",
        "after": "media/tx1/0/after",
    }
    assert "before_model" not in shown["media"][1]
    assert shown["attachments"] == ["art/ref.png", ""]
    assert shown["text"] == "/variants 3 a taller hedge"
    assert "/Users/" not in json.dumps(shown)


def test_subscribe_replays_from_seq(tmp_path: Path) -> None:
    async def body() -> None:
        channel, _ = make(tmp_path)
        await channel.start()
        for n in range(3):
            channel.publish({"kind": "progress", "text": f"step {n}"})
        [host] = hosts.discover(tmp_path / "home")

        async def take(since: int, count: int) -> list[dict[str, Any]]:
            got: list[dict[str, Any]] = []
            stream = hosts.subscribe(host, since)
            async for item in stream:
                got.append(item)
                if len(got) == count:
                    break
            await stream.aclose()
            return got

        hello, *rest = await take(0, 4)
        assert hello["kind"] == "hello" and hello["seq"] == 3 and not hello["gap"]
        assert [item["seq"] for item in rest] == [1, 2, 3]
        hello, item = await take(2, 2)
        assert item["seq"] == 3 and item["event"]["text"] == "step 2"
        # Live events follow the replay.
        stream = hosts.subscribe(host, 3)
        assert (await anext(stream))["kind"] == "hello"
        channel.publish({"kind": "done", "ok": True})
        assert (await anext(stream))["event"] == {"kind": "done", "ok": True}
        await stream.aclose()
        # Further back than the ring holds: the device is told.
        channel.ring = deque(list(channel.ring)[-1:], maxlen=1)
        hello, _ = await take(1, 2)
        assert hello["gap"]
        await channel.close()

    run(body)


def test_actions_are_checked_then_queued(tmp_path: Path) -> None:
    async def body() -> None:
        channel, _ = make(tmp_path)
        await channel.start()
        [host] = hosts.discover(tmp_path / "home")
        refused = await hosts.request(
            host, act({"kind": "art_approval", "sha256": "x"})
        )
        assert refused == {
            "ok": False,
            "refused": True,
            "text": "Approve the art direction at the editor (/art → Approve).",
        }
        scoped = await hosts.request(
            host,
            act({"kind": "decision", "transaction_id": "t", "accept": True}, ["watch"]),
        )
        assert scoped["refused"]
        assert channel.inbox.empty()
        art = await hosts.request(host, act({"kind": "art_review"}, ["watch"]))
        assert art == {"ok": True, "event": {"kind": "art_review", "approved": True}}

        async def host_loop() -> None:
            action, origin = await channel.inbox.get()
            assert action == {"kind": "prompt", "text": "hi"}
            assert origin.device_name == "Ana's iPhone"
            origin.done(True, "")

        loop = asyncio.create_task(host_loop())
        answer = await hosts.request(
            host, act({"kind": "prompt", "text": "hi", "targets": ["x"]})
        )
        await loop
        assert answer == {"ok": True, "text": ""}
        await channel.close()

    run(body)


def test_relay_waits_for_the_sidebar(tmp_path: Path) -> None:
    async def body() -> None:
        channel, sent = make(tmp_path, editor=True)
        await channel.start()
        [host] = hosts.discover(tmp_path / "home")

        async def host_loop() -> None:
            action, origin = await channel.inbox.get()
            channel.relay(action, origin)
            [relayed] = sent
            assert relayed["kind"] == "remote_action"
            assert relayed["device"] == {"id": "d_1", "name": "Ana's iPhone"}
            # The sidebar did it and answers.
            channel.acked(relayed["ref"], True, "Queued.")

        loop = asyncio.create_task(host_loop())
        answer = await hosts.request(
            host, act({"kind": "decision", "transaction_id": "tx1", "accept": False})
        )
        await loop
        assert answer == {"ok": True, "text": "Queued."}
        await channel.close()

    run(body)


def test_a_late_sidebar_answer_is_told_truthfully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sidebar answers after the relay's wait: the device was told "pending", not
    that it failed, and then hears what the sidebar did."""
    from bridge import remote_channel

    monkeypatch.setattr(remote_channel, "RELAY_SECONDS", 0.3)

    async def body() -> None:
        channel, sent = make(tmp_path, editor=True)
        await channel.start()
        [host] = hosts.discover(tmp_path / "home")
        events: list[dict[str, Any]] = []

        async def follow() -> None:
            async for item in hosts.subscribe(host, 0):
                if "event" in item:
                    events.append(item["event"])

        following = asyncio.create_task(follow())

        async def host_loop() -> None:
            action, origin = await channel.inbox.get()
            channel.relay(action, origin)

        loop = asyncio.create_task(host_loop())
        answer = await hosts.request(host, act({"kind": "prompt", "text": "Hi"}))
        await loop
        assert answer["ok"] is False and answer["pending"] is True
        [relayed] = sent
        channel.acked(relayed["ref"], True, "Sent.")
        for _ in range(50):
            if events:
                break
            await asyncio.sleep(0.05)
        assert events == [
            {
                "kind": "notice",
                "level": "info",
                "text": "At the editor, for Ana's iPhone: Sent.",
            }
        ]
        following.cancel()
        await channel.close()

    run(body)


def test_inputs_merge_editor_first(tmp_path: Path) -> None:
    class Early:
        def __init__(self) -> None:
            self.lines: asyncio.Queue[bytes] = asyncio.Queue()

        async def readline(self) -> bytes:
            return await self.lines.get()

    async def body() -> None:
        channel, _ = make(tmp_path)
        early = Early()
        inputs = Inputs(early, channel)
        origin = Origin("d_1", "Phone", ("chat",))
        await channel.inbox.put(({"kind": "cancel"}, origin))
        line, came = await inputs.readline()
        assert json.loads(line) == {"kind": "cancel"} and came is origin
        await early.lines.put(b'{"kind": "prompt"}\n')
        line, came = await inputs.readline()
        assert came is None and b"prompt" in line
        # Remote turned off while waiting: the editor's input still comes through.
        waiting = asyncio.create_task(inputs.readline())
        await asyncio.sleep(0.05)
        await inputs.drop_channel()
        await early.lines.put(b"")
        assert await waiting == (b"", None)
        await inputs.close()

    run(body)


def test_media_only_from_the_change_folder(tmp_path: Path) -> None:
    async def body() -> None:
        channel, _ = make(tmp_path)
        project = channel.project
        folder = project / ".joinery/transactions/tx1/stage/art"
        (folder / "a.png").write_bytes(b"\x89PNG")
        (project / "secret.png").write_bytes(b"x")
        (folder / "link.png").symlink_to(project / "secret.png")
        channel.projected(
            {
                "kind": "file_preview",
                "transaction_id": "tx1",
                "media": [
                    {"after": str(folder / "a.png")},
                    {"after": str(project / "secret.png")},
                    {"after": str(folder / "link.png")},
                    {"after": str(folder / ".." / ".." / ".." / ".." / "x.png")},
                ],
            }
        )
        assert channel.media_path("tx1", 0, "after") == folder / "a.png"
        for index in (1, 2, 3):
            with pytest.raises(ValueError):
                channel.media_path("tx1", index, "after")
        for args in (("tx1", 0, "before"), ("tx1", 9, "after"), ("tx2", 0, "after")):
            with pytest.raises(ValueError):
                channel.media_path(*args)
        with pytest.raises(ValueError):
            channel.media_path("tx1", 0, "../after")
        await channel.start()
        [host] = hosts.discover(tmp_path / "home")
        answer = await hosts.request(
            host, {"op": "media", "transaction_id": "tx1", "index": 0, "side": "after"}
        )
        assert answer == {"ok": True, "path": str(folder / "a.png")}
        await channel.close()

    run(body)


@pytest.mark.parametrize("tx", ["", ".", "..", "a/b", "a\\b"])
def test_checked_media_refuses_odd_ids(tmp_path: Path, tx: str) -> None:
    with pytest.raises(ValueError):
        checked_media(tmp_path, tx, tmp_path / ".joinery/transactions" / tx / "a.png")


def test_checked_media_refuses_other_kinds(tmp_path: Path) -> None:
    folder = tmp_path / ".joinery/transactions/tx1/stage"
    folder.mkdir(parents=True)
    (folder / "main.gd").write_text("extends Node")
    with pytest.raises(ValueError):
        checked_media(tmp_path, "tx1", folder / "main.gd")
    # A linked change folder is refused too.
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "a.png").write_bytes(b"x")
    (tmp_path / ".joinery/transactions/tx2").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError):
        checked_media(tmp_path, "tx2", tmp_path / ".joinery/transactions/tx2/a.png")
