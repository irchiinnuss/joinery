"""``joinery remote``: turn Joinery Remote on and off, pair and revoke devices (ADR 0093).

Pairing a device and every other setting stay on the machine: here, or in the editor's
Settings → Remote, which runs these same commands. A phone can never pair another one.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from joinery.platform_support import ProcessWatch, private_file
from joinery.remote import audit, qr, tailscale
from joinery.remote.actions import SCOPES
from joinery.remote.hosts import discover
from joinery.remote.paths import read_json, remote_home, write_private_json
from joinery.remote.store import Store

SERVER_FILE = "server.json"
LOG_FILE = "server.log"
#: What a pairing confirmation says the device will be able to do.
CAN_DO = {
    "watch": "follow your chats and changes",
    "chat": "send requests and stop them",
    "decide": "accept or discard changes",
}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    start = actions.add_parser("start", help="turn Remote on and start its server")
    start.add_argument("--port", type=int, default=0)
    start.add_argument("--json", action="store_true")
    stop = actions.add_parser("stop", help="turn Remote off; paired devices stay paired")
    stop.add_argument("--json", action="store_true")
    serve = actions.add_parser("serve", help="run the server in the foreground")
    serve.add_argument("--port", type=int, default=0)
    status = actions.add_parser("status", help="whether Remote is on, and where")
    status.add_argument("--json", action="store_true")
    pair = actions.add_parser("pair", help="pair a phone: a QR code and a code")
    pair.add_argument("--scopes", default=",".join(SCOPES))
    pair.add_argument("--json", action="store_true")
    pair.add_argument("--qr-png", type=Path, help="also write the QR code as a PNG")
    pair.add_argument(
        "--no-wait",
        action="store_true",
        help="print the code and return; confirm with `joinery remote confirm`",
    )
    requests = actions.add_parser("requests", help="pairing requests waiting here")
    requests.add_argument("--pairing", default="")
    requests.add_argument("--json", action="store_true")
    confirm = actions.add_parser("confirm", help="allow or deny a pairing request")
    confirm.add_argument("request_id")
    answer = confirm.add_mutually_exclusive_group(required=True)
    answer.add_argument("--allow", action="store_true")
    answer.add_argument("--deny", action="store_true")
    confirm.add_argument("--json", action="store_true")
    devices = actions.add_parser("devices", help="list paired devices")
    devices.add_argument("--json", action="store_true")
    revoke = actions.add_parser("revoke", help="revoke a device at once")
    revoke.add_argument("device_id")
    revoke.add_argument("--json", action="store_true")
    forget = actions.add_parser("forget-all", help="unpair every device")
    forget.add_argument("--yes", action="store_true")
    forget.add_argument("--json", action="store_true")
    log = actions.add_parser("log", help="what devices did")
    log.add_argument("--project", type=Path)
    log.add_argument("--limit", type=int, default=50)
    log.add_argument("--json", action="store_true")
    ts = actions.add_parser(
        "tailscale", help="the `tailscale serve` command that puts Remote on your tailnet"
    )
    ts.add_argument("--run", action="store_true", help="run it (asks first)")
    ts.add_argument("--yes", action="store_true", help="with --run: don't ask")
    ts.add_argument("--json", action="store_true")


def run(args: argparse.Namespace) -> int:
    store = Store()
    handlers = {
        "start": _start,
        "stop": _stop,
        "serve": _serve,
        "status": _status,
        "pair": _pair,
        "requests": _requests,
        "confirm": _confirm,
        "devices": _devices,
        "revoke": _revoke,
        "forget-all": _forget,
        "log": _log,
        "tailscale": _tailscale,
    }
    try:
        return handlers[args.action](store, args)
    except (OSError, ValueError) as exc:
        _out(args, {"error": str(exc)}, f"remote: {exc}")
        return 1


def _out(args: argparse.Namespace, data: Any, text: str) -> None:
    print(json.dumps(data) if getattr(args, "json", False) else text)


# --- Server -------------------------------------------------------------------------------


def server_info(home: Path | None = None) -> dict[str, Any]:
    """``{running, pid, port}`` of the server this user started."""
    data = read_json((home or remote_home()) / SERVER_FILE)
    if not isinstance(data, dict) or not isinstance(data.get("pid"), int):
        return {"running": False, "pid": 0, "port": 0}
    watch = ProcessWatch(int(data["pid"]))
    try:
        running = watch.alive()
    finally:
        watch.close()
    return {"running": running, "pid": data["pid"], "port": int(data.get("port", 0))}


def origin(store: Store, port: int, ts: dict[str, Any] | None = None) -> str:
    """Where a phone opens Remote: the tailnet name, else (on this machine only) loopback."""
    configured = str(store.config().get("origin", "") or "")
    if configured:
        return configured
    found = str((ts or {}).get("origin", ""))
    return found or f"http://127.0.0.1:{port}"


def _port(store: Store, wanted: int) -> int:
    port = wanted or int(store.config()["port"])
    if not 1024 <= port <= 65535:
        raise ValueError("Choose a port from 1024 to 65535.")
    return port


def _start(store: Store, args: argparse.Namespace) -> int:
    port = _port(store, args.port)
    store.set_config(enabled=True, port=port)
    info = server_info()
    if not info["running"]:
        log = store.home / LOG_FILE
        with log.open("ab") as stream:
            private_file(log)
            flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0
            subprocess.Popen(
                [sys.executable, "-m", "joinery.remote.cli", "serve", "--port", str(port)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stream,
                creationflags=flags,
                start_new_session=os.name != "nt",
            )
        for _ in range(50):
            if server_info()["running"]:
                break
            time.sleep(0.1)
    audit.record("remote_on", "ok")
    return _status(store, args)


def _stop(store: Store, args: argparse.Namespace) -> int:
    store.set_config(enabled=False)
    info = server_info()
    if info["running"]:
        # Only the server this user started, by the pid it wrote itself.
        os.kill(int(info["pid"]), signal.SIGTERM)
    audit.record("remote_off", "ok")
    _out(args, {"enabled": False, "running": False}, "Remote is off. Devices stay paired.")
    return 0


def _serve(store: Store, args: argparse.Namespace) -> int:
    from joinery.remote.server import run as serve

    port = _port(store, args.port)
    path = store.home / SERVER_FILE
    write_private_json(path, {"pid": os.getpid(), "port": port, "started": time.time()})
    try:
        return serve(store, port)
    finally:
        current = read_json(path)
        if isinstance(current, dict) and current.get("pid") == os.getpid():
            path.unlink(missing_ok=True)


def status(store: Store) -> dict[str, Any]:
    config = store.config()
    info = server_info(store.home)
    port = int(info["port"] or config["port"])
    ts = tailscale.status()
    devices = store.devices()
    return {
        "enabled": bool(config.get("enabled")),
        "running": bool(info["running"]),
        "port": port,
        "url": origin(store, port, ts),
        "tailscale": ts,
        "serve_command": " ".join(
            tailscale.serve_command(port, tailscale.executable() or "tailscale")
        ),
        "devices": [d for d in devices if d["state"] == "active"],
        "projects": [h.public() for h in discover(store.home)],
    }


def _status(store: Store, args: argparse.Namespace) -> int:
    data = status(store)
    if getattr(args, "json", False):
        print(json.dumps(data))
        return 0
    state = "on" if data["enabled"] else "off"
    running = "running" if data["running"] else "not running"
    print(f"Remote is {state} (server {running} on 127.0.0.1:{data['port']}).")
    print(f"Address: {data['url']}")
    if not data["tailscale"]["running"]:
        print(data["tailscale"]["message"])
    elif not store.config().get("origin"):
        print(f"Put it on your tailnet: {data['serve_command']}")
    print(f"Paired devices: {len(data['devices'])}")
    return 0


# --- Pairing ------------------------------------------------------------------------------


def _scopes(text: str) -> tuple[str, ...]:
    chosen = tuple(s.strip() for s in text.split(",") if s.strip())
    if not chosen or any(s not in SCOPES for s in chosen):
        raise ValueError("Scopes are watch, chat and decide.")
    return chosen


def confirm_text(request: dict[str, Any]) -> str:
    """The question the machine answers before a device is paired."""
    platform = f" ({request['platform']})" if request.get("platform") else ""
    can = ", ".join(CAN_DO[s] for s in SCOPES if s in request.get("scopes", []))
    return f'Pair "{request["name"]}"{platform}? It can {can} in your projects.'


def _pair(store: Store, args: argparse.Namespace) -> int:
    config = store.config()
    if not config.get("enabled"):
        raise ValueError("Turn Remote on first: joinery remote start")
    port = int(server_info()["port"] or config["port"])
    made = store.new_code(_scopes(args.scopes))
    url = qr.pair_url(origin(store, port, tailscale.status()), made["code"])
    if args.qr_png:
        qr.png(url, args.qr_png)
    shown = {**made, "url": url, "qr": str(args.qr_png or "")}
    audit.record("pair_code", "ok", pairing_id=made["pairing_id"])
    if args.json and args.no_wait:
        print(json.dumps(shown))
        return 0
    if not args.json:
        print(qr.terminal(url))
        print(f"Code: {made['code']}  (valid for 5 minutes)")
        print(f"Or open: {url}")
    if args.no_wait:
        return 0
    return _wait_and_ask(store, made, args)


def _wait_and_ask(store: Store, made: dict[str, Any], args: argparse.Namespace) -> int:
    while time.time() < float(made["expires"]):
        waiting = store.requests(made["pairing_id"])
        if waiting:
            request = waiting[0]
            try:
                said = input(confirm_text(request) + " [y/N] ").strip().lower()
            except EOFError:
                said = ""
            allow = said in {"y", "yes"}
            answered = store.answer(request["id"], allow)
            audit.record(
                "pair", "ok" if allow else "refused", name=request["name"], step="answer"
            )
            _out(
                args,
                answered,
                f"Paired {request['name']}." if allow else "Not paired.",
            )
            return 0 if allow else 1
        time.sleep(1)
    _out(args, {"error": "expired"}, "The code expired. Make a new one.")
    return 1


def _requests(store: Store, args: argparse.Namespace) -> int:
    waiting = [{**r, "question": confirm_text(r)} for r in store.requests(args.pairing)]
    text = "\n".join(f"{r['id']}  {r['question']}" for r in waiting) or "Nothing waits."
    _out(args, {"requests": waiting}, text)
    return 0


def _confirm(store: Store, args: argparse.Namespace) -> int:
    answered = store.answer(args.request_id, bool(args.allow))
    audit.record(
        "pair",
        "ok" if args.allow else "refused",
        name=answered.get("name", ""),
        step="answer",
        device_id=answered.get("device_id", ""),
    )
    _out(args, answered, "Paired." if args.allow else "Not paired.")
    return 0


# --- Devices ------------------------------------------------------------------------------


def _devices(store: Store, args: argparse.Namespace) -> int:
    devices = store.devices()
    lines = [
        f"{d['id']}  {d['name']}  {d['state']}  {','.join(d.get('scopes', []))}"
        + (f"  last: {d['last_action']}" if d.get("last_action") else "")
        for d in devices
    ]
    _out(args, {"devices": devices}, "\n".join(lines) or "No paired devices.")
    return 0


def _revoke(store: Store, args: argparse.Namespace) -> int:
    device = store.revoke(args.device_id)
    audit.record("revoke", "ok", device=device)
    _out(args, device, f"Revoked {device['name']}.")
    return 0


def _forget(store: Store, args: argparse.Namespace) -> int:
    if not args.yes:
        raise ValueError("This unpairs every device. Run again with --yes.")
    count = store.forget_all()
    audit.record("forget_all", "ok", count=count)
    _out(args, {"forgotten": count}, f"Unpaired {count} device(s).")
    return 0


def _log(store: Store, args: argparse.Namespace) -> int:
    project = args.project.resolve() if args.project else None
    found = audit.entries(args.limit, project=project, home=store.home)
    lines = [
        time.strftime("%Y-%m-%d %H:%M", time.localtime(float(e.get("ts", 0))))
        + f"  {e.get('device_name', '-')}  {e.get('action')}  {e.get('result')}"
        + (f"  {e['reason']}" if e.get("reason") else "")
        for e in found
    ]
    _out(args, {"entries": found}, "\n".join(lines) or "Nothing yet.")
    return 0


def _tailscale(store: Store, args: argparse.Namespace) -> int:
    program = tailscale.executable()
    ts = tailscale.status(program)
    port = int(server_info()["port"] or store.config()["port"])
    command = tailscale.serve_command(port, program or "tailscale")
    data: dict[str, Any] = {**ts, "command": command}
    if not args.run:
        text = " ".join(command) if ts["running"] else ts["message"]
        _out(args, data, text)
        return 0 if ts["installed"] else 1
    if not ts["running"]:
        raise ValueError(ts["message"])
    if not args.yes:
        answer = input(f"Run `{' '.join(command)}`? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            _out(args, {**data, "ran": False}, "Nothing changed.")
            return 1
    done = tailscale.serve(port, program)
    if done.returncode != 0:
        raise ValueError((done.stderr or done.stdout).strip() or "tailscale serve failed.")
    store.set_config(origin=ts["origin"])
    audit.record("tailscale_serve", "ok", origin=ts["origin"])
    _out(args, {**data, "ran": True}, f"Remote is on your tailnet: {ts['origin']}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="joinery remote")
    add_arguments(parser)
    raise SystemExit(run(parser.parse_args()))
