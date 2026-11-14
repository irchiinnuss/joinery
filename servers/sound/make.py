"""make_sound: a sound from Joinery's typed kit, made in the tool that owns it.

The spec (`joinery.sound_kit`) becomes a native source under audio/sources/,
SuperCollider or Surge XT renders it offline in the creative worker, and the
render is brought to the art bible's loudness (ambience beds also get their
loop points) by the same chained audio edit `process_audio` writes. So the
result is one recipe that `joinery rebuild` replays byte for byte, and later
audio edits or Surge parameter changes keep working on it.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import BaseModel, Field

from joinery import authoring, sound_kit
from joinery.bible_rules import load_rules
from joinery.mcp_runtime import validate_arguments
from joinery.tools import audio_chain, supercollider
from joinery.tools import get as get_tool
from servers.errors import tool_errors
from servers.sound import ops

#: Every recipe make_sound writes says so in its note.
NOTE_PREFIX = "Joinery sound kit: "
#: Sounds one make_sounds call takes, and how many render at once.
MAX_BATCH = 24
BATCH_PARALLEL = 4


class SoundArgument(BaseModel):
    """One sound of a make_sounds batch: make_sound's own arguments."""

    kind: Annotated[str, Field(min_length=1, max_length=40)]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    surface: str | None = None
    variant: int = 0
    seed: int | None = None
    export: str | None = None
    note: Annotated[str, Field(max_length=400)] = ""


def _channels(spec: sound_kit.SoundSpec, allowed: list[int]) -> int:
    if not allowed or spec.channels in allowed:
        return spec.channels
    return allowed[0]


@dataclass
class _Prepared:
    """A kit sound ready to render: its spec, paths, scripts and old files."""

    spec: sound_kit.SoundSpec
    source: str
    export: str
    render: str
    code: str
    post: str
    script: str
    outputs: list[str]
    note: str
    target: float | None
    #: Outputs that existed before, put back if the render fails.
    kept: dict[str, bytes] = field(default_factory=dict)


def _prepare(
    project: Path,
    kind: str,
    name: str,
    surface: str | None = None,
    variant: int = 0,
    seed: int | None = None,
    export: str | None = None,
    note: str = "",
) -> _Prepared:
    validate_arguments({"note": note})
    spec = sound_kit.SoundSpec.model_validate(
        {
            "kind": kind,
            "name": name,
            "surface": surface,
            "variant": variant,
            "seed": seed,
        }
    )
    tool = spec.tool
    authoring.enabled(project, tool)
    source = spec.source_path()
    export = ops._audio_path(project, export or spec.export_path(), "export")
    suffix = Path(export).suffix.lower()
    if spec.ambience and suffix != ".ogg":
        raise ValueError(
            "An ambience loop exports to .ogg, such as audio/ambience/wind.ogg."
        )
    if not spec.ambience and suffix != ".wav":
        raise ValueError("A sound effect exports to .wav, such as audio/sfx/jump.wav.")
    for path in (source, export):
        owner = ops._owner(project, path)
        if (project / path).is_file() and (
            owner is None or not owner[1].note.startswith(NOTE_PREFIX)
        ):
            raise ValueError(
                f"{path} already exists and the sound kit did not make it. Change it "
                "with process_audio or set_surge_parameters, or pick another name."
            )
    rules = load_rules(project)
    rule = ops._check_format(rules.audio.format, export)
    sample_rate = 48000
    if rule is not None and rule.sample_rates and sample_rate not in rule.sample_rates:
        sample_rate = rule.sample_rates[0]
    channels = _channels(spec, list(rule.channels) if rule is not None else [])
    output = ops._output({"sample_rate": sample_rate, "channels": channels}, rule)
    steps = audio_chain.resolve_targets(
        audio_chain.parse_steps(spec.steps()),
        export,
        rules.audio.lufs,
        rules.audio.true_peak_max_dbtp,
        default_lufs=spec.default_lufs,
    )
    render = spec.render_path(export)
    code = spec.source_text(render, sample_rate, channels)
    base = get_tool(tool).script(source, "", code, [render])
    payload = {
        "source": render,
        "export": export,
        "steps": [step.model_dump() for step in steps],
        "output": output.model_dump(),
    }
    script, outputs = ops._with_edits(tool, base, [source, render], [payload])
    # The same edit program the script's chain runs, for a batch to run.
    post = audio_chain.program(render, export, steps, output)
    # The patches and raw renders are sources: Godot never imports them.
    folder = authoring.confined(project, f"audio/sources/{spec.folder}")
    folder.mkdir(parents=True, exist_ok=True)
    ignore = folder / ".gdignore"
    if not ignore.exists():
        ignore.write_text("")
    target = next((s.lufs for s in steps if isinstance(s, audio_chain.Normalise)), None)
    note_text = (
        NOTE_PREFIX
        + f"{spec.describe()}, {spec.kind} variant {spec.variant} seed "
        + f"{spec.effective_seed}, normalised to {target} LUFS"
        + (", looping" if spec.ambience else "")
        + "."
        + (f" {note}" if note else "")
    )
    kept = {
        path: (project / path).read_bytes()
        for path in outputs
        if (project / path).is_file()
    }
    return _Prepared(
        spec=spec,
        source=source,
        export=export,
        render=render,
        code=code,
        post=post,
        script=script,
        outputs=outputs,
        note=note_text,
        target=target,
        kept=kept,
    )


def _restore(project: Path, sound: _Prepared) -> None:
    """A render that did not complete leaves nothing behind: no half-made file
    without a recipe, and a sound made before stays as it was."""
    for path in sound.outputs:
        target_path = project / path
        if path in sound.kept:
            target_path.write_bytes(sound.kept[path])
        else:
            target_path.unlink(missing_ok=True)


def _finish(project: Path, sound: _Prepared, result: dict[str, Any]) -> dict[str, Any]:
    spec = sound.spec
    imported = ops._write_import(project, sound.export, result["measured"])
    if imported:
        result["godot_import"] = imported
    result["sound"] = {
        "kind": spec.kind,
        "name": spec.name,
        "surface": spec.surface,
        "variant": spec.variant,
        "seed": spec.effective_seed,
        "tool": spec.tool,
        "export": sound.export,
        "source": sound.source,
        "target_lufs": sound.target,
    }
    measured = [
        s for s in result["measured"].get("steps", []) if s.get("op") == "normalise"
    ]
    if measured:
        result["loudness"] = {
            "lufs": measured[-1].get("lufs"),
            "true_peak": measured[-1].get("true_peak"),
            "limited": measured[-1].get("limited", False),
        }
    return result


async def make_sound(
    project: Path,
    kind: str,
    name: str,
    surface: str | None = None,
    variant: int = 0,
    seed: int | None = None,
    export: str | None = None,
    note: str = "",
    *,
    lock: bool = True,
) -> dict[str, Any]:
    """`lock=False` leaves joinery.lock to the caller: sounds rendering side by
    side each have their files on disk before their recipe, so the lock is
    written once they all have one."""
    sound = _prepare(project, kind, name, surface, variant, seed, export, note)
    try:
        result = await ops._run(
            project,
            sound.spec.tool,
            sound.script,
            sound.outputs,
            (),
            sound.note,
            chained=True,
            lock=lock,
        )
    except BaseException:
        _restore(project, sound)
        raise
    return _finish(project, sound, result)


async def _render_together(
    project: Path, sounds: list[_Prepared]
) -> list[dict[str, Any] | Exception]:
    """Render SuperCollider kit sounds in one sclang session.

    Each sound still gets its own standalone recipe script (the one make_sound
    writes), recorded from what the batch rendered; a seeded patch renders the
    same bytes either way, so `joinery rebuild` replays each recipe alone and
    checks it exactly. A sound that fails is that sound's error alone.
    """
    staged: list[ops.Staged] = []
    try:
        for sound in sounds:
            staged.append(
                ops.stage(
                    project,
                    "supercollider",
                    sound.script,
                    sound.outputs,
                    (),
                    sound.note,
                )
            )
        batch = project / f".joinery-batch-{uuid.uuid4().hex}.scd"
        batch.write_text(
            supercollider.batch_script(
                [(s.source, s.code, s.render, s.post) for s in sounds]
            )
        )
        try:
            limit = supercollider.batch_limit(len(sounds))
            result = await authoring.run_offline(
                ["timeout", str(limit + 60), "sclang", batch.name],
                project,
                max(ops.RENDER_TIMEOUT, limit + 120),
            )
        finally:
            batch.unlink(missing_ok=True)
    except BaseException:
        for done in staged:
            (project / done.recipe.script).unlink(missing_ok=True)
        for sound in sounds:
            _restore(project, sound)
        raise
    parts = supercollider.batch_results(result.output, len(sounds))
    out: list[dict[str, Any] | Exception] = [
        RuntimeError("SuperCollider did not complete the operation: no output")
    ] * len(sounds)
    # Failed sounds go first, so the lock is written over the made ones only.
    for index, (sound, (ok, output)) in enumerate(zip(sounds, parts, strict=True)):
        if not ok:
            out[index] = RuntimeError(
                f"SuperCollider did not complete the operation: {ops._tail(output)}"
            )
            (project / staged[index].recipe.script).unlink(missing_ok=True)
            _restore(project, sound)
    recorded: list[int] = []
    for index, (sound, (ok, output)) in enumerate(zip(sounds, parts, strict=True)):
        if not ok:
            continue
        # The batch's line for this sound stands for its script's own marker.
        marked = output + "\nJOINERY_POST_OK"
        try:
            made = await ops.finish(
                project, staged[index], ok, marked, chained=True, lock=False
            )
        except Exception as exc:  # noqa: BLE001 - reported as that sound's failure
            _restore(project, sound)
            out[index] = exc
            continue
        out[index] = _finish(project, sound, made)
        recorded.append(index)
    if recorded:
        try:
            # One lock for the batch: every made sound has its recipe now.
            async with ops._recording():
                authoring._lock(project)
        except ValueError as exc:
            for index in recorded:
                done = staged[index]
                (project / done.recipe.script).unlink(missing_ok=True)
                (project / "recipes" / f"{done.identifier}.toml").unlink(
                    missing_ok=True
                )
                _restore(project, sounds[index])
                out[index] = exc
    return out


async def make_sounds(
    project: Path, sounds: list[SoundArgument], parallel: int = BATCH_PARALLEL
) -> dict[str, Any]:
    """Several kit sounds in one call: each is made exactly as make_sound makes it
    (its own source, recipe and lock entry, so rebuild and later edits work per
    sound). SuperCollider sounds render one after another in a single sclang
    session; Surge XT sounds render side by side a few at a time. A sound that
    fails is reported beside the ones made; it never undoes them."""
    if not 1 <= len(sounds) <= MAX_BATCH:
        raise ValueError(f"Give 1 to {MAX_BATCH} sounds.")
    names = [s.name for s in sounds]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise ValueError("Each sound needs its own name: " + ", ".join(twice) + ".")
    gate = asyncio.Semaphore(max(1, parallel))
    results: list[dict[str, Any] | BaseException | None] = [None] * len(sounds)
    together: list[tuple[int, _Prepared]] = []
    alone: list[int] = []
    for index, sound in enumerate(sounds):
        try:
            prepared = _prepare(project, **sound.model_dump())
        except Exception as exc:  # noqa: BLE001 - reported as that sound's failure
            results[index] = exc
            continue
        if prepared.spec.tool == "supercollider" and supercollider.batchable(
            prepared.code
        ):
            together.append((index, prepared))
        else:
            alone.append(index)

    async def one(index: int) -> None:
        async with gate:
            try:
                results[index] = await make_sound(
                    project, **sounds[index].model_dump(), lock=False
                )
            except Exception as exc:  # noqa: BLE001 - reported as that sound's failure
                results[index] = exc

    if len(together) == 1:
        # One SuperCollider sound alone renders the way make_sound does.
        alone.append(together.pop()[0])
    # Side by side, every render's files are on disk before its recipe is, so
    # the lock is written once, when every finished sound has its recipe.
    await asyncio.gather(*(one(i) for i in alone))
    if any(isinstance(results[i], dict) for i in alone):
        async with ops._recording():
            authoring._lock(project)
    # Then the SuperCollider session, which writes the lock for its own sounds.
    if together:
        rendered = await _render_together(project, [p for _, p in together])
        for (index, _), one_result in zip(together, rendered, strict=True):
            results[index] = one_result
    made: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for sound, result in zip(sounds, results, strict=True):
        if isinstance(result, dict):
            made.append(result)
        else:
            failed.append(
                {"name": sound.name, "error": str(result) or type(result).__name__}
            )
    return {
        "made": made,
        "failed": failed,
        "status": "staged" if made else "failed",
        "requires_review": bool(made),
        "outputs": [o for r in made for o in r.get("outputs", [])],
    }


def register(server: MCPServer[Any], project: Path) -> None:
    @server.tool(name="make_sounds")
    @tool_errors
    async def make_sounds_tool(sounds: list[SoundArgument]) -> dict[str, Any]:
        """Make several kit sounds in one call (up to 24): each item takes make_sound's arguments (kind, name, surface, variant, seed, export, note). SuperCollider sounds render in one session and Surge XT ones side by side, each with its own source and replayable recipe. Prefer it to one make_sound call per sound. Returns made (each like make_sound's result) and failed (name, error)."""
        return await make_sounds(project, sounds)

    @server.tool(name="make_sound")
    @tool_errors
    async def make_sound_tool(
        kind: str,
        name: str,
        surface: str | None = None,
        variant: int = 0,
        seed: int | None = None,
        export: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Make a game sound from Joinery's typed kit, in the tool that owns it, with a replayable recipe. kind: footstep (needs surface: default, grass, stone, wood, metal, sand or water), jump, land, pickup, hit, damage, ui_click, ui_hover, door, button, lever, plate (one-shot WAVs, mono, in audio/sfx/<name>.wav), or wind, room_tone, cave, forest (looping ambience beds, stereo Ogg in audio/ambience/<name>.ogg with Godot's loop import). Noisy sounds are seeded SuperCollider NRT patches; pickups and UI blips are Surge XT sine patches. The source goes to audio/sources/sfx|ambience/<name>.scd|.json. variant picks another take; seed fixes the randomness (derived from the rest when left out). Loudness follows the art bible (the kit's own target where it sets none). Requires approved art direction and user review."""
        return await make_sound(
            project, kind, name, surface, variant, seed, export, note
        )
