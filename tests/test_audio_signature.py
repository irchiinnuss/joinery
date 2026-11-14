from __future__ import annotations

import array
import hashlib
import json
import math
import shutil
import subprocess
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

import pytest

from joinery import audio_signature as snd
from joinery.recipe import load_recipe, matches, rebuild_one, record
from joinery.runner import Result
from tests.test_recipe import put, write_recipe

REAL = json.loads(
    (Path(__file__).parent / "fixtures" / "audio" / "scattergun_snd.json").read_text()
)


def test_real_renders_of_one_noisy_patch_match() -> None:
    # Each render drew new noise grains, so no two have the same bytes.
    for (a, x), (b, y) in combinations(REAL["same"].items(), 2):
        assert snd.difference(x, y) is None, (a, b, snd.difference(x, y))


@pytest.mark.parametrize("change", sorted(REAL["changed"]))
def test_a_real_edit_of_the_render_differs(change: str) -> None:
    for base in REAL["same"].values():
        assert snd.difference(base, REAL["changed"][change]) is not None


def bands(levels: list[list[float]]) -> bytes:
    """ffmpeg's output for slices of constant power per band (dBFS)."""
    out = array.array("f")
    for band in range(snd.BANDS):
        for row in levels:
            amplitude = math.sqrt(10 ** (row[band] / 10))
            out.extend([amplitude, -amplitude] * (snd.SLICE // 2))
    return out.tobytes()


def tone(slices: int = 12, level: float = -20.0) -> list[list[float]]:
    return [[level - band for band in range(snd.BANDS)] for _ in range(slices)]


def sign(levels: list[list[float]], seconds: float = 0.6) -> str:
    return snd.signature(bands(levels), 1, 44100, seconds)


def test_signature_reads_back_the_levels() -> None:
    value = sign(tone())
    assert value.startswith("snd:1:44100:600:1:")
    *_, rows = snd._parse(value)
    assert rows[0][:3] == [-20, -21, -22]


def test_tolerance_and_its_limits() -> None:
    base = sign(tone())
    grains = tone()
    for row, nudge in zip(grains, [1, -1, 0, 1, -1, 1, 0, -1, 1, -1, 0, 0]):
        row[3] += nudge
        row[9] -= nudge
    assert snd.similar(base, sign(grains))
    assert snd.difference(base, sign(tone(level=-21))) == "loudness differs by -1.0 dB"
    assert "length" in str(snd.difference(base, sign(tone(), seconds=0.7)))
    assert "format" in str(
        snd.difference(base, snd.signature(bands(tone()), 2, 44100, 0.6))
    )
    moment = tone()
    moment[5] = [v - 6 for v in moment[5]]
    assert "at 0.25 s" in str(snd.difference(base, sign(moment)))


def test_long_audio_is_summarised_and_still_compares() -> None:
    long = sign(tone(slices=200), seconds=10.0)
    _channels, _rate, _ms, merge, rows = snd._parse(long)
    assert merge == 4 and len(rows) <= snd.MAX_SLICES
    # A take summarised with a different merge is coarsened to compare.
    shorter = sign(tone(slices=128), seconds=9.9)
    assert snd._parse(shorter)[3] == 2
    assert snd.similar(long, shorter)
    fading = tone(slices=128)
    for row in fading[64:]:
        row[:] = [v - 6 for v in row]
    assert not snd.similar(long, sign(fading, seconds=9.9))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_the_band_split_runs_in_ffmpeg(tmp_path: Path) -> None:
    wav = tmp_path / "t.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=1000:d=0.5", str(wav)],
        check=True,
    )
    raw = tmp_path / "t.raw"
    subprocess.run(snd.ffmpeg_argv(str(wav), str(raw)), check=True)
    *_, rows = snd._parse(snd.signature(raw.read_bytes(), 1, 44100, 0.5))
    loudest = max(range(snd.BANDS), key=lambda band: rows[2][band])
    assert abs(snd.CENTRES[loudest - 1] - 1000) < 20  # band 0 is all below


@dataclass
class SoundRunner:
    """Renders a WAV, and answers ffprobe and the band split like ffmpeg would."""

    name: str = "fake"
    render: bytes = b"RIFFone"
    levels: list[list[float]] = field(default_factory=tone)
    calls: list[list[str]] = field(default_factory=list)

    def available(self) -> bool:
        return True

    def run(self, argv: list[str], project: Path, timeout: int = 900) -> Result:
        self.calls.append(argv)
        if argv[0] == "ffprobe":
            stream = {"channels": 1, "sample_rate": "44100"}
            return Result(True, json.dumps({"streams": [stream]}), "", 0.1)
        if argv[0] == "ffmpeg":
            (project / argv[-1]).write_bytes(bands(self.levels))
            return Result(True, "", "", 0.1)
        put(project, "audio/sfx/boom.wav", self.render)
        return Result(True, "rendered", "", 0.2)


NOISY = "Out.ar(0, WhiteNoise.ar * EnvGen.ar(Env.perc));"


def sc_recipe(project: Path, expect: str = "", patch: str = NOISY) -> Path:
    (project / "recipes").mkdir(parents=True, exist_ok=True)
    (project / "recipes" / "boom.scd").write_text(patch)
    body = (
        'version = 1\ntool = "supercollider"\nscript = "recipes/boom.scd"\n'
        'outputs = ["audio/sfx/boom.wav"]\n'
    )
    if expect:
        body += f'[expect]\n"audio/sfx/boom.wav" = "{expect}"\n'
    return write_recipe(project, "boom", body)


def test_a_supercollider_render_is_compared_by_sound(tmp_path: Path) -> None:
    put(tmp_path, "audio/sfx/boom.wav", b"RIFFone")
    path = sc_recipe(tmp_path)
    runner = SoundRunner()
    assert (
        record(path, tmp_path, runner).expect["audio/sfx/boom.wav"].startswith("snd:")
    )
    # New noise grains: other bytes, the same sound.
    runner.render = b"RIFFtwo"
    assert rebuild_one(path, tmp_path, runner).ok
    # A changed sound fails, and says how.
    runner.levels = tone(level=-24)
    result = rebuild_one(path, tmp_path, runner)
    assert not result.ok
    assert result.problems == (
        "audio/sfx/boom.wav: the rebuilt sound differs (loudness differs by -4.0 dB)",
    )


def test_a_render_recorded_by_bytes_is_read_by_sound(tmp_path: Path) -> None:
    put(tmp_path, "audio/sfx/boom.wav", b"RIFFone")
    legacy = "sha256:" + hashlib.sha256(b"RIFFone").hexdigest()
    path = sc_recipe(tmp_path, legacy)
    assert rebuild_one(path, tmp_path, SoundRunner(render=b"RIFFtwo")).ok
    assert load_recipe(path).expect["audio/sfx/boom.wav"] == legacy  # not rewritten
    # A file that no longer has the recorded bytes proves nothing.
    put(tmp_path, "audio/sfx/boom.wav", b"RIFFedited")
    assert not rebuild_one(path, tmp_path, SoundRunner(render=b"RIFFtwo")).ok


def test_only_sound_fingerprints_are_compared_loosely() -> None:
    assert matches("sha256:ab", "sha256:ab")
    assert not matches("sha256:ab", "sha256:ac")
    base = sign(tone())
    assert not matches("sha256:ab", base)
    assert matches(base, sign(tone()))


def test_a_seeded_or_noiseless_patch_keeps_the_exact_check(tmp_path: Path) -> None:
    put(tmp_path, "audio/sfx/boom.wav", b"RIFFone")
    seeded = "RandID.ir(0); RandSeed.ir(1, 7); " + NOISY
    for patch in (seeded, "Out.ar(0, SinOsc.ar(440));"):
        path = sc_recipe(tmp_path, patch=patch)
        assert (
            record(path, tmp_path, SoundRunner())
            .expect["audio/sfx/boom.wav"]
            .startswith("sha256:")
        )
