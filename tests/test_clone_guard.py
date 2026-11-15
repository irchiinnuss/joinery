"""The clone guard: a request to reproduce a specific existing game gets a reminder to
ask "your own take or a twist?"; genre and mechanics inspiration never does, and the
reminder never blocks anything."""

from __future__ import annotations

from pathlib import Path

import pytest

from bridge import provider_process
from bridge.instructions import GAME_GUIDE
from bridge.protocol import Prompt
from joinery import clone_guard


@pytest.mark.parametrize(
    ("request_text", "name"),
    [
        ("Clone Super Mario Bros for me", "Super Mario Bros"),
        ("recreate the first level of Super Mario Bros", "Super Mario Bros"),
        ("make an exact copy of Hollow Knight", "Hollow Knight"),
        ("Can you remake Tetris?", "Tetris"),
        ("I want the same characters as Pokemon", "Pokemon"),
        ("copy Stardew Valley's art style", "Stardew Valley"),
        ("rip the sprites from Celeste", "Celeste"),
        ("Build a clone of Vampire Survivors", "Vampire Survivors"),
        ('recreate "Super Meat Boy"', "Super Meat Boy"),
        ("copy World 1-1 from Super Mario Bros", "Super Mario Bros"),
    ],
)
def test_requests_to_reproduce_a_game_get_the_reminder(
    request_text: str, name: str
) -> None:
    assert name in clone_guard.named_games(request_text)
    note = clone_guard.reminder(request_text)
    assert f'"{name}"' in note
    assert "own take or a twist" in note
    assert "original game in the same genre" in note
    assert "Borrowing a genre, a mechanic or a feel is fine" in note


@pytest.mark.parametrize(
    "request_text",
    [
        "a platformer with a dash like Celeste's",
        "make a roguelike inspired by Spelunky",
        "make the jump feel like Mario but floatier",
        "I love Hollow Knight, make something in that genre",
        "copy the Player node to the new scene",
        "copy this material onto the Door",
        "copy the @Door node",
        "duplicate the enemy and make it red",
        "rebuild the level with more platforms",
        "Port the player script to C#",
        "Remake the HUD with bigger hearts",
        "Make the bat faster",
    ],
)
def test_inspiration_and_project_edits_pass_quietly(request_text: str) -> None:
    assert clone_guard.reminder(request_text) == ""


def test_the_reminder_rides_in_the_turn_not_the_stable_prefix(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")

    def prompt(text: str) -> Prompt:
        return Prompt(text=text, project_dir=str(tmp_path))

    copy, plain = prompt("Remake Tetris exactly"), prompt("Add a jump")
    assert provider_process.stable_text(copy) == provider_process.stable_text(plain)
    assert "Clone check:" in provider_process.turn_part(copy)
    assert "Clone check:" not in provider_process.turn_part(plain)


def test_every_agent_is_told_to_ask_not_refuse() -> None:
    assert "own take or a twist" in GAME_GUIDE
    assert "never refuse those" in GAME_GUIDE
    assert "original game in the same genre" in GAME_GUIDE
