"""Store media: the bot aims the view at content, and the trailer records at full size."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from joinery import playtest, share_media

ROOT = Path(__file__).resolve().parents[1]
STOCK = ROOT / ".tools/Godot.app/Contents/MacOS/Godot"


def _godot() -> Path | None:
    for candidate in (
        os.environ.get("JOINERY_TEST_GODOT"),
        os.environ.get("JOINERY_GODOT_BIN"),
        str(STOCK),
    ):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


@pytest.mark.skipif(_godot() is None, reason="no Godot binary (set JOINERY_TEST_GODOT)")
def test_the_view_turns_to_what_can_be_seen(tmp_path: Path) -> None:
    project = tmp_path / "frame"
    project.mkdir()
    (project / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="F"\n'
    )
    shutil.copyfile(
        ROOT / "tests/godot/bot_frame_test.gd", project / "bot_frame_test.gd"
    )
    env = {
        **os.environ,
        "BOT_DIR": str(ROOT / "joinery/playtest_bot"),
        "JOINERY_BACKGROUND": "1",
    }
    binary = _godot()
    assert binary is not None
    got = subprocess.run(
        [
            str(binary),
            "--headless",
            "--path",
            str(project),
            "--script",
            "bot_frame_test.gd",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "bot_frame: ok" in got.stdout, got.stdout[-2000:] + got.stderr[-2000:]


def test_media_runs_ask_the_bot_to_frame_the_view(tmp_path: Path) -> None:
    plain = playtest._bot_args(playtest.Options(project=tmp_path), tmp_path)
    media = playtest._bot_args(playtest.Options(project=tmp_path, media=True), tmp_path)
    assert "--media" not in plain and "--media" in media


def test_the_trailer_records_at_the_store_size(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text(
        "[display]\nwindow/size/viewport_width=1280\nwindow/size/viewport_height=720\n"
    )
    settings = share_media.movie_override(tmp_path)
    assert settings is not None
    assert "window/size/window_width_override=1920" in settings
    assert "window/size/window_height_override=1080" in settings
    # A game drawn at a fixed base size keeps it, and is enlarged sharp.
    (tmp_path / "project.godot").write_text(
        '[display]\nwindow/stretch/mode="viewport"\n'
    )
    assert share_media.movie_override(tmp_path) is None
    graph = share_media.filter_graph(26.0, False, "/f.ttf", sharp=True)
    assert "scale=1920:1080:flags=neighbor" in graph
    assert "flags=neighbor" not in share_media.filter_graph(26.0, False, "/f.ttf")
