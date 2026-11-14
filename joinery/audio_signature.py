"""A loudness-over-time-and-frequency signature for audio renders (``snd:``).

SuperCollider's server seeds its noise generators (WhiteNoise, PinkNoise,
Dust, rand, ...) from the clock, so an NRT render of the same patch gives
different samples every run: the same sound, but never the same bytes. Edits
after the render (trim to the first and last sample above a threshold, then
normalise) shift a few milliseconds and a fraction of a dB along with it. A
byte hash can then never be reproduced, however correct the recipe is.

So, like ``img:`` for camera renders, such audio is compared by what a
listener hears rather than by bytes. ffmpeg decodes it to mono and splits it
into third-octave bands; each band's level is measured in short time slices.
Two renders match when they have the same channels and sample rate, nearly the
same length, and their band levels agree closely on average. Different noise
grains do not move those levels, while a changed pitch, filter, envelope,
timing, length or loudness does.

The fingerprint keeps the levels themselves (a hash could not be compared with
a tolerance), so it is longer than the others; a long piece is summarised in
at most :data:`MAX_SLICES` slices.
"""

from __future__ import annotations

import array
import base64
import math
import sys

PREFIX = "snd:"
#: Analysis sample rate: bands reach 11 kHz, well into what defines a sound.
RATE = 22050
#: One time slice: 50 ms, fine enough to see an attack or a gap move.
SLICE = 1102
#: Long audio averages pairs of slices until it fits.
MAX_SLICES = 64
#: Third-octave band centres, 100 Hz to 8 kHz, plus everything below and above.
CENTRES = tuple(round(100 * 2 ** (k / 3)) for k in range(20))
#: Cells more than this far (dB) below the loudest cell count as silence:
#: the quiet edges of a sound are where noise grains matter most.
RANGE_DB = 40
#: Largest average difference (dB) between two renders' audible band levels.
#: Renders of one noisy patch differ by about 1 dB, made of noise grains; a
#: changed sound is several dB off in the bands it touches.
MEAN_DB = 1.5
#: Largest overall loudness drift (dB, signed mean over audible cells).
DRIFT_DB = 0.5
#: Largest average difference within one loud time slice, so a change
#: confined to a moment (a quieter tail, a missing click) is not averaged away.
SLICE_DB = 2.5
#: A slice is judged on its own when its loudest band is within this many dB
#: of the loudest cell and it has SLICE_BANDS audible bands.
SLICE_RANGE_DB = 24
SLICE_BANDS = 4
#: Length tolerance: the larger of 30 ms and 2 %.
LENGTH_S = 0.03
LENGTH_SHARE = 0.02


def _edges() -> list[tuple[float | None, float | None]]:
    third = 2 ** (1 / 6)
    bands: list[tuple[float | None, float | None]] = [(None, CENTRES[0] / third)]
    bands += [(c / third, c * third) for c in CENTRES]
    bands.append((CENTRES[-1] * third, None))
    return bands


BANDS = len(CENTRES) + 2


def ffmpeg_argv(source: str, target: str) -> list[str]:
    """ffmpeg command writing every band, one after another, to `target` (raw f32le).

    The bands are concatenated in time rather than merged into channels:
    ffmpeg has no channel layout for this many, and the order is enough.
    """
    parts = []
    labels = []
    for index, (low, high) in enumerate(_edges()):
        chain = []
        # Two second-order sections a side: 24 dB per octave band edges.
        if low is not None:
            chain += [f"highpass=f={low:.1f}"] * 2
        if high is not None:
            chain += [f"lowpass=f={high:.1f}"] * 2
        parts.append(f"[s{index}]" + ",".join(chain) + f"[b{index}]")
        labels.append(f"[b{index}]")
    graph = (
        f"[0:a]aformat=channel_layouts=mono,aresample={RATE},asplit={BANDS}"
        + "".join(f"[s{i}]" for i in range(BANDS))
        + ";"
        + ";".join(parts)
        + ";"
        + "".join(labels)
        + f"concat=n={BANDS}:v=0:a=1[out]"
    )
    return [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-i",
        source,
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        target,
    ]


def levels(raw: bytes) -> tuple[list[list[float]], int]:
    """Mean-square power per slice and band from the bands, back to back.

    Returns the slices and how many 50 ms slices each one averages (a power of
    two, 1 unless the audio is longer than MAX_SLICES slices).
    """
    samples = array.array("f")
    samples.frombytes(raw[: len(raw) - len(raw) % (4 * BANDS)])
    if sys.byteorder != "little":
        samples.byteswap()
    frames = len(samples) // BANDS
    if not frames:
        raise ValueError("no audio decoded")
    powers: list[list[float]] = []
    for start in range(0, frames, SLICE):
        end = min(frames, start + SLICE)
        row = []
        for band in range(BANDS):
            offset = band * frames
            part = samples[offset + start : offset + end]
            row.append(math.sumprod(part, part) / len(part))
        powers.append(row)
    merge = 1
    while len(powers) > MAX_SLICES:
        powers = [_mean(powers[i : i + 2]) for i in range(0, len(powers), 2)]
        merge *= 2
    return powers, merge


def _mean(rows: list[list[float]]) -> list[float]:
    return [sum(column) / len(rows) for column in zip(*rows, strict=True)]


def _db(power: float) -> int:
    return max(-100, min(0, round(10 * math.log10(power)))) if power > 0 else -100


def signature(raw: bytes, channels: int, rate: int, seconds: float) -> str:
    """The ``snd:`` fingerprint from ffmpeg's band output and the file's format."""
    powers, merge = levels(raw)
    cells = bytes(-_db(p) for row in powers for p in row)
    body = base64.urlsafe_b64encode(cells).decode().rstrip("=")
    return f"{PREFIX}{channels}:{rate}:{round(seconds * 1000)}:{merge}:{body}"


def _parse(value: str) -> tuple[int, int, int, int, list[list[int]]]:
    channels, rate, ms, merge, body = value.removeprefix(PREFIX).split(":")
    cells = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    rows = [
        [-cell for cell in cells[i : i + BANDS]] for i in range(0, len(cells), BANDS)
    ]
    return int(channels), int(rate), int(ms), int(merge), rows


def _coarser(rows: list[list[int]], factor: int) -> list[list[int]]:
    """Average slices in groups of `factor` (by power), to match a longer file."""
    out = []
    for i in range(0, len(rows), factor):
        group = rows[i : i + factor]
        out.append(
            [
                _db(sum(10 ** (v / 10) for v in column) / len(group))
                for column in zip(*group, strict=True)
            ]
        )
    return out


def difference(expected: str, measured: str) -> str | None:
    """How two ``snd:`` fingerprints sound different, or None when they match."""
    if expected == measured:
        return None
    if not (expected.startswith(PREFIX) and measured.startswith(PREFIX)):
        return "not both sound fingerprints"
    try:
        ch_a, rate_a, ms_a, merge_a, rows_a = _parse(expected)
        ch_b, rate_b, ms_b, merge_b, rows_b = _parse(measured)
    except ValueError:
        return "unreadable sound fingerprint"
    if (ch_a, rate_a) != (ch_b, rate_b):
        return f"format {ch_a} ch {rate_a} Hz became {ch_b} ch {rate_b} Hz"
    if abs(ms_a - ms_b) > max(LENGTH_S * 1000, LENGTH_SHARE * max(ms_a, ms_b)):
        return f"length {ms_a} ms became {ms_b} ms"
    if merge_a < merge_b:
        rows_a = _coarser(rows_a, merge_b // merge_a)
    elif merge_b < merge_a:
        rows_b = _coarser(rows_b, merge_a // merge_b)
    pairs = list(zip(rows_a, rows_b, strict=False))
    peak = max((max(*ra, *rb) for ra, rb in pairs), default=-100)
    floor = peak - RANGE_DB
    rows = [
        [
            max(a, floor) - max(b, floor)
            for a, b in zip(row_a, row_b, strict=True)
            if max(a, b) >= floor
        ]
        for row_a, row_b in pairs
    ]
    diffs = [d for row in rows for d in row]
    if not diffs:
        return None  # both silent throughout
    drift = sum(diffs) / len(diffs)
    if abs(drift) > DRIFT_DB:
        return f"loudness differs by {-drift:+.1f} dB"
    mean = sum(abs(d) for d in diffs) / len(diffs)
    if mean > MEAN_DB:
        return f"band levels differ by {mean:.1f} dB on average"
    for index, ((row_a, row_b), row) in enumerate(zip(pairs, rows, strict=True)):
        loud = max(*row_a, *row_b) >= peak - SLICE_RANGE_DB
        if not loud or len(row) < SLICE_BANDS:
            continue
        local = sum(abs(d) for d in row) / len(row)
        if local > SLICE_DB:
            at = index * max(merge_a, merge_b) * SLICE / RATE
            return f"band levels differ by {local:.1f} dB at {at:.2f} s"
    return None


def similar(expected: str, measured: str) -> bool:
    """Whether two ``snd:`` fingerprints describe the same sound."""
    return difference(expected, measured) is None
