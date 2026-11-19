"""The sound kit: typed specs become deterministic native patches; make_sound is typed."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from joinery import gateway, sound_kit
from joinery.tools import get, supercollider
from servers.sound import make


def test_every_kind_has_a_patch_in_its_tool() -> None:
    for kind in sound_kit.KINDS:
        surface = "stone" if kind == "footstep" else None
        spec = sound_kit.SoundSpec(kind=kind, name=f"k_{kind}", surface=surface)  # type: ignore[arg-type]
        export = spec.export_path()
        render = spec.render_path(export)
        text = spec.source_text(render, 48000, spec.channels)
        if spec.tool == "surge-xt":
            patch = json.loads(text)
            assert patch["synth"] == "surge-xt"
            assert patch["parameters"]["a_osc_1_type"] == "Sine", kind
            assert patch["notes"]
            assert spec.source_path().endswith(".json")
        else:
            # Seeded noise and an NRT render that exits: a replay is byte for byte.
            assert f"RandSeed.ir(1, {spec.effective_seed});" in text, kind
            assert f'outputFilePath: "{render}"' in text
            assert "0.exit" in text
            assert spec.source_path().endswith(".scd")
            # The wrapped recipe compiles the patch rather than refusing it.
            script = get("supercollider").script(spec.source_path(), "", text, [render])
            assert "never exits" not in script
        assert text == spec.source_text(render, 48000, spec.channels), kind


def test_where_sounds_go() -> None:
    step = sound_kit.SoundSpec(kind="footstep", name="step_grass", surface="grass")
    assert step.export_path() == "audio/sfx/step_grass.wav"
    assert step.render_path(step.export_path()) == step.export_path()
    assert step.source_path() == "audio/sources/sfx/step_grass.scd"
    assert step.channels == 1 and step.tool == "supercollider"
    wind = sound_kit.SoundSpec(kind="wind", name="level_wind")
    assert wind.export_path() == "audio/ambience/level_wind.ogg"
    assert (
        wind.render_path(wind.export_path()) == "audio/sources/ambience/level_wind.wav"
    )
    assert wind.channels == 2
    assert [s["op"] for s in wind.steps()] == ["loop", "normalise"]
    assert sound_kit.SoundSpec(kind="pickup", name="pickup_coin").tool == "surge-xt"


def test_variants_differ_and_seeds_are_stable() -> None:
    one = sound_kit.SoundSpec(kind="hit", name="hit_enemy")
    two = sound_kit.SoundSpec(kind="hit", name="hit_enemy", variant=1)
    assert (
        one.effective_seed
        == sound_kit.SoundSpec(kind="hit", name="hit_enemy").effective_seed
    )
    assert one.effective_seed != two.effective_seed
    assert one.source_text("a.wav", 48000, 1) != two.source_text("a.wav", 48000, 1)
    fixed = sound_kit.SoundSpec(kind="hit", name="hit_enemy", seed=7)
    assert "RandSeed.ir(1, 7);" in fixed.source_text("a.wav", 48000, 1)


def test_specs_are_refused_in_plain_words() -> None:
    with pytest.raises(ValidationError, match="needs its surface"):
        sound_kit.SoundSpec(kind="footstep", name="step")
    with pytest.raises(ValidationError, match="only footsteps"):
        sound_kit.SoundSpec(kind="jump", name="jump", surface="stone")
    with pytest.raises(ValidationError, match="lower_snake_case"):
        sound_kit.SoundSpec(kind="jump", name="Big Jump")
    with pytest.raises(ValidationError):
        sound_kit.SoundSpec(kind="explosion", name="boom")  # type: ignore[arg-type]


def test_make_sound_runs_the_app_of_its_kind() -> None:
    assert "make_sound" in gateway.NATIVE_OPS
    assert gateway.op_apps("make_sound", {"kind": "footstep"}) == ("supercollider",)
    assert gateway.op_apps("make_sound", {"kind": "ui_click"}) == ("surge-xt",)
    assert gateway.op_apps("make_sound", {"kind": "cave"}) == ("supercollider",)
    assert gateway.op_apps("make_sound", {"kind": "nope"}) == ("supercollider",)


def test_make_sound_needs_a_transaction_and_its_tool(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    with pytest.raises(PermissionError, match="project transaction"):
        asyncio.run(make.make_sound(stage, "jump", "player_jump"))
    (tmp_path / "journal.json").write_text(json.dumps({"state": "staging"}))
    with pytest.raises(PermissionError, match="supercollider is not selected"):
        asyncio.run(make.make_sound(stage, "jump", "player_jump"))
    (stage / "joinery.toml").write_text(
        '[tools]\nenabled = ["godot", "supercollider"]\n'
    )
    with pytest.raises(ValueError, match="exports to .wav"):
        asyncio.run(
            make.make_sound(stage, "jump", "player_jump", export="audio/sfx/jump.ogg")
        )
    # A file the kit did not make is never written over.
    (stage / "audio/sfx").mkdir(parents=True)
    (stage / "audio/sfx/player_jump.wav").write_bytes(b"RIFF")
    with pytest.raises(ValueError, match="did not make it"):
        asyncio.run(make.make_sound(stage, "jump", "player_jump"))


def test_make_sounds_batches_supercollider_and_runs_surge_side_by_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    running: list[str] = []
    most = 0

    async def fake(
        project: Path, kind: str, name: str, *_a: object, **_k: object
    ) -> dict[str, object]:
        nonlocal most
        running.append(name)
        most = max(most, len(running))
        await asyncio.sleep(0.05)
        running.remove(name)
        return {"outputs": [f"audio/sfx/{name}.wav"], "status": "staged"}

    def prepare(project: Path, kind: str, name: str, **_k: object) -> object:
        if kind == "nope":
            raise ValueError("no such kind")
        tool = "surge-xt" if kind == "pickup" else "supercollider"
        return SimpleNamespace(
            spec=SimpleNamespace(tool=tool), code="{ 0.exit }", n=name
        )

    batches: list[list[str]] = []

    async def together(project: Path, prepared: list[SimpleNamespace]) -> list[object]:
        batches.append([p.n for p in prepared])
        return [
            RuntimeError("render failed")
            if p.n == "j1"
            else {"outputs": [f"audio/sfx/{p.n}.wav"]}
            for p in prepared
        ]

    monkeypatch.setattr(make, "make_sound", fake)
    monkeypatch.setattr(make, "_prepare", prepare)
    monkeypatch.setattr(make, "_render_together", together)
    locks: list[Path] = []
    touched: list[tuple[str, ...]] = []

    def lock(project: Path, outputs: tuple[str, ...] | None = None) -> list[str]:
        locks.append(project)
        touched.append(outputs or ())
        return []

    monkeypatch.setattr(make.authoring, "_lock", lock)
    sounds = [make.SoundArgument(kind="pickup", name=f"s{i}") for i in range(6)]
    sounds += [make.SoundArgument(kind="jump", name=f"j{i}") for i in range(3)]
    sounds.append(make.SoundArgument(kind="nope", name="bad"))
    result = asyncio.run(make.make_sounds(tmp_path, sounds, parallel=3))
    # Surge sounds three at a time; every SuperCollider sound in one session.
    assert most == 3 and batches == [["j0", "j1", "j2"]]
    # The side-by-side sounds write the lock once, after they all finished.
    assert locks == [tmp_path]
    # Scoped to the sounds it made: another asset's lost file never refuses it.
    assert "audio/sfx/s0.wav" in touched[0]
    assert len(result["made"]) == 8
    assert result["failed"] == [
        {"name": "j1", "error": "render failed"},
        {"name": "bad", "error": "no such kind"},
    ]
    assert "audio/sfx/s0.wav" in result["outputs"] and result["requires_review"]
    with pytest.raises(ValueError, match="own name"):
        asyncio.run(make.make_sounds(tmp_path, [sounds[0], sounds[0]]))
    with pytest.raises(ValueError, match="1 to"):
        asyncio.run(make.make_sounds(tmp_path, []))


def test_a_batch_holds_the_slot_of_every_app_it_runs() -> None:
    kinds = [{"kind": "footstep"}, {"kind": "pickup"}, {"kind": "jump"}]
    apps = gateway.op_apps("make_sounds", {"sounds": kinds})
    assert set(apps) == {sound_kit.app_for(k) for k in kinds}


def test_a_batch_script_renders_each_patch_as_its_own_recipe_would() -> None:
    spec = sound_kit.SoundSpec(kind="hit", name="hit")
    code = spec.source_text("audio/sfx/hit.wav", 48000, 1)
    assert supercollider.batchable(code)
    script = supercollider.batch_script(
        [("audio/sources/sfx/hit.scd", code, "audio/sfx/hit.wav", "print('edit')\n")]
        * 2
    )
    # Each sound writes its source and edit program, then signals the next sound
    # where its own recipe would end sclang; only the batch's end exits.
    assert script.count("JOINERY_SOUND_BEGIN ") == 2
    # The source keeps its own exit; the code the batch runs signals instead.
    assert script.count("{ 0.exit }") == 2
    assert script.count("~joineryDone.signal") == 2
    assert script.count("~joineryDone.wait;") == 2
    assert f"thisThread.randSeed = {spec.effective_seed};" in script
    assert "  0.exit;\n}.play(AppClock);" in script
    assert not supercollider.batchable("0.exit; 1.exit;")
    log = (
        "JOINERY_SOUND_BEGIN 0\nJOINERY_AUDIO {}\nJOINERY_SOUND_OK 0\n"
        "JOINERY_SOUND_BEGIN 1\nJOINERY_SOUND_FAILED 1"
    )
    assert supercollider.batch_results(log, 3) == [
        (True, "JOINERY_AUDIO {}\nJOINERY_SOUND_OK 0"),
        (False, "JOINERY_SOUND_FAILED 1"),
        (False, ""),
    ]


def test_a_batch_records_each_sound_alone_and_one_failure_undoes_only_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from joinery import authoring
    from joinery.runner import Result

    stage = tmp_path / "stage"
    stage.mkdir()
    (tmp_path / "journal.json").write_text(json.dumps({"state": "staging"}))
    (stage / "joinery.toml").write_text('[tools]\nenabled = ["supercollider"]\n')
    sounds = [make._prepare(stage, "hit", "hit"), make._prepare(stage, "door", "door")]

    async def render(argv: list[str], project: Path, *_a: object) -> Result:
        assert argv[-2] == "sclang" and (project / argv[-1]).is_file()
        for path in sounds[0].outputs:  # only the first sound renders
            (project / path).write_bytes(b"RIFF")
        (project / sounds[1].source).write_text("half made")
        return Result(True, "JOINERY_SOUND_BEGIN 0\nJOINERY_SOUND_OK 0\n", "", 1.0)

    recorded: list[str] = []
    locked: list[Path] = []

    async def record(
        project: Path, recipe: object, path: Path, *, lock: bool = True
    ) -> dict[str, object]:
        assert not lock
        recorded.append(path.name)
        return {"outputs": list(recipe.outputs), "status": "staged"}  # type: ignore[attr-defined]

    monkeypatch.setattr(authoring, "run_offline", render)
    monkeypatch.setattr(authoring, "_record", record)
    monkeypatch.setattr(
        authoring, "_lock", lambda project, _outputs=None: locked.append(project)
    )
    made, failed = asyncio.run(make._render_together(stage, sounds))
    assert isinstance(made, dict) and made["sound"]["name"] == "hit"
    assert isinstance(failed, RuntimeError)
    # One recipe each, one lock for the batch, and no batch script left behind.
    assert len(recorded) == 1 and locked == [stage]
    scripts = list((stage / "recipes").glob("*.scd"))
    assert len(scripts) == 1 and "joinery_hit" in scripts[0].read_text()
    assert not list(stage.glob(".joinery-batch-*"))
    # The failed sound leaves nothing half made.
    assert not (stage / sounds[1].source).exists()
