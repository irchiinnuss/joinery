from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

from joinery import gltf_signature
from joinery.lockfile import ToolCreated, entry_matches
from joinery.recipe import (
    fingerprint,
    load_recipe,
    migrate_model_expectations,
    rebuild_one,
)
from tests.test_recipe import FakeRunner, put, write_recipe


def _png(pixels: bytes, level: int = 6, text: bytes = b"") -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body))
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    extra = chunk(b"tEXt", text) if text else b""
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + extra
        + chunk(b"IDAT", zlib.compress(b"\x00" + pixels, level))
        + chunk(b"IEND", b"")
    )


def glb(
    *,
    generator: str = "Khronos glTF Blender I/O v5.2.0",
    noise: float = 0.0,
    lift: float = 0.0,
    pad: int = 0,
    colour: float = 0.8,
    pixels: bytes = b"\x10\x20\x30",
    png_level: int = 6,
    keyframe: float = 1.0,
) -> bytes:
    """A one-triangle model with a texture, a node and an animation."""
    positions = struct.pack("<9f", 0, 0 + lift, 0, 1 + noise, 0, 0, 0, 1, 0)
    indices = struct.pack("<3H", 0, 1, 2) + b"\0\0"
    times = struct.pack("<2f", 0.0, keyframe)
    moves = struct.pack("<6f", 0, 0, 0, 0, 1, 0)
    image = _png(pixels, png_level, b"Software\0Blender" if png_level != 6 else b"")
    image += b"\0" * (-len(image) % 4)
    blobs = [b"\0" * pad, positions, indices, times, moves, image]
    offsets = []
    data = b""
    for blob in blobs:
        offsets.append(len(data))
        data += blob
    doc = {
        "asset": {"version": "2.0", "generator": generator},
        "scene": 0,
        "scenes": [{"name": "Scene", "nodes": [0]}],
        "nodes": [{"name": "Gun", "mesh": 0, "translation": [0, 0.1 + noise, 0]}],
        "meshes": [
            {
                "name": "Gun",
                "primitives": [
                    {"attributes": {"POSITION": 0}, "indices": 1, "material": 0}
                ],
            }
        ],
        "materials": [
            {
                "name": "Steel",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [colour, colour, colour, 1.0],
                    "baseColorTexture": {"index": 0},
                    "metallicFactor": 0.0,
                },
            }
        ],
        "textures": [{"source": 0}],
        "images": [{"bufferView": 5, "mimeType": "image/png"}],
        "accessors": [
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": 3, "type": "SCALAR"},
            {"bufferView": 3, "componentType": 5126, "count": 2, "type": "SCALAR"},
            {"bufferView": 4, "componentType": 5126, "count": 2, "type": "VEC3"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": offsets[i], "byteLength": len(blobs[i])}
            for i in range(len(blobs))
        ],
        "buffers": [{"byteLength": len(data)}],
        "animations": [
            {
                "name": "Kick",
                "samplers": [{"input": 2, "output": 3}],
                "channels": [
                    {"sampler": 0, "target": {"node": 0, "path": "translation"}}
                ],
            }
        ],
    }
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    body = (
        struct.pack("<II", len(text), 0x4E4F534A)
        + text
        + struct.pack("<II", len(data), 0x004E4942)
        + data
    )
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def sign(tmp_path: Path, data: bytes, name: str = "m.glb") -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return gltf_signature.signature(path)


def test_same_model_with_byte_noise_has_one_signature(tmp_path: Path) -> None:
    base = sign(tmp_path, glb())
    assert base.startswith("glb:")
    # Exporter version, float noise, buffer layout, PNG encoding: same model.
    assert sign(tmp_path, glb(generator="Khronos glTF Blender I/O v5.3.1")) == base
    assert sign(tmp_path, glb(noise=1e-7)) == base
    assert sign(tmp_path, glb(pad=16)) == base
    assert sign(tmp_path, glb(png_level=9)) == base


def test_a_changed_model_changes_the_signature(tmp_path: Path) -> None:
    base = sign(tmp_path, glb())
    assert sign(tmp_path, glb(lift=0.01)) != base  # a moved vertex
    assert sign(tmp_path, glb(colour=0.2)) != base  # a material
    assert sign(tmp_path, glb(pixels=b"\x10\x20\x31")) != base  # a texture
    assert sign(tmp_path, glb(keyframe=2.0)) != base  # an animation


def test_recipes_fingerprint_models_by_content(tmp_path: Path) -> None:
    put(tmp_path, "art/m.glb", glb())
    got = fingerprint("art/m.glb", tmp_path, FakeRunner())
    assert got == gltf_signature.signature(tmp_path / "art/m.glb")


def _model_recipe(tmp_path: Path, expect: str = "") -> Path:
    (tmp_path / "recipes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "recipes" / "m.py").write_text("# bpy ops")
    body = (
        'version = 1\ntool = "blender"\nscript = "recipes/m.py"\n'
        'outputs = ["art/m.glb"]\n'
    )
    if expect:
        body += f'[expect]\n"art/m.glb" = "{expect}"\n'
    return write_recipe(tmp_path, "m", body)


def test_rebuild_passes_when_the_export_bytes_differ(tmp_path: Path) -> None:
    put(tmp_path, "art/m.glb", glb())
    path = _model_recipe(tmp_path, gltf_signature.signature(tmp_path / "art/m.glb"))
    runner = FakeRunner(writes={"art/m.glb": glb(noise=1e-7, pad=8)})
    assert rebuild_one(path, tmp_path, runner).ok
    runner.writes["art/m.glb"] = glb(lift=0.5)
    assert not rebuild_one(path, tmp_path, runner).ok


def test_a_recipe_recorded_by_bytes_is_read_by_content(tmp_path: Path) -> None:
    put(tmp_path, "art/m.glb", glb())
    legacy = "sha256:" + hashlib.sha256(glb()).hexdigest()
    path = _model_recipe(tmp_path, legacy)
    runner = FakeRunner(writes={"art/m.glb": glb(pad=4)})
    assert rebuild_one(path, tmp_path, runner).ok, "old recipes still rebuild"

    assert migrate_model_expectations(tmp_path) == ["recipes/m.toml"]
    assert load_recipe(path).expect["art/m.glb"].startswith("glb:")
    assert migrate_model_expectations(tmp_path) == []


def test_a_legacy_expectation_for_a_changed_file_is_left_alone(tmp_path: Path) -> None:
    put(tmp_path, "art/m.glb", glb(lift=1.0))
    legacy = "sha256:" + hashlib.sha256(glb()).hexdigest()
    path = _model_recipe(tmp_path, legacy)
    assert migrate_model_expectations(tmp_path) == []
    assert load_recipe(path).expect["art/m.glb"] == legacy


def test_lock_entry_matches_a_re_exported_model(tmp_path: Path) -> None:
    model = tmp_path / "m.glb"
    model.write_bytes(glb())
    entry = ToolCreated(
        path="m.glb",
        sha256=hashlib.sha256(glb()).hexdigest(),
        signature=gltf_signature.signature(model),
        origin="tool-created",
        tool="blender",
        recipe="recipes/m.toml",
    )
    model.write_bytes(glb(pad=12))
    assert entry_matches(model, entry)
    model.write_bytes(glb(lift=2.0))
    assert not entry_matches(model, entry)
    # Older entries without a signature still compare bytes.
    legacy = entry.model_copy(update={"signature": ""})
    model.write_bytes(glb(pad=12))
    assert not entry_matches(model, legacy)


def test_the_lock_records_a_model_signature(tmp_path: Path) -> None:
    from joinery.locking import record_lock

    put(tmp_path, "art/m.glb", glb())
    _model_recipe(tmp_path)
    text, problems = record_lock(tmp_path)
    assert not problems
    assert f'signature = "{gltf_signature.signature(tmp_path / "art/m.glb")}"' in text
