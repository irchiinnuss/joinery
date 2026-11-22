"""Pairing at the machine (ADR 0093): the QR code's picture stays private, and the
question names who asked as well as the name the phone gave."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from remote_tailscale_fake import install, serve_config

from joinery.cli import main
from joinery.remote import tailscale
from joinery.remote.cli import confirm_text
from joinery.remote.store import Store

PORT = 47615


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "remote"
    folder.mkdir(mode=0o700)
    monkeypatch.setenv("JOINERY_REMOTE_HOME", str(folder))
    Store(folder).set_config(enabled=True, port=PORT)
    program = install(tmp_path / "ts", serve=serve_config(PORT))
    monkeypatch.setenv(tailscale.PROGRAM_ENV, program)
    return folder


def pair(capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, Any]:
    assert main(["remote", "pair", "--json", "--no-wait", *args]) == 0
    answer: dict[str, Any] = json.loads(capsys.readouterr().out.strip())
    return answer


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_the_qr_picture_is_written_privately(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    made = pair(capsys, "--qr")
    picture = Path(made["qr"])
    assert picture.parent == home / "qr"
    assert picture.read_bytes().startswith(b"\x89PNG")
    assert stat.S_IMODE(picture.stat().st_mode) == 0o600
    assert stat.S_IMODE(picture.parent.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="symbolic links")
def test_a_link_at_the_picture_path_is_replaced_not_followed(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    planted = tmp_path / "shared" / "qr.png"
    planted.parent.mkdir()
    planted.symlink_to(victim)
    pair(capsys, "--qr-png", str(planted))
    assert victim.read_text() == "keep me"
    assert not planted.is_symlink() and planted.read_bytes().startswith(b"\x89PNG")
    assert stat.S_IMODE(planted.stat().st_mode) == 0o600


def test_old_qr_pictures_are_removed(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = Path(pair(capsys, "--qr")["qr"])
    os.utime(first, (1_000_000, 1_000_000))
    second = Path(pair(capsys, "--qr")["qr"])
    assert second.exists() and not first.exists()


def test_the_question_names_the_tailnet_login() -> None:
    request = {"name": "Ana's iPhone", "platform": "iOS", "scopes": ["watch"]}
    asked = confirm_text({**request, "peer": "ana@example.com"})
    assert asked.startswith('Pair "Ana\'s iPhone" (iOS)?')
    assert "Signed in to Tailscale as ana@example.com." in asked
    # A racer can copy the name, not the login: none is said plainly.
    bare = confirm_text({**request, "peer": "127.0.0.1"})
    assert "no Tailscale login" in bare and "127.0.0.1" not in bare
