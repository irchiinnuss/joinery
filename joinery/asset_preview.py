"""A small picture of a texture or model, so Journeyman can look at what it made.

An agent can't open a staged PNG or see a model at all, so it painted and
modelled blind ("I couldn't open the finished images from here"). This draws
one, read-only:

* an image is scaled to fit ``size`` px; a small one (pixel art) is enlarged by
  a whole number with nearest-neighbour, so its pixels stay crisp;
* a model (.glb, .gltf or .blend) is rendered in Blender's Workbench with its
  textures and a studio light, framed on its bounds from the front three-quarter.

Blender runs in the offline worker on a copy in ``.joinery/previews``, never on
the project's files. The picture is not an asset and is cached by content.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from joinery.authoring import run_offline
from joinery.tools.blender import FAIL_ON_ERROR

PREVIEW_DIR = ".joinery/previews"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tga", ".exr"})
MODEL_SUFFIXES = frozenset({".glb", ".gltf", ".blend"})
SIZES = (64, 1024)
DEFAULT_SIZE = 384
KEEP = 64

_SCRIPT = r"""
import bpy, os, json, math
import numpy as np
from mathutils import Vector

def _image():
    image = bpy.data.images.load(os.path.abspath(SRC))
    width, height = image.size
    if width == 0 or height == 0:
        raise RuntimeError(SRC + ' has no pixels.')
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    if max(width, height) * 2 <= SIZE:
        factor = SIZE // max(width, height)
        grid = pixels.reshape(height, width, 4).repeat(factor, 0).repeat(factor, 1)
        out = bpy.data.images.new('joinery_preview', width * factor, height * factor, alpha=True)
        out.colorspace_settings.name = image.colorspace_settings.name
        out.pixels.foreach_set(grid.ravel())
    else:
        scale = min(1.0, SIZE / max(width, height))
        out = image
        out.scale(max(1, round(width * scale)), max(1, round(height * scale)))
    out.filepath_raw = os.path.abspath(OUT)
    out.file_format = 'PNG'
    out.save()
    return {'source_size': [width, height], 'size': list(out.size)}

def _model():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if SRC.lower().endswith('.blend'):
        bpy.ops.wm.open_mainfile(filepath=os.path.abspath(SRC), load_ui=False)
    else:
        bpy.ops.import_scene.gltf(filepath=os.path.abspath(SRC))
    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == 'MESH' and o.visible_get()]
    if not meshes:
        raise RuntimeError(SRC + ' has no visible mesh to draw.')
    corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    low = Vector([min(c[i] for c in corners) for i in range(3)])
    high = Vector([max(c[i] for c in corners) for i in range(3)])
    centre = (low + high) / 2
    radius = max(0.05, (high - low).length / 2)
    camera = bpy.data.objects.new('JoineryPreviewCamera', bpy.data.cameras.new('JoineryPreviewCamera'))
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.data.lens = 50
    camera.data.clip_end = max(100.0, radius * 20)
    # Blender is +Z up and a glTF's front (+Z in Godot) is -Y here.
    view = Vector((0.9, -1.35, 0.75)).normalized()
    camera.location = centre + view * (radius / math.sin(camera.data.angle / 2) * 1.08)
    camera.rotation_euler = (-view).to_track_quat('-Z', 'Y').to_euler()
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'TEXTURE'
    scene.display.shading.show_cavity = True
    scene.render.film_transparent = True
    scene.render.resolution_x = scene.render.resolution_y = SIZE
    scene.render.filepath = os.path.abspath(OUT)
    bpy.ops.render.render(write_still=True)
    triangles = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in meshes)
    return {'size': [SIZE, SIZE], 'bounds_m': [round(v, 3) for v in (high - low)], 'triangles': triangles, 'objects': len(meshes)}

print('JOINERY_PREVIEW:' + json.dumps(_image() if KIND == 'image' else _model()))
"""


def kind_of(rel: str) -> str:
    suffix = Path(rel).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in MODEL_SUFFIXES:
        return "model"
    raise ValueError(
        "Preview a texture (PNG, JPEG, WebP, TGA, EXR) or a model (.glb, .gltf, .blend)."
    )


def _confined(project: Path, rel: str) -> Path:
    relative = rel.removeprefix("res://")
    path = (project / relative).resolve()
    if not path.is_relative_to(project.resolve()):
        raise ValueError(f"{rel} is outside this project.")
    if not path.is_file():
        raise ValueError(f"{relative} is not a file in this project (or this change).")
    return path


async def preview(project: Path, rel: str, size: int = DEFAULT_SIZE) -> dict[str, Any]:
    """Draw a texture or model as a PNG of at most ``size`` px; returns its facts and path."""
    low, high = SIZES
    if not low <= size <= high:
        raise ValueError(f"size is {low} to {high} pixels.")
    kind = kind_of(rel)
    source = _confined(project, rel)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    digest.update(f"{kind}:{size}".encode())
    key = digest.hexdigest()[:24]
    folder = project / PREVIEW_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".gdignore").touch()
    image = folder / f"{key}.png"
    facts = folder / f"{key}.json"
    if image.is_file() and facts.is_file():
        return {**json.loads(facts.read_text()), "image": str(image), "cached": True}
    copy = folder / f"{key}-source{source.suffix.lower()}"
    shutil.copyfile(source, copy)
    work = folder / f"{key}.py"
    work.write_text(
        "\n".join(
            [
                "import os",
                FAIL_ON_ERROR,
                "os.chdir('/work')",
                f"SRC = {copy.name!r}",
                f"OUT = {image.name!r}",
                f"SIZE = {size}",
                f"KIND = {kind!r}",
                _SCRIPT,
            ]
        ),
        encoding="utf-8",
    )
    try:
        result = await run_offline(
            ["blender", "--background", "--factory-startup", "--python", work.name],
            folder,
            300,
        )
    finally:
        work.unlink(missing_ok=True)
        copy.unlink(missing_ok=True)
    row = next(
        (
            json.loads(line.removeprefix("JOINERY_PREVIEW:"))
            for line in result.output.splitlines()
            if line.startswith("JOINERY_PREVIEW:")
        ),
        None,
    )
    if not result.ok or row is None or not image.is_file():
        lines = [line for line in result.output.splitlines() if "Error" in line]
        why = (lines[-1] if lines else result.output[-400:]).strip()
        raise RuntimeError(f"Blender could not draw {rel}: {why}")
    data = {"path": rel.removeprefix("res://"), "kind": kind, **row}
    facts.write_text(json.dumps(data))
    _prune(folder)
    return {**data, "image": str(image), "cached": False}


def _prune(folder: Path) -> None:
    pictures = sorted(
        folder.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for old in pictures[KEEP:]:
        old.unlink(missing_ok=True)
        old.with_suffix(".json").unlink(missing_ok=True)
