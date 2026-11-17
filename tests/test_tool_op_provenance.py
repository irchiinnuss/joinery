"""Tool operations keep a model's files, recipe and joinery.lock entry in step.

Replays the sequence that left a garden chair stuck: a Blender model gets a
painted map (its texture file is a recipe output), then an edit rebuilds the
model without that image. The edit wrote the new .blend and .glb and removed
the old texture before its recipe was refused, so joinery.lock named a texture
that was gone and hashes that no longer matched, and every later operation on
the model, and on any other model, was refused. The application is replaced at
its boundary; recipes and the lock are the real ones.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from joinery import authoring
from joinery.catalog import load_catalog
from joinery.lockfile import LOCK_NAME, check_assets
from joinery.recipe import load_recipe, recipe_paths
from joinery.runner import Result

SOURCE = "art/sources/garden_chair.blend"
MODEL = "art/props/garden_chair.glb"
TEXTURES = "art/textures/garden_chair"
MAP = f"{TEXTURES}/Chair_Plastic_base_color.png"


@pytest.fixture
def stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "transaction" / "stage"
    project.mkdir(parents=True)
    (project.parent / "journal.json").write_text(json.dumps({"state": "staging"}))
    (project / "joinery.toml").write_text('[tools]\nenabled=["blender","krita"]\n')
    # Fingerprints are measured by the real application; not here.
    monkeypatch.setattr(authoring, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(authoring, "_fingerprint_runner", lambda: None)
    return project


def _blender(
    stage: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes]
) -> None:
    """The next Blender run saves and exports exactly `files`."""

    async def run(_argv: list[str], project: Path, *_a: object, **_k: object) -> Result:
        for rel, data in files.items():
            (project / rel).parent.mkdir(parents=True, exist_ok=True)
            (project / rel).write_bytes(data)
        return Result(True, "saved", "", 0.01)

    monkeypatch.setattr(authoring, "run_offline", run)


def _edit(stage: Path, source: str = SOURCE, model: str = MODEL) -> dict[str, object]:
    return asyncio.run(
        authoring.edit_document(stage, "blender", source, "pass", [model])
    )


def _consistent(stage: Path) -> None:
    assert check_assets(stage, load_catalog()) == []


def _current(stage: Path, output: str) -> list[str]:
    return [
        p.relative_to(stage).as_posix()
        for p in recipe_paths(stage)
        if output in load_recipe(p).outputs
    ]


def _made_with_a_map(stage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _blender(stage, monkeypatch, {SOURCE: b"chair", MODEL: b"glb"})
    _edit(stage)
    _consistent(stage)
    # add_painted_map's Blender step: a new image, written as its own texture.
    _blender(
        stage,
        monkeypatch,
        {SOURCE: b"chair+map", MODEL: b"glb+map", MAP: b"flat colour"},
    )
    _edit(stage)
    _consistent(stage)
    assert (stage / MAP).is_file()


def test_an_edit_that_drops_a_map_replaces_its_recipe_and_lock_entry(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _made_with_a_map(stage, monkeypatch)
    before = _current(stage, MAP)
    # The model rebuilt flat: Blender writes no texture for the removed image.
    _blender(stage, monkeypatch, {SOURCE: b"chair flat", MODEL: b"glb flat"})
    result = _edit(stage)
    assert result["outputs"] == [SOURCE, MODEL]
    assert not (stage / MAP).exists()
    _consistent(stage)
    assert MAP not in (stage / LOCK_NAME).read_text()
    assert _current(stage, MAP) == []
    assert (stage / "recipes/history" / Path(before[0]).name).is_file()
    # And the model takes further operations: a new map under a new name.
    renamed = f"{TEXTURES}/garden_chair_plastic_albedo.png"
    _blender(
        stage,
        monkeypatch,
        {SOURCE: b"chair+albedo", MODEL: b"glb+albedo", renamed: b"albedo"},
    )
    _edit(stage)
    _consistent(stage)


def _snapshot(stage: Path) -> dict[str, bytes]:
    """Every file of the stage but the preimages, which are kept by content."""
    return {
        p.relative_to(stage).as_posix(): p.read_bytes()
        for p in sorted(stage.rglob("*"))
        if p.is_file() and not p.is_relative_to(stage / "recipes/inputs")
    }


def _broken_elsewhere(stage: Path) -> Callable[[], None]:
    """Another recipe claims a file that is gone, so the lock can't be written."""
    recipe = stage / "recipes/lost.toml"
    recipe.write_text(
        'version = 1\ntool = "krita"\nscript = "recipes/lost.py"\n'
        'outputs = ["art/sources/lost.kra", "art/textures/lost.png"]\n'
    )
    return lambda: recipe.unlink()


def test_an_edit_whose_lock_cannot_be_written_is_undone(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _made_with_a_map(stage, monkeypatch)
    mend = _broken_elsewhere(stage)
    before = _snapshot(stage)
    _blender(stage, monkeypatch, {SOURCE: b"chair flat", MODEL: b"glb flat"})
    with pytest.raises(ValueError, match="art/textures/lost.png"):
        _edit(stage)
    assert _snapshot(stage) == before
    mend()
    _consistent(stage)


def test_a_new_model_refused_by_the_lock_leaves_nothing_behind(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The speaker after the chair: its files were saved, the lock refused, and
    # they stayed with no lock entry.
    _made_with_a_map(stage, monkeypatch)
    _broken_elsewhere(stage)
    before = _snapshot(stage)
    speaker = "art/sources/party_speaker.blend"
    files = {
        speaker: b"speaker",
        "art/props/party_speaker.glb": b"glb",
        "art/textures/party_speaker/Cabinet.png": b"blue",
    }
    _blender(stage, monkeypatch, files)
    with pytest.raises(ValueError, match="Provenance recording failed"):
        _edit(stage, speaker, "art/props/party_speaker.glb")
    assert _snapshot(stage) == before
    assert not any((stage / rel).exists() for rel in files)


def test_a_failed_application_run_puts_every_file_back(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _made_with_a_map(stage, monkeypatch)
    before = _snapshot(stage)

    async def run(_argv: list[str], project: Path, *_a: object, **_k: object) -> Result:
        # Saved the .blend, then the export failed.
        (project / SOURCE).write_bytes(b"half")
        return Result(False, "", "export failed", 0.01)

    monkeypatch.setattr(authoring, "run_offline", run)
    with pytest.raises(RuntimeError, match="export failed"):
        _edit(stage)
    assert _snapshot(stage) == before
    _consistent(stage)
