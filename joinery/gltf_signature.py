"""A semantic signature for glTF models (.glb and .gltf).

Blender's glTF exporter does not write byte-identical files from one run to the
next: the generator string changes with the add-on version, float values pick
up last-bit noise from the evaluated mesh, and buffers can be laid out in a
different order. Hashing the bytes then fails every rebuild while the model is
exactly the same. So, like the ``blend:`` signature, a model is fingerprinted
by what it contains:

* meshes: every primitive's attributes and indices, decoded from the buffers
  and rounded, plus its material and draw mode;
* materials: their PBR factors and flags, and the pixels of each texture;
* nodes: names, transforms, the mesh, skin and children they refer to;
* skins and animations: joints, bind matrices, channels and keyframes.

Everything refers to other things by name or content, never by index or byte
offset, so a reordered buffer or a renumbered accessor does not change the
result, while a moved vertex, a changed colour or a new bone does.

This module uses the standard library only: Blender's own Python runs it too,
when a batch of edits measures an export that the next step will overwrite.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import zlib
from pathlib import Path
from typing import Any

PREFIX = "glb:"
#: Decimal places kept for float data. The exporter's noise sits in the last
#: bits of a 32-bit float (about 7 significant digits); 5 places is a tenth of
#: a millimetre in model units, far below anything a person would see change.
PLACES = 5
_COMPONENTS = {
    5120: ("b", 1),
    5121: ("B", 1),
    5122: ("h", 2),
    5123: ("H", 2),
    5125: ("I", 4),
    5126: ("f", 4),
}
_WIDTH = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}
_NORMALISE = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}
#: Keys that describe the file rather than the model.
_FILE_KEYS = {"generator", "copyright", "extras", "minVersion"}


def _round(value: float) -> float:
    rounded = round(value, PLACES)
    return 0.0 if rounded == 0 else rounded  # -0.0 and 0.0 are the same


def _clean(value: Any) -> Any:
    """Floats rounded, recursively, so factors written with noise compare equal."""
    if isinstance(value, float):
        return _round(value)
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    return value


def read(path: Path) -> tuple[dict[str, Any], list[bytes]]:
    """The glTF JSON document and its buffers' bytes."""
    data = path.read_bytes()
    if data[:4] == b"glTF":
        _magic, _version, length = struct.unpack_from("<4sII", data, 0)
        offset = 12
        document: dict[str, Any] | None = None
        binary = b""
        while offset + 8 <= min(length, len(data)):
            size, kind = struct.unpack_from("<II", data, offset)
            chunk = data[offset + 8 : offset + 8 + size]
            if kind == 0x4E4F534A:  # JSON
                document = json.loads(chunk.decode("utf-8"))
            elif kind == 0x004E4942 and not binary:  # BIN
                binary = chunk
            offset += 8 + size
        if document is None:
            raise ValueError(f"{path.name}: a GLB without a JSON chunk")
    else:
        document = json.loads(data.decode("utf-8"))
        binary = b""
    buffers: list[bytes] = []
    for index, buffer in enumerate(document.get("buffers", [])):
        uri = buffer.get("uri")
        if uri is None:
            buffers.append(binary if index == 0 else b"")
        elif uri.startswith("data:"):
            buffers.append(base64.b64decode(uri.partition(",")[2]))
        else:
            target = (path.parent / uri).resolve()
            if not target.is_relative_to(path.parent.resolve()):
                raise ValueError(f"{path.name}: buffer {uri} is outside its folder")
            buffers.append(target.read_bytes())
    return document, buffers


class _Model:
    def __init__(self, document: dict[str, Any], buffers: list[bytes]) -> None:
        self.doc = document
        self.buffers = buffers

    def _list(self, key: str) -> list[dict[str, Any]]:
        value = self.doc.get(key, [])
        return value if isinstance(value, list) else []

    def _view(self, index: int) -> tuple[bytes, int, int]:
        view = self._list("bufferViews")[index]
        data = self.buffers[int(view.get("buffer", 0))]
        start = int(view.get("byteOffset", 0))
        return data, start, int(view.get("byteStride", 0))

    def accessor(self, index: int | None) -> list[Any]:
        """An accessor's elements as flat Python numbers (floats rounded)."""
        if index is None:
            return []
        acc = self._list("accessors")[index]
        kind = int(acc["componentType"])
        code, size = _COMPONENTS[kind]
        width = _WIDTH[str(acc["type"])]
        count = int(acc["count"])
        values: list[float | int] = [0] * (count * width)
        if "bufferView" in acc:
            data, start, stride = self._view(int(acc["bufferView"]))
            start += int(acc.get("byteOffset", 0))
            stride = stride or size * width
            element = struct.Struct("<" + code * width)
            for item in range(count):
                values[item * width : (item + 1) * width] = element.unpack_from(
                    data, start + item * stride
                )
        sparse = acc.get("sparse")
        if sparse:
            hits = int(sparse["count"])
            ind = sparse["indices"]
            icode, isize = _COMPONENTS[int(ind["componentType"])]
            idata, istart, _ = self._view(int(ind["bufferView"]))
            istart += int(ind.get("byteOffset", 0))
            val = sparse["values"]
            vdata, vstart, _ = self._view(int(val["bufferView"]))
            vstart += int(val.get("byteOffset", 0))
            element = struct.Struct("<" + code * width)
            for hit in range(hits):
                (target,) = struct.unpack_from("<" + icode, idata, istart + hit * isize)
                values[target * width : (target + 1) * width] = element.unpack_from(
                    vdata, vstart + hit * size * width
                )
        if kind == 5126:
            return [_round(float(v)) for v in values]
        if acc.get("normalized") and kind in _NORMALISE:
            scale = _NORMALISE[kind]
            return [_round(max(float(v) / scale, -1.0)) for v in values]
        return list(values)

    def digest(self, index: int | None) -> str:
        return hashlib.sha256(
            json.dumps(self.accessor(index), separators=(",", ":")).encode()
        ).hexdigest()

    def name(self, key: str, index: Any) -> str | None:
        if not isinstance(index, int):
            return None
        items = self._list(key)
        if not 0 <= index < len(items):
            return None
        return str(items[index].get("name", f"#{index}"))

    def image(self, index: int) -> str:
        """An image's pixels' hash: PNG chunks that only describe the file are ignored."""
        image = self._list("images")[index]
        if "bufferView" in image:
            view = self._list("bufferViews")[int(image["bufferView"])]
            data = self.buffers[int(view.get("buffer", 0))]
            start = int(view.get("byteOffset", 0))
            raw = data[start : start + int(view["byteLength"])]
        elif str(image.get("uri", "")).startswith("data:"):
            raw = base64.b64decode(str(image["uri"]).partition(",")[2])
        else:
            return "uri:" + str(image.get("uri", ""))
        return _png_pixels(raw)

    def texture(self, info: Any) -> Any:
        if not isinstance(info, dict) or "index" not in info:
            return info
        texture = self._list("textures")[int(info["index"])]
        source = texture.get("source")
        sampler = texture.get("sampler")
        out = {k: v for k, v in info.items() if k not in {"index", "extensions"}}
        out["image"] = self.image(int(source)) if isinstance(source, int) else None
        out["sampler"] = (
            {k: v for k, v in self._list("samplers")[sampler].items() if k != "name"}
            if isinstance(sampler, int)
            else None
        )
        return _clean(out)

    def material(self, index: int) -> dict[str, Any]:
        raw = self._list("materials")[index]
        out: dict[str, Any] = {}
        for key, value in raw.items():
            if key in {"extras"}:
                continue
            out[key] = self._textures(value)
        return out

    def _textures(self, value: Any) -> Any:
        if isinstance(value, dict):
            if "index" in value and isinstance(value["index"], int):
                return self.texture(value)
            return {k: self._textures(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._textures(v) for v in value]
        return _clean(value)

    def mesh(self, index: int) -> dict[str, Any]:
        mesh = self._list("meshes")[index]
        primitives = []
        for prim in mesh.get("primitives", []):
            primitives.append(
                {
                    "attributes": {
                        key: self.digest(int(acc))
                        for key, acc in sorted(prim.get("attributes", {}).items())
                    },
                    "indices": self.digest(prim.get("indices")),
                    "material": (
                        self.material(int(prim["material"]))
                        if isinstance(prim.get("material"), int)
                        else None
                    ),
                    "mode": prim.get("mode", 4),
                    "targets": [
                        {k: self.digest(int(a)) for k, a in sorted(t.items())}
                        for t in prim.get("targets", [])
                    ],
                }
            )
        return {
            "name": mesh.get("name"),
            "primitives": primitives,
            "weights": _clean(mesh.get("weights", [])),
        }

    def node(self, index: int) -> dict[str, Any]:
        node = self._list("nodes")[index]
        return {
            "name": node.get("name", f"#{index}"),
            "mesh": self.mesh(node["mesh"])
            if isinstance(node.get("mesh"), int)
            else None,
            "skin": self.skin(node["skin"])
            if isinstance(node.get("skin"), int)
            else None,
            "camera": node.get("camera") is not None,
            "children": [self.name("nodes", c) for c in node.get("children", [])],
            **{
                key: _clean(node[key])
                for key in ("translation", "rotation", "scale", "matrix", "weights")
                if key in node
            },
            "extras": _clean(node.get("extras")),
        }

    def skin(self, index: int) -> dict[str, Any]:
        skin = self._list("skins")[index]
        return {
            "name": skin.get("name"),
            "joints": [self.name("nodes", j) for j in skin.get("joints", [])],
            "skeleton": self.name("nodes", skin.get("skeleton")),
            "bind": self.digest(skin.get("inverseBindMatrices")),
        }

    def animation(self, anim: dict[str, Any]) -> dict[str, Any]:
        samplers = anim.get("samplers", [])
        channels = []
        for channel in anim.get("channels", []):
            target = channel.get("target", {})
            sampler = samplers[int(channel["sampler"])]
            channels.append(
                {
                    "node": self.name("nodes", target.get("node")),
                    "path": target.get("path"),
                    "interpolation": sampler.get("interpolation", "LINEAR"),
                    "input": self.digest(sampler.get("input")),
                    "output": self.digest(sampler.get("output")),
                }
            )
        channels.sort(key=lambda c: (str(c["node"]), str(c["path"])))
        return {"name": anim.get("name"), "channels": channels}

    def summary(self) -> dict[str, Any]:
        asset = self.doc.get("asset", {})
        scenes = [
            {
                "name": scene.get("name"),
                "nodes": [self.name("nodes", n) for n in scene.get("nodes", [])],
            }
            for scene in self._list("scenes")
        ]
        nodes = sorted(
            (self.node(i) for i in range(len(self._list("nodes")))),
            key=lambda n: json.dumps(n, sort_keys=True),
        )
        # Meshes, materials and images no node uses still count: they are in the file.
        meshes = sorted(
            json.dumps(self.mesh(i), sort_keys=True)
            for i in range(len(self._list("meshes")))
        )
        materials = sorted(
            json.dumps(self.material(i), sort_keys=True)
            for i in range(len(self._list("materials")))
        )
        animations = sorted(
            (self.animation(a) for a in self._list("animations")),
            key=lambda a: json.dumps(a, sort_keys=True),
        )
        return {
            "asset": {k: v for k, v in asset.items() if k not in _FILE_KEYS},
            "scene": self.name("scenes", self.doc.get("scene")),
            "scenes": scenes,
            "nodes": nodes,
            "meshes": meshes,
            "materials": materials,
            "animations": animations,
            "extensionsUsed": sorted(self.doc.get("extensionsUsed", [])),
            "extensionsRequired": sorted(self.doc.get("extensionsRequired", [])),
        }


def _png_pixels(raw: bytes) -> str:
    """Hash a PNG by its header and decompressed image data, else by its bytes.

    Encoders differ in zlib level and in chunks such as tIME or tEXt; the
    pixels do not. Non-PNG images are hashed as they are.
    """
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        return "raw:" + hashlib.sha256(raw).hexdigest()
    offset = 8
    header = b""
    palette = b""
    compressed = bytearray()
    while offset + 8 <= len(raw):
        (size,) = struct.unpack_from(">I", raw, offset)
        kind = raw[offset + 4 : offset + 8]
        body = raw[offset + 8 : offset + 8 + size]
        if kind == b"IHDR":
            header = body
        elif kind in {b"PLTE", b"tRNS"}:
            palette += kind + body
        elif kind == b"IDAT":
            compressed += body
        elif kind == b"IEND":
            break
        offset += 12 + size
    try:
        pixels = zlib.decompress(bytes(compressed))
    except zlib.error:
        return "raw:" + hashlib.sha256(raw).hexdigest()
    return "png:" + hashlib.sha256(header + palette + pixels).hexdigest()


def summary(path: Path) -> dict[str, Any]:
    """What the signature hashes, readable: useful when two models differ."""
    document, buffers = read(path)
    return _Model(document, buffers).summary()


def signature(path: Path) -> str:
    """The ``glb:`` fingerprint of a .glb or .gltf file."""
    blob = json.dumps(summary(path), sort_keys=True, separators=(",", ":"))
    return PREFIX + hashlib.sha256(blob.encode()).hexdigest()
