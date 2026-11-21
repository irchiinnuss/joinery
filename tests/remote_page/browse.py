"""The phone page in a real (headless) browser against the real Remote server.

    uv run python tests/remote_page/browse.py [--out DIR]

A real chat host channel plays a short session (a request, a step, a reply, a change
with a before and after and a reviewer note, a to-do), and answers Accept and Go as the
host would. The server runs on a free loopback port; headless Chrome, driven over its
DevTools protocol by drive.mjs (node, no package), goes through the screens at 390 px in
dark and light. Screenshots land in DIR (default out/remote-page).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.remote_channel import RemoteChannel
from joinery.remote.server import RemoteServer
from joinery.remote.store import Store, digest

CHROME = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
]


def chrome() -> str:
    for name in CHROME:
        found = shutil.which(name) or (name if Path(name).is_file() else None)
        if found:
            return found
    return ""


def picture(path: Path, colour: tuple[int, int, int], size: int = 96) -> None:
    """A small solid PNG (a stand-in render)."""
    row = b"\x00" + bytes(colour) * size
    raw = row * size

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def session(project: Path) -> list[dict[str, Any]]:
    tx = project / ".joinery/transactions/tx1"
    picture(tx / "original/art/hedge.png", (60, 110, 60))
    picture(tx / "stage/art/hedge.png", (90, 170, 80))
    return [
        {"kind": "connection", "state": "connected", "agent": "claude"},
        {"kind": "chat", "chat_id": "c1", "mode": "fresh", "message": ""},
        {"kind": "request", "chat_id": "c1", "text": "Make the hedge taller"},
        {"kind": "execution", "name": "Claude"},
        {"kind": "trail", "text": "Opening hedge.blend in Blender", "state": "done"},
        {
            "kind": "message_delta",
            "text": "I raised the hedge by 30 cm and kept its base.",
            "new_message": True,
        },
        {
            "kind": "file_preview",
            "transaction_id": "tx1",
            "summary": "Taller hedge along the harbour wall",
            "joinery_facts": ["1 texture changed", "Made in Krita"],
            "checks": ["lint", "import"],
            "review_notes": [
                {"severity": "warn", "text": "The top row is darker than the palette."}
            ],
            "diff": "--- a/hedge.gd\n+++ b/hedge.gd\n@@ -1 +1 @@\n-var height := 1.0\n+var height := 1.3",
            "changes": [{"path": "art/hedge.png", "change": "modified"}],
            "media": [
                {
                    "path": "art/hedge.png",
                    "media": "image",
                    "before": str(tx / "original/art/hedge.png"),
                    "after": str(tx / "stage/art/hedge.png"),
                }
            ],
        },
        {"kind": "done", "ok": True},
        {
            "kind": "todo",
            "todo_id": "t1",
            "state": "waiting",
            "steps": ["Plant hedges along the quay", "Add a gate"],
            "done_when": [{"id": "d1", "text": "The quay has a hedge", "status": ""}],
        },
    ]


async def host_loop(channel: RemoteChannel) -> None:
    """Answers as a headless host would."""
    while True:
        action, origin = await channel.inbox.get()
        if action["kind"] == "decision":
            channel.publish(
                {
                    "kind": "file_decision",
                    "transaction_id": action["transaction_id"],
                    "accepted": action["accept"],
                    "changes": [],
                }
            )
        elif action["kind"] == "todo_go":
            channel.publish(
                {"kind": "todo", "todo_id": action["todo_id"], "state": "done"}
            )
        origin.done(True, "")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def run(out: Path) -> dict[str, Any]:
    """Drive the page in dark and light, each on a fresh session; returns {checks, errors}."""
    browser = chrome()
    if not browser or not shutil.which("node"):
        raise SystemExit("Needs Chrome and node.")
    out.mkdir(parents=True, exist_ok=True)
    seen: dict[str, Any] = {"checks": [], "errors": []}
    for scheme in ("dark", "light"):
        one = _run_one(out, browser, scheme)
        seen["checks"] += one["checks"]
        seen["errors"] += one["errors"]
    return seen


def _run_one(out: Path, browser: str, scheme: str) -> dict[str, Any]:
    import uvicorn

    work = Path(tempfile.mkdtemp(prefix="joinery-page-"))
    home, project = work / "home", work / "Harbour"
    home.mkdir(mode=0o700)
    project.mkdir()
    port = free_port()
    store = Store(home)
    store.set_config(enabled=True, port=port)
    made = store.new_code()
    token = "browser-token"
    request = store.redeem(
        made["code"], "Test Phone", "iOS", digest(token), digest("s")
    )
    store.answer(request["id"], True)
    server = RemoteServer(store, port=port, home=home)
    config = uvicorn.Config(
        server.app(), host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    web = uvicorn.Server(config)
    loop = asyncio.new_event_loop()
    channel = RemoteChannel(
        project, "claude", editor=False, to_editor=lambda e: None, home=home
    )

    async def serve() -> None:
        await channel.start()
        for event in session(project):
            channel.publish(event)
        answering = asyncio.create_task(host_loop(channel))
        try:
            await web.serve()
        finally:
            answering.cancel()
            await channel.close()

    thread = threading.Thread(
        target=lambda: loop.run_until_complete(serve()), daemon=True
    )
    thread.start()
    for _ in range(100):
        if web.started:
            break
        time.sleep(0.05)
    seen: dict[str, Any] = {"checks": [], "errors": []}
    try:
        if True:
            done = subprocess.run(
                [
                    "node",
                    str(ROOT / "tests/remote_page/drive.mjs"),
                    browser,
                    f"http://127.0.0.1:{port}",
                    token,
                    str(out),
                    scheme,
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            found = json.loads(done.stdout.strip().splitlines()[-1])
            for item in found.get("checks", []):
                seen["checks"].append({**item, "what": f"{scheme}: {item['what']}"})
            if found.get("error"):
                seen["errors"].append(f"{scheme}: {found['error']}")
    finally:
        web.should_exit = True
        thread.join(10)
        shutil.rmtree(work, ignore_errors=True)
    return seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "out/remote-page")
    seen = run(parser.parse_args().out)
    for item in seen["checks"]:
        print(("pass " if item["ok"] else "FAIL ") + item["what"])
    for error in seen["errors"]:
        print("ERROR " + error)
    return 0 if all(i["ok"] for i in seen["checks"]) and not seen["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
