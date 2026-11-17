"""The art bible's PNG pixels read in Python give what the worker's ffmpeg gives."""

from __future__ import annotations

import asyncio
import random
import shutil
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Any

import pytest

from joinery import bible_rules, png_pixels
from joinery.runner import Result

Pixel = tuple[int, int, int, int]


def _chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    )


def _predict(kind: int, a: int, b: int, c: int) -> int:
    if kind == 1:
        return a
    if kind == 2:
        return b
    if kind == 3:
        return (a + b) >> 1
    if kind == 4:
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        return a if pa <= pb and pa <= pc else b if pb <= pc else c
    return 0


def encode(
    width: int,
    rows: list[bytes],
    colour: int,
    *,
    palette: bytes = b"",
    alpha: bytes = b"",
    depth: int = 8,
    interlace: int = 0,
) -> bytes:
    """A PNG of raw ``rows``, each row with a different filter (0 to 4)."""
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
    data = bytearray()
    previous = bytes(len(rows[0]))
    for number, row in enumerate(rows):
        kind = number % 5
        data.append(kind)
        for i, value in enumerate(row):
            a = row[i - bpp] if i >= bpp else 0
            c = previous[i - bpp] if i >= bpp else 0
            data.append((value - _predict(kind, a, previous[i], c)) & 0xFF)
        previous = row
    header = struct.pack(">IIBBBBB", width, len(rows), depth, colour, 0, 0, interlace)
    return (
        png_pixels.SIGNATURE
        + _chunk(b"IHDR", header)
        + (_chunk(b"PLTE", palette) if palette else b"")
        + (_chunk(b"tRNS", alpha) if alpha else b"")
        + _chunk(b"IDAT", zlib.compress(bytes(data)))
        + _chunk(b"IEND", b"")
    )


def fixtures() -> dict[str, tuple[bytes, list[list[Pixel]]]]:
    """Seeded images of every kind read here, with the RGBA each one holds."""
    rng = random.Random(4242)
    made: dict[str, tuple[bytes, list[list[Pixel]]]] = {}
    for width, height in [(1, 1), (3, 5), (37, 11), (130, 70)]:

        def byte() -> int:
            # Few distinct values, so colours repeat and counts mean something.
            return rng.choice([0, 17, 64, 128, 200, 255])

        rgba = [
            [(byte(), byte(), byte(), byte()) for _ in range(width)]
            for _ in range(height)
        ]
        made[f"rgba_{width}x{height}.png"] = (
            encode(width, [bytes(v for p in row for v in p) for row in rgba], 6),
            rgba,
        )
        made[f"rgb_{width}x{height}.png"] = (
            encode(width, [bytes(v for p in row for v in p[:3]) for row in rgba], 2),
            [[(*p[:3], 255) for p in row] for row in rgba],
        )
        made[f"grey_{width}x{height}.png"] = (
            encode(width, [bytes(p[0] for p in row) for row in rgba], 0),
            [[(p[0], p[0], p[0], 255) for p in row] for row in rgba],
        )
        made[f"greyalpha_{width}x{height}.png"] = (
            encode(
                width, [bytes(v for p in row for v in (p[0], p[3])) for row in rgba], 4
            ),
            [[(p[0], p[0], p[0], p[3]) for p in row] for row in rgba],
        )
        palette = [(byte(), byte(), byte()) for _ in range(7)]
        alpha = bytes(byte() for _ in range(4))
        indices = [[rng.randrange(7) for _ in range(width)] for _ in range(height)]
        made[f"palette_{width}x{height}.png"] = (
            encode(
                width,
                [bytes(row) for row in indices],
                3,
                palette=bytes(v for c in palette for v in c),
                alpha=alpha,
            ),
            [
                [(*palette[i], alpha[i] if i < len(alpha) else 255) for i in row]
                for row in indices
            ],
        )
    return made


def test_every_kind_read_here_decodes_to_its_pixels() -> None:
    for name, (data, pixels) in fixtures().items():
        image = png_pixels.decode(data)
        assert image is not None, name
        got = [
            [tuple(row[i : i + 4]) for i in range(0, len(row), 4)] for row in image.rows
        ]
        assert got == pixels, name


def test_what_ffmpeg_might_read_its_own_way_is_left_to_it() -> None:
    row = [bytes(6)]
    assert png_pixels.decode(encode(1, row, 2, depth=16)) is None
    assert png_pixels.decode(encode(2, [bytes(6)], 2, interlace=1)) is None
    assert (
        png_pixels.decode(encode(2, [bytes(6)], 2, alpha=b"\x00\x00\x00\x00\x00\x00"))
        is None
    )
    assert png_pixels.decode(encode(1, [b"\x03"], 3, palette=bytes(6))) is None
    assert png_pixels.decode(b"\xff\xd8\xff\xe0 a jpeg") is None
    good = encode(2, [bytes(6)], 2)
    assert png_pixels.decode(good[:-20]) is None
    assert png_pixels.decode(good[:40] + b"x" + good[41:]) is None


def _sh(script: str, root: Path, files: list[str]) -> str:
    return subprocess.run(
        ["sh", "-c", script, "sh", *files],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    ).stdout


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_colour_counts_match_the_worker_script(tmp_path: Path) -> None:
    made = fixtures()
    for name, (data, _pixels) in made.items():
        (tmp_path / name).write_bytes(data)
    worker = bible_rules.parse_colour_counts(
        _sh(bible_rules._COUNT_COLOURS, tmp_path, list(made))
    )
    native: dict[str, int] = {}
    for name, (data, _pixels) in made.items():
        said = png_pixels.count_output(name, data)
        assert said is not None
        native.update(bible_rules.parse_colour_counts(said))
    assert native == worker


def test_sprite_counts_of_plain_pngs_need_no_worker(tmp_path: Path) -> None:
    made = fixtures()
    (tmp_path / "art/sprites").mkdir(parents=True)
    for name in ["rgba_37x11.png", "palette_130x70.png"]:
        (tmp_path / "art/sprites" / name).write_bytes(made[name][0])
    (tmp_path / "art/sprites/hero.jpg").write_bytes(b"\xff\xd8 not decoded here")
    calls: list[list[str]] = []

    async def measure(argv: list[str], _root: Path) -> Result:
        calls.append(argv)
        return Result(
            True, "JOINERY_FILE:art/sprites/hero.jpg\nJOINERY_COLOURS:3\n", "", 0.0
        )

    rules: Any = bible_rules.SpriteRules(max_colours={"art/sprites": 4})
    paths = [
        "art/sprites/rgba_37x11.png",
        "art/sprites/palette_130x70.png",
        "art/sprites/hero.jpg",
    ]
    problems = asyncio.run(
        bible_rules._sprite_problems(tmp_path, paths, rules, measure)
    )
    # Only the JPEG went to the worker; both PNGs were counted here.
    assert [argv[4:] for argv in calls] == [["art/sprites/hero.jpg"]]
    assert sorted(p.split(":")[0] for p in problems) == [
        "art/sprites/palette_130x70.png",
        "art/sprites/rgba_37x11.png",
    ]
