from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from joinery import asset_preview
from joinery.read_access import read_only
from joinery.runner import Result

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01"
    b"\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "art/textures").mkdir(parents=True)
    (tmp_path / "art/textures/brick.png").write_bytes(PNG)
    (tmp_path / "art/models").mkdir(parents=True)
    (tmp_path / "art/models/gun.glb").write_bytes(b"glTF-model")
    return tmp_path


class FakeBlender:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path, str]] = []

    async def __call__(
        self, argv: list[str], folder: Path, timeout: int = 600, **_: Any
    ) -> Result:
        script = (folder / argv[-1]).read_text()
        self.calls.append((argv, folder, script))
        out = next(
            line.split("=", 1)[1].strip().strip("'")
            for line in script.splitlines()
            if line.startswith("OUT = ")
        )
        (folder / out).write_bytes(PNG)
        row = {"size": [384, 384]}
        return Result(True, "JOINERY_PREVIEW:" + json.dumps(row), "", 0.5)


def test_a_texture_and_a_model_are_drawn_once_and_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    blender = FakeBlender()
    monkeypatch.setattr(asset_preview, "run_offline", blender)
    first = asyncio.run(asset_preview.preview(project, "res://art/textures/brick.png"))
    assert first["kind"] == "image" and not first["cached"]
    assert Path(first["image"]).read_bytes() == PNG
    again = asyncio.run(asset_preview.preview(project, "art/textures/brick.png"))
    assert again["cached"] and len(blender.calls) == 1
    model = asyncio.run(asset_preview.preview(project, "art/models/gun.glb", 256))
    assert model["kind"] == "model"
    argv, folder, script = blender.calls[-1]
    assert argv[:2] == ["blender", "--background"]
    assert folder == project / asset_preview.PREVIEW_DIR
    assert "KIND = 'model'" in script and "SIZE = 256" in script
    # Blender worked on a copy that is gone again; the project is untouched.
    assert not list(folder.glob("*-source.*"))
    assert (project / "art/models/gun.glb").read_bytes() == b"glTF-model"


@pytest.mark.parametrize(
    ("path", "size", "reason"),
    [
        ("../outside.png", 384, "outside this project"),
        ("art/missing.png", 384, "not a file"),
        ("audio/boom.wav", 384, "Preview a texture"),
        ("art/textures/brick.png", 4096, "64 to 1024"),
    ],
)
def test_previews_stay_in_the_project(
    tmp_path: Path, path: str, size: int, reason: str
) -> None:
    project = _project(tmp_path)
    with pytest.raises(ValueError, match=reason):
        asyncio.run(asset_preview.preview(project, path, size))


def test_the_registry_tool_returns_the_picture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp import Client

    from servers.joinery_registry.server import make_server

    project = _project(tmp_path)
    monkeypatch.setattr(asset_preview, "run_offline", FakeBlender())

    async def run() -> Any:
        async with Client(make_server(project)) as client:
            return await client.call_tool(
                "preview_asset", {"path": "art/textures/brick.png"}
            )

    result = asyncio.run(run())
    assert not result.is_error, result.content[0].text
    assert result.content[1].type == "image"
    assert result.content[1].mime_type == "image/png"
    assert read_only("joinery-registry", "preview_asset")
