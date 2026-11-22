"""Keep-awake on Linux holds only on mains power (joinery.remote.awake, ADR 0093)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from joinery.remote import awake


def supply(root: Path, name: str, kind: str, online: str = "") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "type").write_text(kind + "\n")
    if online:
        (folder / "online").write_text(online + "\n")
    return folder


def test_on_mains(tmp_path: Path) -> None:
    assert awake.on_mains(tmp_path / "none")  # Nothing listed: a desktop or a VM.
    supply(tmp_path, "BAT0", "Battery")
    assert awake.on_mains(tmp_path)  # A battery alone says nothing about mains.
    ac = supply(tmp_path, "AC", "Mains", "0")
    assert not awake.on_mains(tmp_path)
    (ac / "online").write_text("1\n")
    assert awake.on_mains(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="Windows asks the system directly")
def test_linux_holds_only_on_mains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[list[str]] = []

    class Process:
        def __init__(self, argv: list[str], **_kw: Any) -> None:
            started.append(argv)
            self.alive = True

        def poll(self) -> int | None:
            return None if self.alive else 0

        def terminate(self) -> None:
            self.alive = False

        def wait(self, _timeout: float = 0) -> int:
            return 0

    monkeypatch.setattr(awake.sys, "platform", "linux")
    monkeypatch.setattr(awake.subprocess, "Popen", Process)
    monkeypatch.setattr(awake.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(awake, "POWER_SUPPLIES", tmp_path)
    ac = supply(tmp_path, "AC", "Mains", "0")
    keep = awake.KeepAwake()
    keep.follow(True)
    assert started == [] and not keep.on  # On battery: nothing held.
    (ac / "online").write_text("1\n")
    keep.follow(True)
    assert len(started) == 1 and started[0][0] == "systemd-inhibit" and keep.on
    (ac / "online").write_text("0\n")
    keep.follow(True)
    assert not keep.on  # Unplugged: let go.
