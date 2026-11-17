"""PNG pixels read in Python, for the art bible's sprite colour counts without a worker.

The bible's ``sprites.max_colours`` rule counts the distinct opaque colours of each
changed sprite. That used to decode every image with ffmpeg in the offline creative
worker, which runs emulated on Apple Silicon. A plain PNG is only zlib and five row
filters, so it is read here instead and counted exactly as the worker's script
counts ffmpeg's RGBA output.

Only what is certain to give the same RGBA bytes is read: 8-bit greyscale, grey
with alpha, RGB, RGBA and palette images (a palette's tRNS alpha included), not
interlaced. Anything else (16-bit, fewer than 8 bits, interlaced, a colour key on a
non-palette image, JPEG, WebP, a damaged file) returns None and is counted by
ffmpeg in the worker as before. ``tests/test_png_pixels.py`` compares both.

The palette and albedo checks still sample in the worker: ffmpeg's
``scale=64:64:flags=neighbor`` picks the same pixels a point filter would, but its
scaler converts through YUV on the way, which moves channels by one here and there,
so no Python reading gives byte-identical samples.
"""

from __future__ import annotations

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: Channels per colour type, for the 8-bit images read here.
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
#: Larger images go to the worker: decoding them here would take longer.
MAX_PIXELS = 1024 * 1024


class Rgba:
    """An image as rows of RGBA bytes."""

    def __init__(self, width: int, height: int, rows: list[bytes]) -> None:
        self.width = width
        self.height = height
        self.rows = rows


def decode(data: bytes) -> Rgba | None:
    """The PNG's pixels as RGBA, or None when it is not one this module reads."""
    try:
        return _decode(data)
    except (struct.error, zlib.error, IndexError, ValueError):
        return None


def _decode(data: bytes) -> Rgba | None:
    if data[:8] != SIGNATURE:
        return None
    offset = 8
    header: tuple[int, ...] | None = None
    palette = b""
    alpha = b""
    idat: list[bytes] = []
    while offset + 8 <= len(data):
        (length,) = struct.unpack_from(">I", data, offset)
        kind = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        if len(body) != length or len(data) < offset + 12 + length:
            return None
        (crc,) = struct.unpack_from(">I", data, offset + 8 + length)
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            return None
        offset += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            alpha = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if header is None or not idat:
        return None
    width, height, depth, colour, compression, filtering, interlace = header
    if (
        depth != 8
        or colour not in _CHANNELS
        or compression
        or filtering
        or interlace
        or not 0 < width * height <= MAX_PIXELS
    ):
        return None
    if alpha and colour != 3:
        return None  # a colour key: ffmpeg's handling is its own
    if colour == 3 and (not palette or len(palette) % 3):
        return None
    channels = _CHANNELS[colour]
    stride = width * channels
    raw = zlib.decompress(b"".join(idat))
    if len(raw) < (stride + 1) * height:
        return None
    rows = _unfilter(raw, stride, channels, height)
    if rows is None:
        return None
    if colour == 3 and max(max(row) for row in rows) >= len(palette) // 3:
        return None  # an index past the palette: ffmpeg's fill is its own
    return Rgba(width, height, [_rgba(row, colour, palette, alpha) for row in rows])


def _unfilter(raw: bytes, stride: int, bpp: int, height: int) -> list[bytes] | None:
    rows: list[bytes] = []
    previous = bytearray(stride)
    at = 0
    for _ in range(height):
        kind = raw[at]
        line = bytearray(raw[at + 1 : at + 1 + stride])
        at += stride + 1
        if kind == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif kind == 2:
            line = bytearray(
                (a + b) & 0xFF for a, b in zip(line, previous, strict=True)
            )
        elif kind == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif kind == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = previous[i]
                c = previous[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[i] = (line[i] + pred) & 0xFF
        elif kind != 0:
            return None
        rows.append(bytes(line))
        previous = line
    return rows


def _rgba(row: bytes, colour: int, palette: bytes, alpha: bytes) -> bytes:
    if colour == 6:
        return row
    out = bytearray(len(row) // _CHANNELS[colour] * 4)
    if colour == 2:
        out[0::4], out[1::4], out[2::4] = row[0::3], row[1::3], row[2::3]
        out[3::4] = b"\xff" * (len(row) // 3)
    elif colour == 0:
        out[0::4] = out[1::4] = out[2::4] = row
        out[3::4] = b"\xff" * len(row)
    elif colour == 4:
        out[0::4] = out[1::4] = out[2::4] = row[0::2]
        out[3::4] = row[1::2]
    else:
        table = [
            palette[i * 3 : i * 3 + 3] + bytes([alpha[i] if i < len(alpha) else 255])
            for i in range(len(palette) // 3)
        ]
        out = bytearray(b"".join(table[index] for index in row))
    return bytes(out)


def distinct_opaque(image: Rgba) -> int:
    """Distinct RGB colours among pixels with alpha 128 or more."""
    seen: set[bytes] = set()
    for row in image.rows:
        seen.update(row[i : i + 3] for i in range(0, len(row), 4) if row[i + 3] >= 128)
    return len(seen)


def count_output(path: str, data: bytes) -> str | None:
    """What the worker's colour-counting script prints for one image, or None."""
    image = decode(data)
    if image is None:
        return None
    return f"JOINERY_FILE:{path}\nJOINERY_COLOURS:{distinct_opaque(image)}"
