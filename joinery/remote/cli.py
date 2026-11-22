"""``joinery remote``: turn Joinery Remote on and off, pair and revoke devices (ADR 0093).

Pairing a device and every other setting stay on the machine: here, or in the editor's
Settings → Remote, which runs these same commands. A phone can never pair another one.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from joinery import __version__
from joinery.remote import audit, login_item, qr, tailscale
from joinery.remote.actions import SCOPES
from joinery.remote.hosts import discover
from joinery.remote.paths import read_json, write_private_json
from joinery.remote.service import (
    LOG_FILE,
    SERVER_FILE,
    ensure_server,
    server_info,
    stop_server,
)
from joinery.remote.store import Store

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
    stop = actions.add_parser(
        "stop", help="turn Remote off; paired devices stay paired"
    )
    stop.add_argument("--json", action="store_true")
    serve = actions.add_parser("serve", help="run the server in the foreground")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument(
        "--helper",
        action="store_true",
        help="also start a project's chat for a phone when no editor has it open",
    )
    for name, says in (
        ("install-helper", "keep Remote available when Joinery is closed (login item)"),
        ("uninstall-helper", "remove the login item"),
    ):
        helper = actions.add_parser(name, help=says)
        helper.add_argument("--json", action="store_true")
    awake = actions.add_parser(
        "keep-awake", help="keep this machine awake on power while Remote is on"
    )
    awake.add_argument("state", choices=["on", "off"])
    awake.add_argument("--json", action="store_true")
    status = actions.add_parser("status", help="whether Remote is on, and where")
    status.add_argument("--json", action="store_true")
    pair = actions.add_parser("pair", help="pair a phone: a QR code and a code")
    pair.add_argument("--scopes", default=",".join(SCOPES))
    pair.add_argument("--json", action="store_true")
    pair.add_argument("--qr-png", type=Path, help="also write the QR code as a PNG")
    pair.add_argument(
        "--qr",
        action="store_true",
        help="also write the QR code as a PNG in Remote's private folder (its path "
        "is in the answer; delete it once shown)",
    )
    pair.add_argument(
        "--local",
        action="store_true",
        help="pair a browser on this machine (127.0.0.1) when Remote isn't on the "
        "tailnet yet",
    )
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
    scopes = actions.add_parser("scopes", help="change what a paired device may do")
    scopes.add_argument("device_id")
    scopes.add_argument("scopes", help="comma-separated: watch, chat, decide")
    scopes.add_argument("--json", action="store_true")
    projects = actions.add_parser(
        "projects",
        help="the projects a phone may reach: list them, or limit a device to some",
    )
    projects.add_argument("device_id", nargs="?", default="")
    chosen = projects.add_mutually_exclusive_group()
    chosen.add_argument(
        "--allow",
        nargs="+",
        default=None,
        metavar="PROJECT",
        help="project ids (from `joinery remote projects`) or folders",
    )
    chosen.add_argument("--all", action="store_true", help="every project again")
    projects.add_argument("--json", action="store_true")
    expiry = actions.add_parser(
        "expiry", help="how long a paired device lasts (unused, and in all)"
    )
    expiry.add_argument("--unused-days", type=float, default=None)
    expiry.add_argument("--absolute-days", type=float, default=None)
    expiry.add_argument("--json", action="store_true")
    forget = actions.add_parser("forget-all", help="unpair every device")
    forget.add_argument("--yes", action="store_true")
    forget.add_argument("--json", action="store_true")
    log = actions.add_parser("log", help="what devices did")
    log.add_argument("--project", type=Path)
    log.add_argument("--limit", type=int, default=50)
    log.add_argument("--json", action="store_true")
    owner = actions.add_parser(
        "owner",
        help="only your own tailnet login's devices may reach Remote through the "
        "tailnet",
    )
    owner.add_argument("login", nargs="?", default="", help="e.g. ana@example.com")
    owner.add_argument("--clear", action="store_true", help="any tailnet device again")
    owner.add_argument("--json", action="store_true")
    ts = actions.add_parser(
        "tailscale",
        help="the `tailscale serve` command that puts Remote on your tailnet",
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
        "scopes": _set_scopes,
        "projects": _projects,
        "expiry": _expiry,
        "log": _log,
        "tailscale": _tailscale,
        "owner": _owner,
        "install-helper": _install_helper,
        "uninstall-helper": _uninstall_helper,
        "keep-awake": _keep_awake,
    }
    try:
        return handlers[args.action](store, args)
    except (OSError, ValueError) as exc:
        _out(args, {"error": str(exc)}, f"remote: {exc}")
        return 1


def _out(args: argparse.Namespace, data: Any, text: str) -> None:
    print(json.dumps(data) if getattr(args, "json", False) else text)


# --- Server -------------------------------------------------------------------------------

NOT_SERVED = (
    "Remote isn't on your tailnet yet, so a phone can't open it. Put it on first: "
    "joinery remote tailscale --run"
)
FUNNEL = (
    "Tailscale Funnel puts Remote on the public internet, and Remote refuses that. "
    "Turn Funnel off for it in Tailscale, then serve it on your tailnet only."
)


def tailnet(store: Store, port: int) -> dict[str, Any]:
    """Whether ``tailscale serve`` carries Remote now, kept in step with the stored origin.

    The server answers for the stored origin's name only (ADR 0093), so the address
    ``tailscale serve`` is found to serve is stored whenever it is seen, however it was
    set up, and a stored one that Tailscale says it no longer serves is cleared.
    Returns ``{served, origin, funnel}``; ``origin`` is "" unless served."""
    found = tailscale.served(port)
    stored = str(store.config().get("origin", "") or "")
    if found["known"] and found["origin"] != stored:
        store.set_config(origin=found["origin"])
        audit.record("tailnet_origin", "ok", origin=found["origin"] or "-")
        stored = found["origin"]
    served = bool(found["known"] and found["origin"])
    return {
        "served": served,
        "origin": stored if served else "",
        "funnel": bool(found["funnel"]),
    }


def origin(port: int, served: dict[str, Any]) -> str:
    """Where a phone opens Remote: the served tailnet address, else (on this machine
    only) loopback."""
    return str(served.get("origin", "")) or f"http://127.0.0.1:{port}"


def _port(store: Store, wanted: int) -> int:
    port = wanted or int(store.config()["port"])
    if not 1024 <= port <= 65535:
        raise ValueError("Choose a port from 1024 to 65535.")
    return port


def _start(store: Store, args: argparse.Namespace) -> int:
    port = _port(store, args.port)
    store.set_config(enabled=True, port=port)
    ensure_server(store, port)
    audit.record("remote_on", "ok")
    return _status(store, args)


def _stop(store: Store, args: argparse.Namespace) -> int:
    store.set_config(enabled=False)
    stop_server(store.home)
    audit.record("remote_off", "ok")
    _out(
        args,
        {"enabled": False, "running": False},
        "Remote is off. Devices stay paired.",
    )
    return 0


def _serve(store: Store, args: argparse.Namespace) -> int:
    from joinery.remote.server import run as serve

    if not store.config().get("enabled"):
        # A login item (the always-on helper) starts this at every login, even after
        # `joinery remote stop`: while Remote is off nothing listens. Exit 0, so the
        # login item doesn't start it again.
        _out(args, {"enabled": False}, "Remote is off: nothing to serve.")
        return 0
    port = _port(store, args.port)
    path = store.home / SERVER_FILE
    write_private_json(
        path,
        {
            "pid": os.getpid(),
            "port": port,
            "started": time.time(),
            "version": __version__,
        },
    )
    try:
        return serve(store, port, helper=bool(getattr(args, "helper", False)))
    finally:
        current = read_json(path)
        if isinstance(current, dict) and current.get("pid") == os.getpid():
            path.unlink(missing_ok=True)


def status(store: Store) -> dict[str, Any]:
    config = store.config()
    if config.get("enabled"):
        # Settings → Remote reads this when shown: after a restart or an update, the
        # server is started again at this version.
        ensure_server(store, wait=True)
    info = server_info(store.home)
    port = int(info["port"] or config["port"])
    ts = tailscale.status()
    on_tailnet = (
        tailnet(store, port)
        if ts["running"]
        else {"served": False, "origin": "", "funnel": False}
    )
    devices = store.devices()
    return {
        "enabled": bool(config.get("enabled")),
        "running": bool(info["running"]),
        "port": port,
        "url": origin(port, on_tailnet),
        "served": on_tailnet["served"],
        "funnel": on_tailnet["funnel"],
        "tailscale": ts,
        "serve_command": " ".join(
            tailscale.serve_command(port, tailscale.executable() or "tailscale")
        ),
        "devices": [d for d in devices if d["state"] == "active"],
        "helper": login_item.status()["installed"],
        "keep_awake": bool(config.get("keep_awake")),
        "owner": str(config.get("owner", "") or ""),
        "projects": [h.public() for h in discover(store.home)],
        # For Settings → Remote: the projects a device may be limited to, and how
        # long devices last (`joinery remote projects`, `joinery remote expiry`).
        "known_projects": _known_projects(store),
        "expiry": {
            "unused_days": float(config["unused_days"]),
            "absolute_days": float(config["absolute_days"]),
        },
    }


def _known_projects(store: Store) -> list[dict[str, str]]:
    from joinery.remote.paths import known

    return [
        {"id": identifier, "name": str(entry.get("name", ""))}
        for identifier, entry in known(store.home).items()
    ]


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
    elif data["funnel"]:
        print(FUNNEL)
    elif not data["served"]:
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
    """The question the machine answers before a device is paired. It says who asked
    as well as the name the phone gave, which anyone could type: the tailnet login
    ``tailscale serve`` added, or that there was none."""
    platform = f" ({request['platform']})" if request.get("platform") else ""
    can = ", ".join(CAN_DO[s] for s in SCOPES if s in request.get("scopes", []))
    peer = str(request.get("peer", "") or "")
    who = (
        f" Signed in to Tailscale as {peer}."
        if "@" in peer
        else " It gave no Tailscale login (this machine, or a tagged device)."
    )
    return f'Pair "{request["name"]}"{platform}? It can {can} in your projects.{who}'


def _pair(store: Store, args: argparse.Namespace) -> int:
    config = store.config()
    if not config.get("enabled"):
        raise ValueError("Turn Remote on first: joinery remote start")
    port = int(server_info(store.home)["port"] or config["port"])
    on_tailnet = tailnet(store, port)
    if not on_tailnet["served"] and not args.local:
        raise ValueError(FUNNEL if on_tailnet["funnel"] else NOT_SERVED)
    made = store.new_code(_scopes(args.scopes))
    url = qr.pair_url(origin(port, on_tailnet), made["code"])
    picture: Path | None = None
    if args.qr_png:
        picture = qr.png(url, args.qr_png)
    elif args.qr:
        picture = qr.private_png(url, store.home, made["pairing_id"])
    shown = {**made, "url": url, "qr": str(picture or "")}
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


STOPPED = (
    "Wrong codes were tried too often, so this code stopped working. Pair again "
    "for a new one."
)


def _wait_and_ask(store: Store, made: dict[str, Any], args: argparse.Namespace) -> int:
    while time.time() < float(made["expires"]):
        if store.pairing_state(made["pairing_id"]) == "stopped":
            _out(args, {"error": "stopped"}, STOPPED)
            return 1
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
                "pair",
                "ok" if allow else "refused",
                name=request["name"],
                step="answer",
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
    data: dict[str, Any] = {"requests": waiting}
    if args.pairing:
        # Settings polls this: a code stopped by wrong guesses is said, not hidden.
        data["pairing"] = store.pairing_state(args.pairing)
        if data["pairing"] == "stopped":
            data["text"] = STOPPED
            text = STOPPED
    _out(args, data, text)
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


def _set_scopes(store: Store, args: argparse.Namespace) -> int:
    device = store.set_scopes(args.device_id, list(_scopes(args.scopes)))
    audit.record("scopes", "ok", device=device, scopes=",".join(device["scopes"]))
    _out(args, device, f"{device['name']} may now: {', '.join(device['scopes'])}.")
    return 0


def _project_ids(store: Store, named: list[str]) -> list[str]:
    """Project ids from ids or folders, each one a project used here before."""
    from joinery.remote.paths import known, project_id

    projects = known(store.home)
    ids = []
    for name in named:
        folder = Path(name).expanduser()
        identifier = project_id(folder) if folder.is_dir() else name
        if identifier not in projects:
            raise ValueError(
                f"{name} isn't a project used here. See `joinery remote projects`."
            )
        ids.append(identifier)
    return ids


def _projects(store: Store, args: argparse.Namespace) -> int:
    """No device: the projects a phone could reach. A device: what it may reach, or
    (``--allow`` / ``--all``) change that; an open stream on a project it loses ends
    at once."""
    listed = _known_projects(store)
    if not args.device_id:
        if args.allow is not None or args.all:
            raise ValueError("Name the device: joinery remote projects <device> ...")
        lines = [f"{p['id']}  {p['name']}" for p in listed]
        _out(args, {"projects": listed}, "\n".join(lines) or "No projects yet.")
        return 0
    if args.all:
        device = store.set_projects(args.device_id, [])
        audit.record("projects", "ok", device=device, projects="all")
    elif args.allow is not None:
        device = store.set_projects(args.device_id, _project_ids(store, args.allow))
        audit.record(
            "projects", "ok", device=device, projects=",".join(device["projects"])
        )
    else:
        found = [d for d in store.devices() if d["id"] == args.device_id]
        if not found:
            raise ValueError(f"No paired device {args.device_id}.")
        device = found[0]
    names = {p["id"]: p["name"] for p in listed}
    allowed = device.get("projects") or []
    text = (
        f"{device['name']} may reach: "
        + ", ".join(names.get(p, p) for p in allowed)
        + "."
        if allowed
        else f"{device['name']} may reach every project."
    )
    _out(args, device, text)
    return 0


def _expiry(store: Store, args: argparse.Namespace) -> int:
    if args.unused_days is not None or args.absolute_days is not None:
        config = store.set_expiry(args.unused_days, args.absolute_days)
        audit.record(
            "expiry",
            "ok",
            unused_days=config["unused_days"],
            absolute_days=config["absolute_days"],
        )
    config = store.config()
    shown = {
        "unused_days": float(config["unused_days"]),
        "absolute_days": float(config["absolute_days"]),
    }
    _out(
        args,
        shown,
        f"A device is unpaired after {shown['unused_days']:g} days unused, and "
        f"{shown['absolute_days']:g} days after pairing in all.",
    )
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
    port = int(server_info(store.home)["port"] or store.config()["port"])
    command = tailscale.serve_command(port, program or "tailscale")
    data: dict[str, Any] = {**ts, "command": command}
    if not args.run:
        if ts["running"]:
            data |= tailnet(store, port)
        text = (
            ts["message"]
            if not ts["running"]
            else f"Remote is on your tailnet: {data['origin']}"
            if data["served"]
            else " ".join(command)
        )
        _out(args, data, text)
        return 0 if ts["installed"] else 1
    if not ts["running"]:
        raise ValueError(ts["message"])
    if not args.yes:
        answer = input(f"Run `{' '.join(command)}`? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            _out(args, {**data, "ran": False}, "Nothing changed.")
            return 1
    ok, said = tailscale.serve(port, program)
    if not ok:
        raise ValueError(said)
    # What Tailscale now serves, not what it was asked to: that name is stored.
    on_tailnet = tailnet(store, port)
    if not on_tailnet["served"]:
        raise ValueError(
            FUNNEL
            if on_tailnet["funnel"]
            else "tailscale serve ran, but Tailscale doesn't list Remote as served. "
            "Check `tailscale serve status`."
        )
    audit.record("tailscale_serve", "ok", origin=on_tailnet["origin"])
    _out(
        args,
        {**data, "ran": True, "origin": on_tailnet["origin"]},
        f"Remote is on your tailnet: {on_tailnet['origin']}",
    )
    return 0


def _owner(store: Store, args: argparse.Namespace) -> int:
    """Set, clear or show the tailnet login whose devices alone may reach Remote
    through ``tailscale serve`` (its ``Tailscale-User-Login``). Requests to 127.0.0.1
    on this machine aren't affected."""
    if args.clear:
        store.set_config(owner="")
        audit.record("owner", "ok", owner="-")
    elif args.login:
        login = args.login.strip().lower()
        if "@" not in login or len(login) > 100 or any(c.isspace() for c in login):
            raise ValueError("Give a tailnet login, like ana@example.com.")
        store.set_config(owner=login)
        audit.record("owner", "ok", owner=login)
    owner = str(store.config().get("owner", "") or "")
    _out(
        args,
        {"owner": owner},
        f"Only devices signed in to Tailscale as {owner} can reach Remote."
        if owner
        else "Any device on your tailnet can reach Remote's page (pairing still asks).",
    )
    return 0


def _install_helper(store: Store, args: argparse.Namespace) -> int:
    done = login_item.install(store.home / LOG_FILE)
    audit.record("helper_on", "ok")
    _out(
        args,
        done,
        "Remote stays available when Joinery is closed. Your phone can open any "
        "project you used here; it runs without the editor and keeps every change "
        "for review.",
    )
    return 0


def _uninstall_helper(store: Store, args: argparse.Namespace) -> int:
    done = login_item.uninstall()
    audit.record("helper_off", "ok")
    _out(args, done, "Remote now needs Joinery open.")
    return 0


def _keep_awake(store: Store, args: argparse.Namespace) -> int:
    on = args.state == "on"
    store.set_config(keep_awake=on)
    audit.record("keep_awake", "ok", state=args.state)
    _out(
        args,
        {"keep_awake": on},
        "This machine stays awake on power while Remote is on."
        if on
        else "This machine sleeps as usual.",
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="joinery remote")
    add_arguments(parser)
    raise SystemExit(run(parser.parse_args()))
