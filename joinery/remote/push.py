"""Web Push for Joinery Remote (ADR 0093): a phone hears when Journeyman needs it.

The one thing Remote sends off the machine. The phone's browser hands over a push
subscription (an endpoint at its push service, Apple, Google, Mozilla or Microsoft, and
its keys). A notice is encrypted for that phone alone (RFC 8291, ``aes128gcm``), so the
push service sees only ciphertext, its size and its timing. It is signed with this
machine's VAPID key (RFC 8292). The payload says which project and what kind, never what
the change or the chat says. No push is sent while the user is at the open editor.

Endpoints must be HTTPS on a known push service: a device can't make the machine post
anywhere else.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from joinery.platform_support import private_file
from joinery.remote.paths import remote_home
from joinery.remote.seams import Notice

KEY_FILE = "vapid.pem"
#: Who to contact about these pushes, as push services ask (RFC 8292 "sub").
SUBJECT = "https://github.com/irchiinnuss/joinery"
#: The push services browsers use; a subscription elsewhere is refused.
PUSH_SERVICES = (
    "push.apple.com",
    "fcm.googleapis.com",
    "android.googleapis.com",
    "push.services.mozilla.com",
    "notify.windows.com",
)
#: What a phone is told about by default: everything but a plain "finished".
DEFAULT_KINDS = ("needs_you", "change_ready", "error")
ALL_KINDS = ("finished", "needs_you", "change_ready", "error")
RECORD_SIZE = 4096
TTL = 3600
MAX_SUBSCRIPTIONS = 3


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _point(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(
        ikm
    )


def encrypt(
    plaintext: bytes,
    ua_public: bytes,
    auth_secret: bytes,
    *,
    as_private: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """``plaintext`` for the phone holding ``ua_public`` (RFC 8291 ``aes128gcm``)."""
    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    sender = as_private or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    as_public = _point(sender.public_key())
    shared = sender.exchange(ec.ECDH(), receiver)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    # One record, so its delimiter is 2 (the last record), with no padding.
    sealed = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + struct.pack(">IB", RECORD_SIZE, len(as_public)) + as_public
    return header + sealed


class Vapid:
    """This machine's push signing key, kept in the per-user directory."""

    def __init__(self, home: Path | None = None) -> None:
        path = (home or remote_home()) / KEY_FILE
        if path.is_file():
            loaded = serialization.load_pem_private_key(path.read_bytes(), None)
            assert isinstance(loaded, ec.EllipticCurvePrivateKey)
            self.key = loaded
        else:
            self.key = ec.generate_private_key(ec.SECP256R1())
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(
                    self.key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    )
                )
            private_file(path)

    def public_key(self) -> str:
        """The application server key a browser subscribes with."""
        return b64(_point(self.key.public_key()))

    def header(self, endpoint: str, now: float | None = None) -> str:
        """``Authorization: vapid t=…, k=…`` for a push to ``endpoint``."""
        parts = urlsplit(endpoint)
        claims = {
            "aud": f"{parts.scheme}://{parts.netloc}",
            "exp": int((time.time() if now is None else now) + 12 * 3600),
            "sub": SUBJECT,
        }
        head = b64(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
        body = b64(json.dumps(claims, separators=(",", ":")).encode())
        signed = f"{head}.{body}".encode()
        r, s = decode_dss_signature(self.key.sign(signed, ec.ECDSA(hashes.SHA256())))
        signature = b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={head}.{body}.{signature}, k={self.public_key()}"


def checked_subscription(data: Any) -> dict[str, str]:
    """A browser's PushSubscription JSON, checked: HTTPS on a known push service, with
    a P-256 key and a 16-byte secret. Raises ValueError (TypeError) otherwise."""
    if not isinstance(data, dict):
        raise TypeError("Send the subscription.")
    endpoint = str(data.get("endpoint", ""))
    found = data.get("keys")
    keys: dict[str, Any] = found if isinstance(found, dict) else {}
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
        or len(endpoint) > 1000
        or not any(host == s or host.endswith("." + s) for s in PUSH_SERVICES)
    ):
        raise ValueError("That isn't a push service Joinery sends to.")
    try:
        public = unb64(str(keys.get("p256dh", "")))
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
        auth = unb64(str(keys.get("auth", "")))
    except (ValueError, TypeError) as exc:
        raise ValueError("The subscription's keys aren't right.") from exc
    if len(auth) != 16:
        raise ValueError("The subscription's keys aren't right.")
    return {"endpoint": endpoint, "p256dh": b64(public), "auth": b64(auth)}


Opener = Callable[[urllib.request.Request], int]


def _open(request: urllib.request.Request) -> int:
    try:
        with urllib.request.urlopen(request, timeout=15) as answer:
            return int(answer.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError, TimeoutError):
        return 0


class WebPushSink:
    """A :class:`joinery.remote.seams.NotificationSink` that pushes to paired phones."""

    def __init__(
        self,
        store: Any,
        home: Path | None = None,
        *,
        quiet: Callable[[str], bool] = lambda _project: False,
        opener: Opener = _open,
        background: bool = True,
    ) -> None:
        self.store = store
        self.vapid = Vapid(home)
        self.quiet = quiet
        self.opener = opener
        self.background = background

    def notify(self, notice: Notice) -> None:
        if self.quiet(notice.project_id):
            return  # The user is at the editor; the sidebar says it.
        if self.background:
            threading.Thread(target=self.send, args=(notice,), daemon=True).start()
        else:
            self.send(notice)

    def send(self, notice: Notice) -> int:
        """Push ``notice`` to every phone that wants it; returns how many were sent."""
        payload = json.dumps(
            {
                "kind": notice.kind,
                "project": notice.project,
                "project_id": notice.project_id,
                "text": notice.text,
            }
        ).encode()
        sent = 0
        for device, subscription in self.store.push_targets():
            if notice.kind not in subscription.get("kinds", DEFAULT_KINDS):
                continue
            allowed = device.get("projects") or []
            if allowed and notice.project_id not in allowed:
                continue
            body = encrypt(
                payload, unb64(subscription["p256dh"]), unb64(subscription["auth"])
            )
            request = urllib.request.Request(
                subscription["endpoint"],
                data=body,
                method="POST",
                headers={
                    "Authorization": self.vapid.header(subscription["endpoint"]),
                    "Content-Encoding": "aes128gcm",
                    "Content-Type": "application/octet-stream",
                    "TTL": str(TTL),
                    "Urgency": "high" if notice.kind == "needs_you" else "normal",
                    # Newer of the same kind replaces the older on the phone.
                    "Topic": f"{notice.kind}{notice.project_id}"[:32],
                },
            )
            status = self.opener(request)
            if status in (404, 410):
                # The phone dropped this subscription.
                self.store.remove_push(subscription["endpoint"])
            elif 200 <= status < 300:
                sent += 1
        return sent
