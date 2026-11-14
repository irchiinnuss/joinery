"""`joinery todo-go`: when a to-do waits for the user's Go."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery import done_when
from joinery.cli import main as cli_main


def test_todo_go_setting_from_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "joinery.toml").write_text('[project]\nname = "x"\n')
    assert cli_main(["todo-go", "--project", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["todo_go"] == done_when.DEFAULT_GO
    assert cli_main(["todo-go", "--project", str(tmp_path), "--set", "never"]) == 0
    assert done_when.go_mode(tmp_path) == "never"
    assert cli_main(["todo-go", "--project", str(tmp_path), "--set", "ask"]) == 0
    assert done_when.go_mode(tmp_path) == "multi"
    assert '[project]\nname = "x"' in (tmp_path / "joinery.toml").read_text()
