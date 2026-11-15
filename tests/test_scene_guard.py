"""A saved-scene tool that drops scripts it could not load is caught and undone."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from joinery import scene_guard
from joinery.gateway import ProjectGateway
from joinery.transactions import FileTransaction

LEVEL = """[gd_scene format=3 uid="uid://bkh5uxdqd6xvu"]

[ext_resource type="Script" uid="uid://cfw9ew85w3nl2" path="res://game/level.gd" id="1_level"]
[ext_resource type="PackedScene" uid="uid://cs1ux7fpb43iw" path="res://game/gem.tscn" id="2_gem"]
[ext_resource type="Script" uid="uid://b1ypq5tnr6u5s" path="res://game/ui/hud.gd" id="3_hud"]

[node name="Level" type="Node2D"]
script = ExtResource("1_level")

[node name="Gem" parent="." instance=ExtResource("2_gem")]

[node name="HUD" type="CanvasLayer" parent="."]
script = ExtResource("3_hud")
"""

#: What the headless Godot saved back: level.gd and hud.gd did not compile there.
SAVED = """[gd_scene format=3]

[ext_resource type="PackedScene" path="res://game/gem.tscn" id="2_gem"]

[node name="Level" type="Node2D" unique_id=1]

[node name="Gem" parent="." unique_id=2 instance=ExtResource("2_gem")]

[node name="HUD" type="CanvasLayer" parent="." unique_id=3]

[node name="Shelf" type="Node2D" parent="." unique_id=4]
"""

#: A clean save: the new node added, every link kept.
CLEAN = LEVEL + '\n[node name="Shelf" type="Node2D" parent="."]\n'


def test_a_node_that_lost_its_script_is_named() -> None:
    lost = scene_guard.dropped(LEVEL, SAVED)
    assert lost == [
        ". (script res://game/level.gd)",
        "HUD (script res://game/ui/hud.gd)",
    ]
    text = scene_guard.message("game/level.tscn", lost)
    assert "res://game/level.tscn" in text and "dropped them" in text
    assert "write_file (patch)" in text


def test_a_lost_instance_is_named_and_a_removed_node_is_not() -> None:
    without_gem = LEVEL.replace(' instance=ExtResource("2_gem")', "")
    assert scene_guard.dropped(LEVEL, without_gem) == [
        "Gem (instance of res://game/gem.tscn)"
    ]
    removed = LEVEL.split('[node name="HUD"')[0]
    assert scene_guard.dropped(LEVEL, removed) == []


def test_a_clean_save_passes() -> None:
    assert scene_guard.dropped(LEVEL, CLEAN) == []


class _SavesScene:
    """The Godot MCP's add_node: loads the staged scene and saves it back."""

    def __init__(self, scene: Path, saved: str) -> None:
        self.scene, self.saved = scene, saved
        self.tools: dict[str, Any] = {"add_node": object()}

    async def call(
        self, _tool: str, _arguments: dict[str, Any], **_k: Any
    ) -> CallToolResult:
        self.scene.write_text(self.saved)
        return CallToolResult(content=[TextContent(type="text", text="Node added")])

    async def close(self) -> None:
        return None


def _add_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saved: str
) -> tuple[CallToolResult, str]:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))
    gateway = ProjectGateway(tmp_path)
    tx = FileTransaction(tmp_path)
    tx.begin("Add a shelf")
    gateway.transaction = tx
    scene = tx.stage / "game/level.tscn"
    scene.parent.mkdir(parents=True)
    scene.write_text(LEVEL)
    fake = _SavesScene(scene, saved)
    gateway.connection = AsyncMock(return_value=fake)  # type: ignore[method-assign]
    gateway.staged["godot"] = fake  # type: ignore[assignment]

    async def go() -> tuple[CallToolResult, str]:
        try:
            result = await gateway.call(
                "godot",
                "add_node",
                {
                    "projectPath": str(tmp_path),
                    "scenePath": "game/level.tscn",
                    "nodeType": "Node2D",
                    "nodeName": "Shelf",
                },
            )
            return result, scene.read_text()
        finally:
            await gateway.close()

    return asyncio.run(go())


def test_the_gateway_puts_back_a_scene_whose_scripts_were_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, text = _add_node(tmp_path, monkeypatch, SAVED)
    assert result.is_error
    assert "HUD (script res://game/ui/hud.gd)" in str(result.content[0])
    assert text == LEVEL


def test_the_gateway_keeps_a_clean_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, text = _add_node(tmp_path, monkeypatch, CLEAN)
    assert not result.is_error
    assert text == CLEAN
