"""Paired devices, pairing codes and the server's settings, in the per-user directory.

Only SHA-256 hashes of device tokens and pairing codes are stored. A device token is
256 random bits, handed to the phone once; a pairing code is 10 characters (about 49
bits), single use, valid for 5 minutes, and useless without the confirmation on the
machine. Five wrong codes lock pairing for 10 minutes. Every process (the server, the
CLI, the editor's Settings page through the CLI) reads and writes the same file under a
lock, so a revoke takes effect on the server's next request.
"""

from __future__ import annotations

import contextlib
import hashlib
import secrets
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from joinery.platform_support import lock_file, private_file, unlock_file
from joinery.remote.actions import SCOPES
from joinery.remote.paths import home_path, read_json, remote_home, write_private_json

VERSION = 1
#: Crockford-like: no I, L, O, U, 0 or 1, so a code read aloud is typed right.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
CODE_LENGTH = 10
PAIR_SECONDS = 5 * 60
MAX_FAILURES = 5
LOCK_SECONDS = 10 * 60
UNUSED_DAYS = 30
ABSOLUTE_DAYS = 90
DEFAULT_PORT = 47615
DEFAULT_SCOPES: tuple[str, ...] = SCOPES
#: How often a device's "last seen" is written at most (seconds).
SEEN_EVERY = 60
NAME_LIMIT = 60
DAY = 86_400


class PairRefused(ValueError):
    """A pairing attempt that fails: wrong or used code, expired, or locked."""


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def normal_code(code: str) -> str:
    """A typed code as stored: letters and digits only, upper case."""
    return "".join(ch for ch in code.upper() if ch.isalnum())


def shown_code(code: str) -> str:
    """``ABCDEFGHJK`` as ``ABCDE-FGHJK``."""
    return code[:5] + "-" + code[5:]


def device_name(name: str) -> str:
    """A device name as shown: one line, printable, at most 60 characters."""
    clean = " ".join("".join(ch for ch in name if ch.isprintable()).split())
    return clean[:NAME_LIMIT] or "Phone"


def public(device: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """A device as the CLI and the Settings page show it: never its token hash."""
    now = time.time() if now is None else now
    hidden = {"token_sha256", "push"}
    shown = {k: v for k, v in device.items() if k not in hidden}
    # Whether it hears pushes; never its endpoint or keys.
    shown["push"] = len(device.get("push", []))
    shown["state"] = state_of(device, now)
    return shown


def state_of(device: dict[str, Any], now: float) -> str:
    if device.get("revoked_at"):
        return "revoked"
    if now >= float(device.get("expires_at", 0)):
        return "expired"
    unused = float(device.get("unused_days", UNUSED_DAYS)) * DAY
    if now >= float(device.get("last_seen", device.get("created", 0))) + unused:
        return "expired"
    return "active"


def enabled(home: Path | None = None) -> bool:
    """Whether Remote is on (Settings → Remote, ``joinery remote start``): only then do
    chat hosts open their channel. Never creates anything."""
    data = read_json((home or home_path()) / "devices.json")
    config = data.get("config") if isinstance(data, dict) else None
    return bool(isinstance(config, dict) and config.get("enabled"))


class Store:
    """The per-user remote state file (``devices.json``)."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = home or remote_home()
        self.path = self.home / "devices.json"
        self.lock_path = self.home / "devices.lock"

    # --- File ---------------------------------------------------------------------------

    def read(self) -> dict[str, Any]:
        data = read_json(self.path)
        if not isinstance(data, dict) or data.get("version") != VERSION:
            data = {}
        data.setdefault("version", VERSION)
        data.setdefault("devices", [])
        data.setdefault("pairings", [])
        data.setdefault("requests", [])
        data.setdefault("lockout", {"failures": [], "until": 0})
        data.setdefault("config", {})
        return data

    @contextlib.contextmanager
    def edit(self) -> Iterator[dict[str, Any]]:
        """The state under the lock; written back when the block ends without error."""
        created = not self.lock_path.exists()
        with self.lock_path.open("a+b") as stream:
            if created:
                private_file(self.lock_path)
            lock_file(stream)
            try:
                data = self.read()
                yield data
                write_private_json(self.path, data)
            finally:
                unlock_file(stream)

    # --- Settings -----------------------------------------------------------------------

    def config(self) -> dict[str, Any]:
        config = dict(self.read()["config"])
        config.setdefault("port", DEFAULT_PORT)
        config.setdefault("origin", "")
        config.setdefault("unused_days", UNUSED_DAYS)
        config.setdefault("absolute_days", ABSOLUTE_DAYS)
        return config

    def set_config(self, **values: Any) -> dict[str, Any]:
        with self.edit() as data:
            data["config"].update(values)
        return self.config()

    # --- Pairing ------------------------------------------------------------------------

    def new_code(
        self, scopes: tuple[str, ...] = DEFAULT_SCOPES, now: float | None = None
    ) -> dict[str, Any]:
        """A fresh pairing code: ``{pairing_id, code, expires}``. Shown once, kept hashed."""
        now = time.time() if now is None else now
        unknown = [s for s in scopes if s not in SCOPES]
        if unknown or not scopes:
            raise ValueError("Scopes are watch, chat and decide.")
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        pairing = {
            "id": "p_" + secrets.token_hex(6),
            "code_sha256": digest(code),
            "created": now,
            "expires": now + PAIR_SECONDS,
            "scopes": list(scopes),
            "used": False,
        }
        with self.edit() as data:
            data["pairings"] = [
                p for p in data["pairings"] if float(p.get("expires", 0)) > now
            ]
            data["pairings"].append(pairing)
        return {
            "pairing_id": pairing["id"],
            "code": shown_code(code),
            "expires": pairing["expires"],
            "scopes": list(scopes),
        }

    def locked_until(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        until = float(self.read()["lockout"].get("until", 0))
        return until if until > now else 0.0

    def redeem(
        self,
        code: str,
        name: str,
        platform: str,
        token_sha256: str,
        secret_sha256: str,
        peer: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """A phone posted ``code``: a request that waits for the machine's confirmation.

        Raises :class:`PairRefused` for a wrong, used or expired code, or while pairing
        is locked; a wrong code counts towards the lock."""
        now = time.time() if now is None else now
        wanted = digest(normal_code(code))
        with self.edit() as data:
            lockout = data["lockout"]
            if float(lockout.get("until", 0)) > now:
                raise PairRefused(
                    "Pairing is locked for a while after too many wrong codes."
                )
            match = next(
                (
                    p
                    for p in data["pairings"]
                    if secrets.compare_digest(str(p.get("code_sha256", "")), wanted)
                ),
                None,
            )
            if match is None or match.get("used") or float(match["expires"]) <= now:
                failures = [
                    float(t)
                    for t in lockout.get("failures", [])
                    if float(t) > now - LOCK_SECONDS
                ]
                failures.append(now)
                lockout["failures"] = failures
                if len(failures) >= MAX_FAILURES:
                    lockout["until"] = now + LOCK_SECONDS
                    lockout["failures"] = []
                reason = (
                    "That code was already used."
                    if match is not None and match.get("used")
                    else "That code has expired. Make a new one at the machine."
                    if match is not None
                    else "That code isn't right."
                )
                # Raising would discard the counted failure: keep it, then refuse.
                data["_refused"] = reason
            else:
                match["used"] = True
                request = {
                    "id": "r_" + secrets.token_hex(8),
                    "pairing_id": match["id"],
                    "name": device_name(name),
                    "platform": device_name(platform) if platform else "",
                    "peer": peer[:100],
                    "scopes": list(match.get("scopes", DEFAULT_SCOPES)),
                    "token_sha256": token_sha256,
                    "secret_sha256": secret_sha256,
                    "state": "waiting",
                    "created": now,
                    "expires": now + PAIR_SECONDS,
                }
                data["requests"] = [
                    r
                    for r in data["requests"]
                    if float(r.get("expires", 0)) > now - PAIR_SECONDS
                ]
                data["requests"].append(request)
                data["_request"] = request
            refused = data.pop("_refused", "")
            made = data.pop("_request", None)
        if refused:
            raise PairRefused(refused)
        assert made is not None
        return {k: v for k, v in made.items() if not k.endswith("_sha256")}

    def requests(
        self, pairing_id: str = "", now: float | None = None
    ) -> list[dict[str, Any]]:
        """Pairing requests waiting for the machine's Allow or Deny."""
        now = time.time() if now is None else now
        return [
            {k: v for k, v in r.items() if not k.endswith("_sha256")}
            for r in self.read()["requests"]
            if r.get("state") == "waiting"
            and float(r.get("expires", 0)) > now
            and (not pairing_id or r.get("pairing_id") == pairing_id)
        ]

    def answer(
        self, request_id: str, allow: bool, now: float | None = None
    ) -> dict[str, Any]:
        """The machine's Allow or Deny. Allow makes the device; returns the request."""
        now = time.time() if now is None else now
        with self.edit() as data:
            request = next(
                (r for r in data["requests"] if r.get("id") == request_id), None
            )
            if request is None or request.get("state") != "waiting":
                raise ValueError("That pairing request is no longer waiting.")
            if float(request.get("expires", 0)) <= now:
                request["state"] = "expired"
                data["_expired"] = True
            elif allow:
                config = data["config"]
                device = {
                    "id": "d_" + secrets.token_hex(6),
                    "name": request["name"],
                    "platform": request.get("platform", ""),
                    "scopes": list(request.get("scopes", DEFAULT_SCOPES)),
                    "projects": [],
                    "token_sha256": request["token_sha256"],
                    "created": now,
                    "last_seen": now,
                    "last_action": "",
                    "unused_days": float(config.get("unused_days", UNUSED_DAYS)),
                    "expires_at": now
                    + float(config.get("absolute_days", ABSOLUTE_DAYS)) * DAY,
                    "revoked_at": 0,
                }
                data["devices"].append(device)
                request["state"] = "allowed"
                request["device_id"] = device["id"]
            else:
                request["state"] = "denied"
            expired = data.pop("_expired", False)
            answered = {k: v for k, v in request.items() if not k.endswith("_sha256")}
        if expired:
            raise ValueError("That pairing request expired. Make a new code.")
        return answered

    def request_state(self, request_id: str, secret: str) -> dict[str, Any]:
        """What the phone that made ``request_id`` (and holds ``secret``) may know."""
        for request in self.read()["requests"]:
            if request.get("id") != request_id:
                continue
            if not secrets.compare_digest(
                str(request.get("secret_sha256", "")), digest(secret)
            ):
                break
            state = str(request.get("state", "waiting"))
            if state == "waiting" and float(request.get("expires", 0)) <= time.time():
                state = "expired"
            return {"state": state, "device_id": str(request.get("device_id", ""))}
        return {"state": "unknown", "device_id": ""}

    # --- Devices ------------------------------------------------------------------------

    def devices(self, now: float | None = None) -> list[dict[str, Any]]:
        return [public(d, now) for d in self.read()["devices"]]

    def authenticate(
        self, token: str, now: float | None = None
    ) -> dict[str, Any] | None:
        """The active device whose token this is, else None. Marks it seen."""
        now = time.time() if now is None else now
        if not token:
            return None
        wanted = digest(token)
        for device in self.read()["devices"]:
            if secrets.compare_digest(str(device.get("token_sha256", "")), wanted):
                if state_of(device, now) != "active":
                    return None
                if now - float(device.get("last_seen", 0)) >= SEEN_EVERY:
                    self.touch(str(device["id"]), now=now)
                return public(device, now)
        return None

    def active(self, device_id: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return any(
            d.get("id") == device_id and state_of(d, now) == "active"
            for d in self.read()["devices"]
        )

    def touch(self, device_id: str, action: str = "", now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.edit() as data:
            for device in data["devices"]:
                if device.get("id") == device_id:
                    device["last_seen"] = now
                    if action:
                        device["last_action"] = action
                        device["last_action_at"] = now

    def revoke(self, device_id: str, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self.edit() as data:
            device = next(
                (d for d in data["devices"] if d.get("id") == device_id), None
            )
            if device is None:
                raise ValueError(f"No paired device {device_id}.")
            device["revoked_at"] = device.get("revoked_at") or now
            shown = public(device, now)
        return shown

    def forget_all(self) -> int:
        with self.edit() as data:
            count = len(data["devices"])
            data["devices"] = []
            data["requests"] = []
            data["pairings"] = []
        return count

    def set_scopes(self, device_id: str, scopes: list[str]) -> dict[str, Any]:
        if not scopes or any(s not in SCOPES for s in scopes):
            raise ValueError("Scopes are watch, chat and decide.")
        with self.edit() as data:
            device = next(
                (d for d in data["devices"] if d.get("id") == device_id), None
            )
            if device is None:
                raise ValueError(f"No paired device {device_id}.")
            device["scopes"] = [s for s in SCOPES if s in scopes]
            shown = public(device)
        return shown

    # --- Push subscriptions -------------------------------------------------------------

    def add_push(
        self, device_id: str, subscription: dict[str, str], kinds: list[str]
    ) -> None:
        """Keep a phone's Web Push subscription (joinery.remote.push), newest three."""
        with self.edit() as data:
            device = next(
                (d for d in data["devices"] if d.get("id") == device_id), None
            )
            if device is None:
                raise ValueError(f"No paired device {device_id}.")
            kept = [
                s
                for s in device.get("push", [])
                if s.get("endpoint") != subscription["endpoint"]
            ]
            kept.append({**subscription, "kinds": kinds, "created": time.time()})
            device["push"] = kept[-3:]

    def remove_push(self, endpoint: str, device_id: str = "") -> None:
        with self.edit() as data:
            for device in data["devices"]:
                if device_id and device.get("id") != device_id:
                    continue
                device["push"] = [
                    s for s in device.get("push", []) if s.get("endpoint") != endpoint
                ]

    def push_targets(
        self, now: float | None = None
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """(device, subscription) for every active device that may watch."""
        now = time.time() if now is None else now
        return [
            (public(device, now), subscription)
            for device in self.read()["devices"]
            if state_of(device, now) == "active" and "watch" in device.get("scopes", [])
            for subscription in device.get("push", [])
        ]
