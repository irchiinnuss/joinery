"""An asset made earlier in a change and deleted again takes its records with it."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from joinery import lockfile, staged_removal
from joinery.gateway import ProjectGateway
from joinery.transactions import FileTransaction

RECIPE = """version = 1
tool = "surge-xt"
script = "recipes/take1.py"
outputs = ["audio/sources/sfx/gem.json", "audio/sfx/gem.wav"]
"""

LOCK = """version = 1

[[asset]]
path = "audio/sfx/gem.wav"
sha256 = "aa"
origin = "tool-created"
tool = "surge-xt"
recipe = "recipes/take1.toml"

[[asset]]
path = "audio/sfx/kept.wav"
sha256 = "bb"
origin = "tool-created"
tool = "surge-xt"
recipe = "recipes/old.toml"

[[asset]]
path = "audio/sources/sfx/gem.json"
sha256 = "cc"
origin = "tool-created"
tool = "surge-xt"
recipe = "recipes/take1.toml"
"""


def _change(tmp_path: Path) -> tuple[Path, Path, FileTransaction]:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "audio/sfx").mkdir(parents=True)
    (tmp_path / "audio/sfx/kept.wav").write_bytes(b"RIFF")
    tx = FileTransaction(tmp_path)
    tx.begin("Gem sound")
    _make_take(tx.stage)
    return tmp_path, tx.stage, tx


def _make_take(stage: Path) -> None:
    """A Surge XT take made earlier in the change, and the project's lock."""
    for rel, text in {
        "audio/sfx/gem.wav": "RIFF",
        "audio/sfx/gem.wav.import": "[remap]",
        "audio/sources/sfx/gem.json": "{}",
        "recipes/take1.toml": RECIPE,
        "recipes/take1.py": "print()",
        "joinery.lock": LOCK,
    }.items():
        (stage / rel).parent.mkdir(parents=True, exist_ok=True)
        (stage / rel).write_text(text)


def _locked(stage: Path) -> set[str]:
    return {e.path for e in lockfile.read_assets(stage)}


def test_deleting_a_new_take_clears_its_lock_sidecar_and_recipe(tmp_path: Path) -> None:
    project, stage, _tx = _change(tmp_path)
    (stage / "audio/sfx/gem.wav").unlink()
    cleared = staged_removal.forget(project, stage, ["audio/sfx/gem.wav"])
    assert "audio/sfx/gem.wav.import" in cleared
    assert not (stage / "audio/sfx/gem.wav.import").exists()
    # Its source is still there, so the recipe stays for now.
    assert (stage / "recipes/take1.toml").is_file()
    assert _locked(stage) == {"audio/sfx/kept.wav", "audio/sources/sfx/gem.json"}

    (stage / "audio/sources/sfx/gem.json").unlink()
    staged_removal.forget(project, stage, [str(stage / "audio/sources/sfx/gem.json")])
    assert not (stage / "recipes/take1.toml").exists()
    assert not (stage / "recipes/take1.py").exists()
    assert _locked(stage) == {"audio/sfx/kept.wav"}


def test_a_recipe_deleted_by_hand_takes_its_script(tmp_path: Path) -> None:
    project, stage, _tx = _change(tmp_path)
    (stage / "recipes/take1.toml").unlink()
    assert staged_removal.forget(project, stage, ["recipes/take1.toml"]) == [
        "recipes/take1.py"
    ]


def test_what_the_project_already_has_is_left_alone(tmp_path: Path) -> None:
    project, stage, _tx = _change(tmp_path)
    (stage / "audio/sfx/kept.wav").unlink()
    assert staged_removal.forget(project, stage, ["audio/sfx/kept.wav"]) == []
    assert "audio/sfx/kept.wav" in _locked(stage)


def test_the_gateway_clears_after_a_godot_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, stage, tx = _change(tmp_path)
    (project / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    gateway = ProjectGateway(project)
    gateway.transaction = tx
    # The gateway brings its stage up to date first; the take is made after.
    gateway.staging()
    _make_take(stage)

    class Deletes:
        tools: ClassVar[dict[str, Any]] = {"delete_file": object()}

        async def call(
            self, _tool: str, arguments: dict[str, Any], **_k: Any
        ) -> CallToolResult:
            (stage / arguments["filePath"]).unlink(missing_ok=True)
            return CallToolResult(content=[TextContent(type="text", text="deleted")])

        async def close(self) -> None:
            return None

    fake = Deletes()
    gateway.connection = AsyncMock(return_value=fake)  # type: ignore[method-assign]
    gateway.staged["godot"] = fake  # type: ignore[assignment]
    # No Godot here: the staged import a sound would get is skipped.
    gateway._import_for = AsyncMock()  # type: ignore[method-assign]

    async def go() -> CallToolResult:
        try:
            return await gateway.call(
                "godot",
                "delete_file",
                {"projectPath": str(project), "filePath": "audio/sfx/gem.wav"},
            )
        finally:
            gateway.transaction = None

    result = asyncio.run(go())
    assert "joinery.lock: audio/sfx/gem.wav" in str(result.content[-1])
    assert "audio/sfx/gem.wav" not in _locked(stage)
