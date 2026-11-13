"""Accept changes automatically: the project's opt-in (ADR 0085)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery import auto_accept
from joinery.cli import main


def test_off_by_default_and_saved_beside_other_settings(tmp_path: Path) -> None:
    assert auto_accept.enabled(tmp_path) is False
    (tmp_path / "joinery.toml").write_text(
        "[tools]\nblender = true\n\n[agents]\nreviewer = true\n", encoding="utf-8"
    )
    assert auto_accept.enabled(tmp_path) is False
    auto_accept.set_enabled(tmp_path, True)
    assert auto_accept.enabled(tmp_path) is True
    text = (tmp_path / "joinery.toml").read_text(encoding="utf-8")
    assert "blender = true" in text and "reviewer = true" in text
    assert "auto_accept = true" in text
    auto_accept.set_enabled(tmp_path, False)
    assert auto_accept.enabled(tmp_path) is False


def test_only_a_real_true_turns_it_on(tmp_path: Path) -> None:
    (tmp_path / "joinery.toml").write_text(
        '[agents]\nauto_accept = "yes"\n', encoding="utf-8"
    )
    assert auto_accept.enabled(tmp_path) is False
    (tmp_path / "joinery.toml").write_text("not toml [", encoding="utf-8")
    assert auto_accept.enabled(tmp_path) is False


def test_cli_reads_and_sets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = str(tmp_path)
    assert main(["auto-accept", "--project", project, "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"enabled": False}
    assert main(["auto-accept", "--project", project, "--json", "--set", "on"]) == 0
    assert json.loads(capsys.readouterr().out) == {"enabled": True}
    assert auto_accept.enabled(tmp_path) is True
    assert main(["auto-accept", "--project", project, "--set", "off"]) == 0
    assert "waits for your Accept" in capsys.readouterr().out


def test_cli_says_why_a_save_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "joinery.toml").write_text("[agents\n", encoding="utf-8")
    code = main(["auto-accept", "--project", str(tmp_path), "--json", "--set", "on"])
    assert code == 1
    assert "error" in json.loads(capsys.readouterr().out)
