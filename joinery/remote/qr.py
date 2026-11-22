"""The pairing QR code (segno: pure Python, no network, no image library)."""

from __future__ import annotations

import io
import os
import tempfile
import time
from pathlib import Path

import segno

from joinery.platform_support import private_directory, private_file


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
    """``url`` as a PNG the editor's Settings → Remote shows. It holds the pairing
    code, so it is written readable by this user only, through a fresh file renamed
    into place: a link already at ``path`` is replaced, never followed."""
    out = io.BytesIO()
    segno.make_qr(url, error="m").save(
        out, kind="png", scale=scale, border=2, dark="#111111", light="#ffffff"
    )
    descriptor, temp = tempfile.mkstemp(prefix=".qr-", dir=path.parent)
    try:
        private_file(Path(temp))
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(out.getvalue())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return path


#: How long a pairing QR code's picture is kept (a code lasts 5 minutes).
KEEP_SECONDS = 10 * 60


def private_png(url: str, home: Path, name: str) -> Path:
    """``url`` as a PNG in the per-user Remote directory (0700), never a shared temp
    folder; pictures of codes long expired are removed on the way."""
    folder = home / "qr"
    if not folder.is_dir():
        folder.mkdir(mode=0o700)
        private_directory(folder)
    for old in folder.glob("*.png"):
        try:
            if old.stat().st_mtime < time.time() - KEEP_SECONDS:
                old.unlink()
        except OSError:
            pass
    return png(url, folder / f"{name}.png")
