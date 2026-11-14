"""Numbers in the user's setup answers become the bible's checked rules."""

from __future__ import annotations

import json
from pathlib import Path

from joinery.bible import ANSWERS_FILE, write_from_answers_file
from joinery.bible_presets import answer_texel_density, rules_dict
from joinery.bible_rules import (
    BibleRules,
    TexelRule,
    material_texel_problems,
    parse_rules,
    rules_text,
)
from tests.test_bible import FULL


def test_numbers_in_the_answers_become_checked_rules(tmp_path: Path) -> None:
    """The user's texture size and hex colours win over the preset's numbers."""
    answers = {
        **FULL,
        "look": "chunky pixel-art textures, 64x64 px, nearest filtering",
        "palette": "rust #8a3b12, soot #222, steel #6E7B85 and #8a3b12 again",
    }
    (tmp_path / ANSWERS_FILE).write_text(json.dumps(answers))
    written = write_from_answers_file(tmp_path, "stylised-pbr-3d")
    assert written is not None
    text = written.read_text()
    block = rules_text(text)
    assert block is not None
    parsed = parse_rules(block)
    assert parsed.palette.colours == ["#8a3b12", "#222222", "#6e7b85"]
    assert {k: v.px_per_m for k, v in parsed.models.texel_density.items()} == {
        "art": 64
    }
    # The preset's prose says the same number as the rules.
    assert "512 px/m" not in text
    assert "64 px/m (from your answers)" in text


def test_px_per_metre_answer_is_taken_as_written() -> None:
    assert answer_texel_density({"look": "PBR at 256 px/m"}) == 256
    assert answer_texel_density({"look": "1 px outlines, flat colours"}) is None
    assert answer_texel_density({"look": "32 px tiles"}) == 32


def test_no_numbers_keep_the_preset() -> None:
    rules = rules_dict("stylised-pbr-3d", FULL)
    assert rules["models"]["texel_density"]["art"]["px_per_m"] == 512
    assert rules["palette"]["colours"] == []


def _png_header(width: int, height: int) -> bytes:
    import struct

    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
        + b"\0\0\0\0"
    )


SCENE = """[gd_scene load_steps=3 format=3]

[ext_resource type="Texture2D" path="res://art/textures/brick.png" id="8_brick"]

[sub_resource type="StandardMaterial3D" id="Mat_brick"]
albedo_texture = ExtResource("8_brick")
texture_filter = 2
uv1_scale = Vector3({scale}, {scale}, {scale})
uv1_triplanar = true
uv1_world_triplanar = true

[node name="Root" type="Node3D"]
"""


def test_texel_rule_applies_to_world_mapped_textures(tmp_path: Path) -> None:
    (tmp_path / "art/textures").mkdir(parents=True)
    (tmp_path / "art/textures/brick.png").write_bytes(_png_header(64, 64))
    rules = BibleRules.model_validate(
        {"models": {"texel_density": {"art": TexelRule(px_per_m=64, tolerance=0.2)}}}
    )
    # 64 px repeated twice a metre is 32 px/m: half the look's density.
    found = material_texel_problems(
        "game/hall.tscn", SCENE.format(scale=0.5), tmp_path, rules
    )
    assert len(found) == 1 and "32 px/m" in found[0], found
    assert not material_texel_problems(
        "game/hall.tscn", SCENE.format(scale=1.0), tmp_path, rules
    )


def test_a_dark_metal_is_flagged_before_it_renders_black() -> None:
    from joinery.bible_presets import rules_dict
    from joinery.bible_rules import _material_problems

    rules = BibleRules.model_validate(rules_dict(None)).materials
    # The scattergun's iron: metallic 1 (glTF's default) on a dark rust colour.
    dark = _material_problems("art/gun.glb", "Iron_Metal", 0.85, 1.0, 0.09, rules)
    assert any("near black" in p for p in dark), dark
    assert not _material_problems("art/gun.glb", "Iron_Metal", 0.85, 1.0, 0.5, rules)
    assert not _material_problems("art/gun.glb", "Grip_Wood", 0.85, 0.0, 0.09, rules)
