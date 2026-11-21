"""The pairing QR code (segno: pure Python, no network, no image library)."""

from __future__ import annotations

import io
from pathlib import Path

import segno


def pair_url(origin: str, code: str) -> str:
    """The page that pairs with ``code``. The code rides in the fragment, which the
    browser never sends: it stays out of every log and proxy until the page posts it."""
    return origin.rstrip("/") + "/#pair=" + code.replace("-", "")


def terminal(url: str) -> str:
    """``url`` as a QR code drawn with text, for ``joinery remote pair``."""
    out = io.StringIO()
    segno.make_qr(url, error="m").terminal(out=out, compact=True, border=2)
    return out.getvalue()


def png(url: str, path: Path, scale: int = 8) -> Path:
    """``url`` as a PNG the editor's Settings → Remote shows."""
    segno.make_qr(url, error="m").save(
        str(path), kind="png", scale=scale, border=2, dark="#111111", light="#ffffff"
    )
    return path
