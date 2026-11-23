"""Web Push for Joinery Remote (joinery.remote.push, ADR 0093)."""

from __future__ import annotations

import json
import os
import struct
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from joinery.remote.push import (
    Vapid,
    WebPushSink,
    b64,
    checked_subscription,
    encrypt,
    unb64,
)
from joinery.remote.seams import Notice
from joinery.remote.store import Store, digest

# RFC 8291, section 5 and Appendix A.
PLAINTEXT = b"When I grow up, I want to be a watermelon"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
SALT = "DGv6ra1nlYgDCS1FRnbzlw"
BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
    "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
    "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)
ENDPOINT = "https://web.push.apple.com/QGuQyavXutnMH4tCa1b"


def key(raw: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(unb64(raw), "big"), ec.SECP256R1())


def decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """What the phone's browser does with a push (RFC 8291), for checking."""
    salt, size, length = body[:16], *struct.unpack(">IB", body[16:21])
    assert size == 4096
    as_public = body[21 : 21 + length]
    ua_public = ua_private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    shared = ua_private.exchange(
        ec.ECDH(),
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public),
    )

    def hkdf(s: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=n, salt=s, info=info).derive(ikm)

    ikm = hkdf(auth, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, body[21 + length :], None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def test_encryption_matches_rfc_8291() -> None:
    body = encrypt(
        PLAINTEXT,
        unb64(UA_PUBLIC),
        unb64(AUTH),
        as_private=key(AS_PRIVATE),
        salt=unb64(SALT),
    )
    assert b64(body) == BODY
    assert decrypt(body, key(UA_PRIVATE), unb64(AUTH)) == PLAINTEXT


def test_vapid_signs_for_the_push_service(tmp_path: Path) -> None:
    vapid = Vapid(tmp_path)
    # Windows keeps it private with an ACL instead; the mode bits say nothing there.
    assert os.name == "nt" or (tmp_path / "vapid.pem").stat().st_mode & 0o777 == 0o600
    assert Vapid(tmp_path).public_key() == vapid.public_key()
    header = vapid.header(ENDPOINT, now=1_800_000_000)
    token = header.split("t=", 1)[1].split(",", 1)[0]
    k = header.split("k=", 1)[1]
    head, body, signature = token.split(".")
    claims = json.loads(unb64(body))
    assert claims == {
        "aud": "https://web.push.apple.com",
        "exp": 1_800_000_000 + 12 * 3600,
        "sub": "https://github.com/irchiinnuss/joinery",
    }
    raw = unb64(signature)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(k))
    public.verify(
        encode_dss_signature(
            int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
        ),
        f"{head}.{body}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://web.push.apple.com/x",
        "https://evil.example/x",
        "https://push.apple.com.evil.example/x",
        "https://user:pw@web.push.apple.com/x",
        "https://web.push.apple.com:8443/x",
        "https://127.0.0.1/x",
    ],
)
def test_only_known_push_services(endpoint: str) -> None:
    with pytest.raises(ValueError):
        checked_subscription(
            {"endpoint": endpoint, "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}}
        )


def test_a_subscription_is_checked() -> None:
    good = checked_subscription(
        {"endpoint": ENDPOINT, "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}}
    )
    assert good == {"endpoint": ENDPOINT, "p256dh": UA_PUBLIC, "auth": AUTH}
    for keys in (
        {"p256dh": "AAAA", "auth": AUTH},
        {"p256dh": UA_PUBLIC, "auth": "AAAA"},
    ):
        with pytest.raises(ValueError):
            checked_subscription({"endpoint": ENDPOINT, "keys": keys})
    with pytest.raises(TypeError):
        checked_subscription("nope")


def paired(store: Store, kinds: list[str]) -> str:
    made = store.new_code()
    request = store.redeem(made["code"], "Phone", "iOS", digest("t"), digest("s"))
    device_id = store.answer(request["id"], True)["device_id"]
    store.add_push(
        device_id,
        checked_subscription(
            {"endpoint": ENDPOINT, "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}}
        ),
        kinds,
    )
    return str(device_id)


NOTICE = Notice("change_ready", "p1", "Harbour", "A change waits in Harbour.", "tx1")


def test_the_sink_pushes_what_a_phone_wants(tmp_path: Path) -> None:
    store = Store(tmp_path)
    paired(store, ["change_ready", "needs_you"])
    sent: list[urllib.request.Request] = []

    def opener(request: urllib.request.Request) -> int:
        sent.append(request)
        return 201

    sink = WebPushSink(store, tmp_path, opener=opener, background=False)
    assert sink.send(NOTICE) == 1
    [request] = sent
    assert request.full_url == ENDPOINT
    headers = {k.lower(): v for k, v in request.header_items()}
    assert headers["content-encoding"] == "aes128gcm"
    assert headers["authorization"].startswith("vapid t=")
    assert headers["ttl"] == "3600"
    assert isinstance(request.data, bytes)
    payload: dict[str, Any] = json.loads(
        decrypt(request.data, key(UA_PRIVATE), unb64(AUTH))
    )
    assert payload == {
        "kind": "change_ready",
        "project": "Harbour",
        "project_id": "p1",
        "text": "A change waits in Harbour.",
    }
    # A kind the phone didn't ask for is not sent.
    assert sink.send(Notice("finished", "p1", "Harbour", "Done.")) == 0


def test_quiet_while_at_the_editor_and_dropped_when_gone(tmp_path: Path) -> None:
    store = Store(tmp_path)
    device_id = paired(store, ["change_ready"])
    calls: list[str] = []
    sink = WebPushSink(
        store,
        tmp_path,
        quiet=lambda project: project == "p1",
        opener=lambda r: calls.append(r.full_url) or 201,
        background=False,
    )
    sink.notify(NOTICE)
    assert calls == []
    gone = WebPushSink(store, tmp_path, opener=lambda r: 410, background=False)
    assert gone.send(NOTICE) == 0
    assert store.push_targets() == []
    shown = next(d for d in store.devices() if d["id"] == device_id)
    assert shown["push"] == 0 and "p256dh" not in json.dumps(shown)


def test_a_revoked_or_watch_less_device_hears_nothing(tmp_path: Path) -> None:
    store = Store(tmp_path)
    device_id = paired(store, ["change_ready"])
    store.revoke(device_id)
    assert store.push_targets() == []
