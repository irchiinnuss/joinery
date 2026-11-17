"""A step of a to-do ends with the asset gate over what the change touched.

A model left out of step with joinery.lock in one step (or a half-made one with
no entry at all) used to be found only by precheck at the very end, after every
later step had tripped over it. Now todo_step 'done' is refused while the files
the change touched fail the gate, and 'skipped' is told what it left. Only the
changed asset files are hashed; Godot is not involved. Same for every agent.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from joinery import step_assets
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.locking import write_lock
from joinery.turn import TurnController
from tests.test_crew import project, wire

__all__ = ["project"]

MAP = "art/textures/garden_chair/Chair_Plastic_base_color.png"
CHAIR = {
    "art/sources/garden_chair.blend": b"chair",
    "art/props/garden_chair.glb": b"glb",
    MAP: b"flat",
}
SPEAKER = {
    "art/sources/party_speaker.blend": b"speaker",
    "art/props/party_speaker.glb": b"glb",
}


def _make(root: Path, files: dict[str, bytes], name: str) -> None:
    """``files`` as one Blender recipe's outputs, with joinery.lock written."""
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    (root / "recipes").mkdir(exist_ok=True)
    (root / f"recipes/{name}.py").write_text("pass\n")
    outputs = ", ".join(f'"{rel}"' for rel in files)
    (root / f"recipes/{name}.toml").write_text(
        f'version = 1\ntool = "blender"\nscript = "recipes/{name}.py"\n'
        f"outputs = [{outputs}]\n"
    )
    assert write_lock(root) == []


def _stage_of(project: Path, stage: Path) -> Path:
    """A stage as Joinery clones it: same bytes, same times."""
    shutil.copytree(project, stage, copy_function=shutil.copy2)
    return stage


def _chair_out_of_step(stage: Path) -> None:
    # An edit rebuilt the chair without its map after the lock was written.
    (stage / "art/sources/garden_chair.blend").write_bytes(b"chair flat")
    (stage / "art/props/garden_chair.glb").write_bytes(b"glb flat")
    (stage / MAP).unlink()


def test_only_what_the_change_touched_is_checked(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "joinery.toml").write_text('[tools]\nenabled=["blender"]\n')
    _make(project, {"art/props/old_tree.glb": b"tree"}, "tree")
    stage = _stage_of(project, tmp_path / "stage")
    assert step_assets.changed_paths(project, stage) == set()
    assert step_assets.problems(project, stage) == []
    _make(stage, CHAIR, "chair")
    assert step_assets.changed_paths(project, stage) == set(CHAIR)
    assert step_assets.problems(project, stage) == []
    _chair_out_of_step(stage)
    found = step_assets.problems(project, stage)
    assert found == [
        "art/props/garden_chair.glb: sha256 differs from joinery.lock",
        "art/sources/garden_chair.blend: sha256 differs from joinery.lock",
        f"{MAP}: in joinery.lock but missing on disk",
    ]
    # The half-made speaker: files with no lock entry.
    for rel, data in SPEAKER.items():
        (stage / rel).parent.mkdir(parents=True, exist_ok=True)
        (stage / rel).write_bytes(data)
    found = step_assets.problems(project, stage)
    assert "art/sources/party_speaker.blend: no joinery.lock entry" in found
    assert "art/props/party_speaker.glb: no joinery.lock entry" in found


def test_an_asset_the_change_did_not_touch_is_not_hashed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "joinery.toml").write_text('[tools]\nenabled=["blender"]\n')
    _make(project, {"art/props/old_tree.glb": b"tree"}, "tree")
    stage = _stage_of(project, tmp_path / "stage")
    _make(stage, CHAIR, "chair")
    hashed: list[str] = []
    from joinery import lockfile

    real = lockfile._file_sha256

    def counted(path: Path) -> str:
        hashed.append(path.relative_to(stage).as_posix())
        return real(path)

    monkeypatch.setattr(lockfile, "_file_sha256", counted)
    assert step_assets.problems(project, stage) == []
    assert sorted(hashed) == sorted(CHAIR)


def _stage(gateway: ProjectGateway) -> Path:
    tx = gateway.staging()
    return tx.stage


def test_a_step_is_not_done_while_its_assets_fail_the_lock(project: Path) -> None:
    (project / "joinery.toml").write_text(
        '[tools]\nenabled=["godot", "blender"]\n[agents]\ntodo_go = "never"\n'
    )

    async def go() -> None:
        events: list[dict[str, Any]] = []
        gateway = wire(ProjectGateway(project))
        controller = TurnController(gateway, events.append, "codex")
        lead = GatewayRPC(gateway, None)
        await lead.start()
        try:
            controller.begin("Grill corner")
            controller.propose_todo(
                {"steps": [{"title": "Garden chair"}, {"title": "Party speaker"}]}
            )
            assert controller.todo is not None
            stage = _stage(gateway)
            _make(stage, CHAIR, "chair")
            await rpc(lead.path, "todo_step", {"step": 1, "state": "started"})
            _chair_out_of_step(stage)
            answer = await rpc(lead.path, "todo_step", {"step": 1, "state": "done"})
            assert answer["status"] == "not_done"
            assert any("missing on disk" in line for line in answer["problems"])
            assert "todo_step(1, 'done') again" in answer["next"]
            assert controller.todo.state(0) == "started"
            # Fixed in the same step: the chair made again, recorded.
            _make(stage, CHAIR, "chair")
            answer = await rpc(lead.path, "todo_step", {"step": 1, "state": "done"})
            assert answer == {"status": "ok", "finished": 1, "steps": 2}
            # A skipped step hears what it left half made.
            for rel, data in SPEAKER.items():
                (stage / rel).parent.mkdir(parents=True, exist_ok=True)
                (stage / rel).write_bytes(data)
            answer = await rpc(
                lead.path,
                "todo_step",
                {"step": 2, "state": "skipped", "note": "Speaker kept failing"},
            )
            assert answer["status"] == "ok" and answer["finished"] == 2
            assert answer["left_behind"] == [
                "art/props/party_speaker.glb: no joinery.lock entry",
                "art/sources/party_speaker.blend: no joinery.lock entry",
            ]
            assert "Remove what the step left" in answer["next"]
            assert controller.todo.state(1) == "skipped"
        finally:
            await lead.close()
            await gateway.close()

    asyncio.run(go())
