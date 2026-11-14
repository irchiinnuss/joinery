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
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import BaseModel, Field

from joinery import authoring, sound_kit
from joinery.bible_rules import load_rules
from joinery.mcp_runtime import validate_arguments
from joinery.tools import audio_chain
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


async def make_sound(
    project: Path,
    kind: str,
    name: str,
    surface: str | None = None,
    variant: int = 0,
    seed: int | None = None,
    export: str | None = None,
    note: str = "",
) -> dict[str, Any]:
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
    try:
        result = await ops._run(
            project, tool, script, outputs, (), note_text, chained=True
        )
    except BaseException:
        # A render that did not complete leaves nothing behind: no half-made
        # file without a recipe, and a sound made before stays as it was.
        for path in outputs:
            target_path = project / path
            if path in kept:
                target_path.write_bytes(kept[path])
            else:
                target_path.unlink(missing_ok=True)
        raise
    imported = ops._write_import(project, export, result["measured"])
    if imported:
        result["godot_import"] = imported
    result["sound"] = {
        "kind": spec.kind,
        "name": spec.name,
        "surface": spec.surface,
        "variant": spec.variant,
        "seed": spec.effective_seed,
        "tool": tool,
        "export": export,
        "source": source,
        "target_lufs": target,
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


async def make_sounds(
    project: Path, sounds: list[SoundArgument], parallel: int = BATCH_PARALLEL
) -> dict[str, Any]:
    """Several kit sounds in one call: each is made exactly as make_sound makes it
    (its own source, recipe and lock entry, so rebuild and later edits work per
    sound), rendered side by side a few at a time instead of one round trip each.
    A sound that fails is reported beside the ones made; it never undoes them."""
    if not 1 <= len(sounds) <= MAX_BATCH:
        raise ValueError(f"Give 1 to {MAX_BATCH} sounds.")
    names = [s.name for s in sounds]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise ValueError("Each sound needs its own name: " + ", ".join(twice) + ".")
    gate = asyncio.Semaphore(max(1, parallel))

    async def one(sound: SoundArgument) -> dict[str, Any]:
        async with gate:
            return await make_sound(project, **sound.model_dump())

    results = await asyncio.gather(*(one(s) for s in sounds), return_exceptions=True)
    made: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for sound, result in zip(sounds, results, strict=True):
        if isinstance(result, BaseException):
            if not isinstance(result, Exception):
                raise result  # a cancel or exit is never a sound's own failure
            failed.append(
                {"name": sound.name, "error": str(result) or type(result).__name__}
            )
        else:
            made.append(result)
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
        """Make several kit sounds in one call (up to 24): each item takes make_sound's arguments (kind, name, surface, variant, seed, export, note). They render side by side, each with its own source and replayable recipe. Prefer it to one make_sound call per sound. Returns made (each like make_sound's result) and failed (name, error)."""
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
