"""No file path reaches a device (joinery.remote.media.scrub, ADR 0093)."""

from __future__ import annotations

import os

import pytest

from joinery.remote.media import scrub

PROJECT = os.sep + os.path.join("Users", "ana", "My Games", "Harbour")


@pytest.mark.parametrize(
    "path",
    [
        "/Users/ana/My Games/Other/art.png",
        "/home/ana/Game Jam/build.log",
        "/Volumes/Work Disk/assets/a.wav",
        "/tmp/joinery run/out.txt",
        "C:\\Users\\Ana\\My Games\\art.png",
        "D:/Game Jam/art.png",
        "\\\\server\\share\\My Art\\a.png",
        "/opt/godot/bin",
    ],
)
def test_paths_with_spaces_are_dropped(path: str) -> None:
    assert scrub({"text": path, "items": [path]}, PROJECT) == {
        "text": "",
        "items": [""],
    }


def test_a_path_in_the_project_becomes_project_relative() -> None:
    inside = PROJECT + os.sep + os.path.join("art", "Big Tree.png")
    assert scrub(inside, PROJECT) == "art/Big Tree.png"


@pytest.mark.parametrize(
    "words",
    [
        "/variants 3 a calmer sky",
        "/feature add audio",
        "// a comment line",
        "Use the Users/ana folder",
        "Made it taller.",
    ],
)
def test_words_stay(words: str) -> None:
    assert scrub(words, PROJECT) == words
