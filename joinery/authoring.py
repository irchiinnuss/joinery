"""Native-document authoring with offline execution and measured replay records."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from joinery import docker_cli, tools, worker_platform
from joinery.catalog import load_catalog, load_project_tools, selection_ids
from joinery.lockfile import GLTF_SUFFIXES, LOCK_NAME
from joinery.locking import write_lock
from joinery.mcp_runtime import validate_arguments
from joinery.recipe import (
    HISTORY_DIR,
    PREIMAGE_DIR,
    Recipe,
    load_recipe,
    recipe_paths,
    recipe_text,
    record,
    store_preimage,
)
from joinery.runner import Result, Runner, SandboxRunner
from joinery.tools import (
    blender,
    blender_anim,
    blender_batch,
    blender_export,
    inkscape,
    material_maker,
)
from joinery.tools.base import ToolModule
from joinery.transactions import copy_file
from joinery.worker_identity import docker_owner_args

IMAGE = "joinery-creative-worker"
#: Tools whose native documents Joinery edits (script, graph or vector adapter).
SUPPORTED = tools.authoring_ids()
SUFFIXES = {tool: tools.get(tool).suffix for tool in SUPPORTED}
#: Native formats inspect_document returns as text.
TEXT_SUFFIXES = {".scd", ".sc", ".ptex", ".svg", ".json"}
#: Kept for callers that still import these from here.
texture_dir = blender.texture_dir
material_export_driver = material_maker.export_driver


def confined(project: Path, relative: str) -> Path:
    path = Path(relative.removeprefix("res://"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Use a path relative to this project.")
    target = project / path
    if not target.resolve().is_relative_to(project.resolve()):
        raise ValueError("The document is outside this project.")
    return target


#: Warnings an app prints about its own bundled scripts on every start (Krita's
#: scripter under Python 3.12): never the agent's code, and long enough to push
#: the real traceback out of the error the agent reads.
_NOISE = re.compile(r"^\S+\.py:\d+: SyntaxWarning: .*\n(?:[ \t]+.*\n?)?", re.MULTILINE)


def _quiet(output: str) -> str:
    """``output`` without the apps' own start-up warnings."""
    return _NOISE.sub("", output)


class WorkerTimeout(TimeoutError):
    """An offline run past its time limit (the worker is killed), with what it
    printed until then."""

    def __init__(self, seconds: float, output: str = "") -> None:
        super().__init__(f"the offline worker ran past {seconds:g} s")
        self.seconds = seconds
        self.output = output


class WorkerStalled(WorkerTimeout):
    """An offline run stopped early: it printed a script or parse error and then
    nothing new for ``idle`` seconds (a SceneTree test whose ``_run`` stopped at
    the error never reaches its quit()). Repeating the same error every frame is
    not progress."""

    def __init__(self, idle: float, output: str = "") -> None:
        super().__init__(idle, output)
        said = (
            "the offline worker printed a script error and then nothing new for "
            f"{idle:g} s, so it was stopped"
        )
        self.args = (said,)
        self.idle = idle


#: What Godot prints when a script stops at an error (or never compiles), or when
#: Godot itself crashed: an emulated worker can hang in the crash handler after
#: "Program crashed" instead of exiting.
STALL_SIGNS = ("SCRIPT ERROR:", "Parse Error:", "handle_crash: Program crashed")


class _Progress:
    """Watches a run's output: whether it printed an error, and when it last
    printed a line it had not printed before."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.errored = False
        self.last_new = 0.0
        self._partial = b""

    def feed(self, chunk: bytes, now: float) -> None:
        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()
        for raw in lines:
            line = raw.decode(errors="replace").strip()
            if not line or line in self.seen:
                continue
            self.seen.add(line)
            self.last_new = now
            if any(sign in line for sign in STALL_SIGNS):
                self.errored = True


async def run_offline(
    argv: list[str],
    project: Path,
    timeout: int = 600,
    *,
    readonly: bool = False,
    image: str = IMAGE,
    warm: bool = True,
    stall: float = 0.0,
) -> Result:
    """Run ``argv`` in the offline worker on ``project`` (mounted at /work).

    A supervised session reuses one warm worker per project, one command at a
    time; ``warm=False`` starts a worker of its own, so several run at once.
    ``stall`` (seconds, a worker of its own only) stops the run early, raising
    :class:`WorkerStalled`, once it printed a script or parse error and then no
    new line for that long, instead of waiting out ``timeout``."""
    if warm and os.environ.get("JOINERY_WORKER_SCOPE"):
        from joinery.warm_workers import execute

        return await execute(argv, project, timeout, readonly, image)
    name = "joinery-mcp-" + uuid.uuid4().hex
    start = asyncio.get_running_loop().time()
    runs_as = await asyncio.to_thread(worker_platform.platform_for, image)
    process = await asyncio.create_subprocess_exec(
        "docker",
        "run",
        "--rm",
        *docker_owner_args(),
        "--platform",
        runs_as,
        "--network",
        "none",
        "--name",
        name,
        "--label",
        "joinery.scope="
        + os.environ.get("JOINERY_WORKER_SCOPE", name.removeprefix("joinery-mcp-")),
        "--pids-limit",
        "512",
        "--memory",
        "4g",
        "-v",
        f"{project.resolve()}:/work" + (":ro" if readonly else ""),
        "-w",
        "/work",
        image,
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await supervise(process, timeout, stall)
        return Result(
            process.returncode == 0,
            out,
            err,
            asyncio.get_running_loop().time() - start,
        )
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        cleanup = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "--force",
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(cleanup.wait(), 10)
        if cleanup.returncode is None:
            cleanup.kill()
            await cleanup.wait()


async def supervise(
    process: asyncio.subprocess.Process, timeout: float, stall: float = 0.0
) -> tuple[str, str]:
    """``process``'s output once it ends, read as it goes.

    Raises :class:`WorkerTimeout` past ``timeout`` and, with ``stall``, raises
    :class:`WorkerStalled` once it printed a script or parse error and then no new
    line for that long. Stopping the run is the caller's (the process may be a
    ``docker`` client whose work goes on in a container)."""
    out, err = bytearray(), bytearray()
    loop = asyncio.get_running_loop()
    progress = _Progress()

    async def pump(stream: asyncio.StreamReader | None, into: bytearray) -> None:
        while stream is not None and (chunk := await stream.read(65536)):
            into.extend(chunk)
            progress.feed(chunk, loop.time())

    async def watch() -> None:
        # Ends (so the run is stopped) once an error is followed by nothing new.
        while True:
            await asyncio.sleep(min(1.0, stall / 4))
            if progress.errored and loop.time() - progress.last_new >= stall:
                return

    finished = asyncio.ensure_future(
        asyncio.gather(
            pump(process.stdout, out), pump(process.stderr, err), process.wait()
        )
    )
    watcher = asyncio.ensure_future(watch()) if stall > 0 else None
    try:
        waiting = {finished} | ({watcher} if watcher is not None else set())
        done, _ = await asyncio.wait(
            waiting, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        pending = [t for t in (finished, watcher) if t is not None and not t.done()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    if finished not in done:
        # What it printed before it was stopped says why it never finished.
        printed = (out + err).decode(errors="replace")
        if watcher is not None and watcher in done:
            raise WorkerStalled(stall, printed)
        if not await docker_cli.responding_async():
            # A hung Docker, not a slow tool: say what to do about it.
            raise docker_cli.DockerNotResponding("docker run")
        raise WorkerTimeout(timeout, printed)
    finished.result()
    return out.decode(errors="replace"), err.decode(errors="replace")


async def inspect_document(project: Path, tool: str, source: str) -> dict[str, Any]:
    if tool not in selection_ids(
        load_catalog(), load_project_tools(project / "joinery.toml")
    ):
        raise PermissionError("Select this owning application in project setup first.")
    # The app reads a project path, never Godot's res:// form.
    source = source.removeprefix("res://")
    path = confined(project, source)
    if not path.is_file():
        raise ValueError("The source document is missing.")
    module = tools.REGISTRY.get(tool)
    if module is not None and module.suffix == path.suffix:
        described = await module.inspect(project, path, source, run_offline)
        if described is not None:
            return described
    if path.suffix in TEXT_SUFFIXES:
        if path.stat().st_size > 2_000_000:
            raise ValueError(
                "Read specific graph sections; this document exceeds the inspection text limit."
            )
        return {"source": source, "content": path.read_text()}
    raise ValueError("Use the owning app's native inspection tool for this format.")


def require_transaction(project: Path) -> None:
    """Refuse unless `project` is the stage of a transaction that is staging."""
    journal = project.parent / "journal.json"
    if (
        project.name != "stage"
        or not journal.is_file()
        or json.loads(journal.read_text()).get("state") != "staging"
    ):
        raise PermissionError(
            "Native authoring requires a project transaction; open it through the project gateway."
        )


def enabled(project: Path, tool: str) -> None:
    require_transaction(project)
    if tool not in SUPPORTED:
        raise ValueError("No native document adapter for this tool.")
    if tool not in selection_ids(
        load_catalog(), load_project_tools(project / "joinery.toml")
    ):
        raise PermissionError(f"{tool} is not selected for this project.")


async def edit_document(
    project: Path,
    tool: str,
    source: str,
    code: str,
    exports: list[str],
    note: str = "",
    *,
    keep_macro_shape_keys: bool | None = None,
    shape_keys: blender_export.ExportShapeKeys | None = None,
    inputs: tuple[str, ...] = (),
    seed: int | None = None,
) -> dict[str, Any]:
    """Apply `code` to a native document, export it and record the recipe.

    `inputs` are further project files the code reads (a painted map), listed
    as recipe sources. For Blender, `shape_keys` chooses the GLB's morph
    targets and `keep_macro_shape_keys` keeps MPFB2's body-shape keys as morph
    targets; with neither, the model keeps its recorded choice. `seed` fixes a
    SuperCollider patch's random sources (derived from the source when left
    out); the recipe records it.
    """
    module, source, exports, target = _checked_document(
        project, tool, source, exports, (code,)
    )
    code = module.seed_code(code, source, seed)
    inputs = tuple(dict.fromkeys((*inputs, *module.script_inputs(code))))
    textures: tuple[str, ...] = ()
    if tool == "blender":
        # Texture files are regenerated on every edit; clear the old set so an
        # image removed from the model does not leave a stale file behind.
        textures = _textures(project, source)
    _refuse_dropping_audio_edits(project, source, (source, *exports))
    _replacements(project, (source, *exports, *textures))
    identifier = uuid.uuid4().hex
    recipe_dir = project / "recipes"
    recipe_dir.mkdir(exist_ok=True)
    # An immutable preimage makes an incremental edit replayable without
    # depending on the future version of the very document it modifies.
    # Named by content, so edits from the same state share one copy.
    keep = target.is_file() and module.uses_preimage
    base = store_preimage(project, target) if keep else ""
    for output in [source, *exports]:
        confined(project, output).parent.mkdir(parents=True, exist_ok=True)
    for extra in inputs:
        if not confined(project, extra).is_file():
            raise ValueError(f"{extra} is not a file in this project.")
    # Every file this operation may write, as it is now: a failure at any point
    # (the app, the recipe, the lock) puts all of it back, so the model's
    # files, its recipe and its lock entry still agree.
    undo = Undo(
        project,
        (source, *exports),
        (texture_dir(source),) if tool == "blender" else (),
    )
    script = recipe_dir / (identifier + module.script_ext)
    try:
        if isinstance(module, blender.Blender):
            chosen = _export_shape_keys(
                project, source, keep_macro_shape_keys, shape_keys
            )
            script.write_text(
                module.script(source, base, code, exports, shape_keys=chosen)
            )
        else:
            script.write_text(module.script(source, base, code, exports))
        if module.text_source:
            target.write_text(code)
        recipe = Recipe(
            tool=tool,
            script=script.relative_to(project).as_posix(),
            outputs=(source, *exports),
            sources=tuple(dict.fromkeys(((base,) if base else ()) + inputs)),
            note=note,
            seed=module.recipe_seed(code),
        )
        # What the exports held before, to tell an edit that changes nothing.
        exported_before = _digests(project, exports)
        # The old maps go before the run, so an image the edit removed leaves no
        # stale file behind; a failed or refused edit puts them back.
        for old in textures:
            (project / old).unlink(missing_ok=True)
        try:
            argv = module.replay_argv(recipe.script)
            if module.replay_image:
                # A tool with its own worker image (Audacity) replays there.
                result = await run_offline(argv, project, image=module.replay_image)
            else:
                result = await run_offline(argv, project)
        except WorkerTimeout as late:
            raise RuntimeError(
                f"{tool} did not finish the document operation in {late.seconds:g} s "
                "and was stopped: the code may loop forever or wait for something "
                f"that never comes. {_quiet(late.output)[-1500:]}".rstrip()
            ) from None
        if not result.ok:
            # A failed operation is no recipe: the undo leaves no script behind.
            raise RuntimeError(
                f"{tool} did not complete the document operation: "
                f"{_quiet(result.output)[-2000:]}"
            )
        if tool == "blender":
            folder = confined(project, texture_dir(source))
            written = tuple(
                p.relative_to(project).as_posix() for p in sorted(folder.glob("*.png"))
            )
            recipe = recipe.model_copy(update={"outputs": (*recipe.outputs, *written)})
            # Godot imports portable GLB exports. Keep native .blend documents
            # without requiring a Blender install on every game developer's host.
            (target.parent / ".gdignore").write_text("")
        # The maps from before are this recipe's to replace even when the edit
        # removed their image and wrote no file for it.
        recorded = await _record(
            project, recipe, recipe_dir / (identifier + ".toml"), replaces=textures
        )
    except BaseException:
        undo.restore()
        raise
    if tool == "blender":
        recorded.update(_export_facts(project, exports, result.output))
    if exported_before and _digests(project, exports) == exported_before:
        recorded["unchanged"] = list(exports)
        recorded["note"] = UNCHANGED
    return recorded


#: Said when an edit renders every export byte for byte as it was.
UNCHANGED = (
    "Every export came out byte for byte as it was: this edit changes nothing "
    "anyone will see or hear (a fade or trim longer than the sound, a value the "
    "render ignores, a step on the wrong selection). Look at the code and the "
    "source's length before submitting; only the recipe changed."
)


def _digests(project: Path, paths: list[str]) -> dict[str, str]:
    """Each path's SHA-256, or {} when any of them is missing."""
    found: dict[str, str] = {}
    for rel in paths:
        path = project / rel
        if not path.is_file():
            return {}
        found[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _checked_document(
    project: Path, tool: str, source: str, exports: list[str], codes: tuple[str, ...]
) -> tuple[ToolModule, str, list[str], Path]:
    """The checks every document edit passes before its app starts.

    Returns the tool module, the source and exports as project paths, and the
    source's path.
    """
    enabled(project, tool)
    module = tools.get(tool)
    if not module.script_ext:
        raise ValueError("Use edit_graph or edit_vector for this application.")
    for code in codes:
        validate_arguments({"code": code})
    # Agents name files the way Godot shows them (res://art/models/x.glb, as a
    # part's resource reads); the recipe and every check use project paths.
    source = source.removeprefix("res://")
    exports = [output.removeprefix("res://") for output in exports]
    target = confined(project, source)
    if target.suffix != module.suffix or Path(source).parts[0] not in {"art", "audio"}:
        raise ValueError("Use the owning tool's native format under art/ or audio/.")
    module.check_destinations(source, exports)
    for output in exports:
        confined(project, output)
        if Path(output).parts[0] not in {"art", "audio"}:
            raise ValueError("Exports belong under art/ or audio/.")
    return module, source, exports, target


def _textures(project: Path, source: str) -> tuple[str, ...]:
    """The texture files a Blender source's last edit wrote."""
    folder = confined(project, texture_dir(source))
    return tuple(
        p.relative_to(project).as_posix() for p in sorted(folder.glob("*.png"))
    )


@dataclass(frozen=True)
class DocumentStep:
    """One edit of a batch: its operation code, its recipe note, and files it reads."""

    code: str
    note: str
    inputs: tuple[str, ...] = ()


@dataclass
class StepsOutcome:
    """What a batch of edits did: a result per finished step, and the failure."""

    #: As edit_document returns for each step, in order.
    results: list[dict[str, Any]] = field(default_factory=list)
    #: The step that failed (0-based), or None when every step finished.
    failed: int | None = None
    error: str = ""
    #: Wall-clock seconds of the one Blender run.
    seconds: float = 0.0


#: A batch runs inside one gateway call, which waits at most 900 s.
BATCH_TIMEOUT = 840


async def edit_document_steps(
    project: Path,
    source: str,
    steps: list[DocumentStep],
    exports: list[str],
    *,
    keep_macro_shape_keys: bool | None = None,
    shape_keys: blender_export.ExportShapeKeys | None = None,
) -> StepsOutcome:
    """Apply several Blender edits to one document in one Blender process.

    Every step gets the recipe script, preimage, note and recorded fingerprints
    it would get from its own edit_document call, in order: the earlier steps'
    recipes go to recipes/history (where the next edit would move them) and the
    last finished step's recipe is the current one, so `joinery rebuild`
    replays the same thing either way. A failing step stops the batch; its
    files are put back as the step before left them, and nothing after it runs.
    """
    if not steps:
        raise ValueError("A batch has at least one step.")
    tool = "blender"
    module, source, exports, target = _checked_document(
        project, tool, source, exports, tuple(step.code for step in steps)
    )
    if not isinstance(module, blender.Blender):
        raise TypeError("Batched edits run in Blender.")
    textures = _textures(project, source)
    _refuse_dropping_audio_edits(project, source, (source, *exports))
    _replacements(project, (source, *exports, *textures))
    for step in steps:
        for extra in step.inputs:
            if not confined(project, extra).is_file():
                raise ValueError(f"{extra} is not a file in this project.")
    recipe_dir = project / "recipes"
    recipe_dir.mkdir(exist_ok=True)
    base = store_preimage(project, target) if target.is_file() else ""
    for output in [source, *exports]:
        confined(project, output).parent.mkdir(parents=True, exist_ok=True)
    chosen = _export_shape_keys(project, source, keep_macro_shape_keys, shape_keys)
    batch = uuid.uuid4().hex
    ids = [uuid.uuid4().hex for _ in steps]
    scripts = [f"recipes/{identifier}{module.script_ext}" for identifier in ids]
    marks = [blender_batch.placeholder(batch, index) for index in range(len(steps))]
    scratch = f"recipes/.batch-{batch}"
    plan = {
        "source": source,
        "base": base,
        "exports": exports,
        "textures": texture_dir(source),
        "preimages": PREIMAGE_DIR,
        "scratch": scratch,
        "steps": [
            {
                "script": script,
                "placeholder": mark,
                "text": module.script(
                    source,
                    base if index == 0 else mark,
                    step.code,
                    exports,
                    shape_keys=chosen,
                ),
            }
            for index, (step, script, mark) in enumerate(
                zip(steps, scripts, marks, strict=True)
            )
        ],
    }
    # Recording the finished steps can still fail (the lock): then the whole
    # batch is put back, so the model, its recipe and its lock entry agree.
    undo = Undo(project, (source, *exports), (texture_dir(source),))
    folder = project / scratch
    folder.mkdir(parents=True)
    try:
        (folder / "plan.json").write_text(json.dumps(plan))
        (folder / "driver.py").write_text(blender_batch.DRIVER)
        try:
            ran = await run_offline(
                [
                    "blender",
                    "--background",
                    "--factory-startup",
                    "--python",
                    f"{scratch}/driver.py",
                    "--",
                    f"{scratch}/plan.json",
                ],
                project,
                BATCH_TIMEOUT,
            )
        except BaseException:
            _undo_step(project, source, exports, scratch, 0, base)
            for script in scripts:
                (project / script).unlink(missing_ok=True)
            raise
        progress = blender_batch.read_progress(ran.stdout)
        finished = 0
        while finished in progress.done:
            finished += 1
        outcome = StepsOutcome(seconds=ran.seconds)
        if finished < len(steps):
            outcome.failed = finished
            outcome.error = progress.error or (
                "Blender stopped without saying why: " + ran.output[-1500:]
            )
            if finished in progress.bases:
                _undo_step(
                    project,
                    source,
                    exports,
                    scratch,
                    finished,
                    progress.bases[finished],
                )
            for script in scripts[finished:]:
                (project / script).unlink(missing_ok=True)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if finished:
        # Godot imports portable GLB exports, never the .blend beside them.
        (target.parent / ".gdignore").write_text("")
    try:
        _record_steps(
            project, source, exports, steps, ids, scripts, progress, finished, outcome
        )
        if finished:
            last = finished - 1
            recipe = _step_recipe(
                tool, source, exports, steps[last], scripts[last], progress, last
            )
            result = await _record(
                project, recipe, recipe_dir / (ids[last] + ".toml"), replaces=textures
            )
            result.update(
                _export_facts(project, exports, progress.output.get(last, ""))
            )
            outcome.results.append(result)
    except BaseException:
        undo.restore()
        raise
    return outcome


def _step_recipe(
    tool: str,
    source: str,
    exports: list[str],
    step: DocumentStep,
    script: str,
    progress: blender_batch.Progress,
    index: int,
) -> Recipe:
    """The recipe of one finished step of a batch."""
    done = progress.done[index]
    step_base = progress.bases.get(index, "")
    return Recipe(
        tool=tool,
        script=script,
        outputs=(source, *exports, *done["textures"]),
        sources=tuple(dict.fromkeys(((step_base,) if step_base else ()) + step.inputs)),
        note=step.note,
    )


def _record_steps(
    project: Path,
    source: str,
    exports: list[str],
    steps: list[DocumentStep],
    ids: list[str],
    scripts: list[str],
    progress: blender_batch.Progress,
    finished: int,
    outcome: StepsOutcome,
) -> None:
    """Write each finished step but the last to history, where the next step's
    edit would have moved its recipe."""
    history = project / HISTORY_DIR
    for index in range(finished - 1):
        done = progress.done[index]
        recipe = _step_recipe(
            "blender", source, exports, steps[index], scripts[index], progress, index
        )
        history.mkdir(parents=True, exist_ok=True)
        path = history / (ids[index] + ".toml")
        path.write_text(
            recipe_text(recipe.model_copy(update={"expect": done["expect"]}))
        )
        result: dict[str, Any] = {
            "source": source,
            "outputs": list(recipe.outputs),
            "recipe": path.relative_to(project).as_posix(),
            "status": "staged",
            "requires_review": True,
        }
        result.update(_export_facts(project, [], progress.output.get(index, "")))
        outcome.results.append(result)


def _undo_step(
    project: Path,
    source: str,
    exports: list[str],
    scratch: str,
    index: int,
    base: str,
) -> None:
    """Put back what a failed batch step replaced: its document, exports and textures."""
    backup = project / scratch / str(index)
    if not backup.is_dir():
        return
    if base:
        copy_file(project / base, project / source)
    else:
        (project / source).unlink(missing_ok=True)
    for number, export in enumerate(exports):
        saved = backup / f"export-{number}"
        if saved.is_file():
            copy_file(saved, project / export)
        else:
            (project / export).unlink(missing_ok=True)
    folder = confined(project, texture_dir(source))
    for png in folder.glob("*.png"):
        png.unlink()
    for png in sorted((backup / "textures").glob("*.png")):
        copy_file(png, folder / png.name)


def recorded_shape_keys(project: Path, source: str) -> blender_export.ExportShapeKeys:
    """The shape-key choice of the recipe that last made `source` (the default if none)."""
    for path in recipe_paths(project):
        recipe = load_recipe(path)
        script = project / recipe.script
        if source in recipe.outputs and script.is_file():
            found = blender_export.recorded_shape_keys(script.read_text())
            return found or blender_export.ExportShapeKeys()
    return blender_export.ExportShapeKeys()


def _export_shape_keys(
    project: Path,
    source: str,
    keep_macros: bool | None,
    chosen: blender_export.ExportShapeKeys | None,
) -> blender_export.ExportShapeKeys:
    """The export's shape-key choice: the one given, else the model's own, with
    `keep_macros` changed when it is given."""
    if chosen is not None:
        return chosen
    recorded = recorded_shape_keys(project, source)
    if keep_macros is None:
        return recorded
    return blender_export.ExportShapeKeys(keep_macros=keep_macros, keep=recorded.keep)


def _export_facts(project: Path, exports: list[str], output: str) -> dict[str, Any]:
    """Shape keys baked or kept per mesh, and each GLB's meshes and morph targets."""
    facts: dict[str, Any] = {}
    rows = blender_export.shape_rows(output)
    if rows:
        facts["shape_keys"] = rows
    # What a typed operation reports of itself (a refitted rig, values set).
    done = []
    for line in output.splitlines():
        for prefix in ("JOINERY_PART_OP:", "JOINERY_MODEL_OP:"):
            if line.startswith(prefix):
                with contextlib.suppress(ValueError):
                    done.append(json.loads(line.removeprefix(prefix)))
    if done:
        facts["operation"] = done
    animations = blender_anim.animation_rows(output)
    if animations:
        facts["animations"] = animations
    models = {}
    for path in exports:
        if Path(path).suffix == ".glb" and (project / path).is_file():
            with contextlib.suppress(ValueError, KeyError, IndexError):
                models[path] = blender_export.glb_meshes(project / path)
    if models:
        facts["models"] = models
    return facts


def _read_file(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _top_files(folder: Path) -> set[str]:
    return (
        {p.name for p in folder.iterdir() if p.is_file()} if folder.is_dir() else set()
    )


class Undo:
    """Put an operation's files, recipes and joinery.lock back as they were.

    A tool operation writes its document, its exports and (Blender) the model's
    texture folder, then records a recipe, moves the recipes it replaces to
    history and rewrites the lock. When any of that fails part way, the model's
    files, its recipe and its lock entry would no longer agree, and every later
    operation on it is refused. :meth:`restore` puts back each file and folder
    named here, removes recipe files made since, returns recipes moved to
    history, and restores the lock byte for byte. A model's import settings,
    written beside its first export, count as part of it.
    """

    def __init__(
        self, project: Path, files: tuple[str, ...], folders: tuple[str, ...] = ()
    ) -> None:
        self.project = project
        files = (
            *files,
            *(f + ".import" for f in files if Path(f).suffix.lower() in GLTF_SUFFIXES),
        )
        self.files = {f: _read_file(project / f) for f in files}
        self.existed = {f for f in files if (project / f).exists()}
        self.folders = {
            folder: {
                p.relative_to(project).as_posix(): p.read_bytes()
                for p in sorted((project / folder).rglob("*"))
                if p.is_file()
            }
            for folder in folders
        }
        self.recipes = _top_files(project / "recipes")
        self.history = _top_files(project / HISTORY_DIR)
        self.lock = _read_file(project / LOCK_NAME)

    def restore(self) -> None:
        project = self.project
        recipes = project / "recipes"
        for name in _top_files(recipes) - self.recipes:
            (recipes / name).unlink(missing_ok=True)
        for name in _top_files(project / HISTORY_DIR) - self.history:
            moved = project / HISTORY_DIR / name
            if name in self.recipes:
                # Replaced by the recipe this operation recorded: current again.
                moved.replace(recipes / name)
            else:
                # Written straight to history (an earlier step of a batch).
                moved.unlink(missing_ok=True)
        for folder, kept in self.folders.items():
            path = project / folder
            if path.is_dir():
                for found in path.rglob("*"):
                    rel = found.relative_to(project).as_posix()
                    if found.is_file() and rel not in kept:
                        found.unlink()
            for rel, content in kept.items():
                (project / rel).parent.mkdir(parents=True, exist_ok=True)
                (project / rel).write_bytes(content)
        for relative, data in self.files.items():
            path = project / relative
            if data is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            elif relative in self.existed:
                continue
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        lock = project / LOCK_NAME
        if self.lock is not None:
            lock.write_bytes(self.lock)
        else:
            lock.unlink(missing_ok=True)


def _refuse_dropping_audio_edits(
    project: Path, source: str, outputs: tuple[str, ...]
) -> None:
    """Refuse a plain render over a sound whose recipe carries audio edits.

    The new recipe would replace the edited one and silently lose the trims,
    fades and loudness changes. The sound tools re-apply them instead.
    """
    from servers.sound.ops import recipe_edits

    for path in recipe_paths(project):
        old = load_recipe(path)
        if not set(old.outputs) & set(outputs):
            continue
        script = project / old.script
        if script.is_file() and recipe_edits(script.read_text()):
            raise ValueError(
                f"{source} has audio edits that a new render would drop. "
                "Use set_surge_parameters to change a Surge XT patch and keep "
                "them, or process_audio to edit the sound."
            )


def _replacements(
    project: Path, outputs: tuple[str, ...], replaces: tuple[str, ...] = ()
) -> list[Path]:
    """The recipes whose outputs this recipe makes again, which it replaces.

    `replaces` are further files the operation took over that it no longer
    makes: a Blender model's texture files from before an edit that removed an
    image. The recipe that made them is replaced all the same; they are gone
    from disk, so the lock drops them.
    """
    replacements = []
    covered = set(outputs) | set(replaces)
    for previous in recipe_paths(project):
        old = load_recipe(previous)
        if set(old.outputs) & covered:
            if not set(old.outputs) <= covered:
                raise ValueError(
                    "Refresh every output of the existing source recipe: "
                    + ", ".join(old.outputs)
                )
            replacements.append(previous)
    return replacements


def _fingerprint_runner() -> Runner:
    """Where the recipe's fingerprints are measured: the warm worker and its
    running Blender when this server has one (joinery.warm_blender), else a
    fresh container."""
    from joinery import warm_blender

    if os.environ.get("JOINERY_WORKER_SCOPE") and warm_blender.enabled():
        from joinery.warm_workers import WarmRunner

        return WarmRunner(asyncio.get_running_loop(), IMAGE)
    return SandboxRunner(image=IMAGE)


def _lock(project: Path) -> None:
    """Write joinery.lock from the recipes; refuse when provenance is missing."""
    problems = write_lock(project)
    if problems:
        raise ValueError("Provenance recording failed: " + "; ".join(problems))


async def _record(
    project: Path,
    recipe: Recipe,
    path: Path,
    *,
    lock: bool = True,
    replaces: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Write a recipe with its measured outputs, then the lock file.

    `lock=False` leaves the lock to the caller, which records several recipes
    whose files appeared together (a batch) and writes the lock once after.
    `replaces` are files the operation took over (see :func:`_replacements`).
    """
    replacements = [
        p for p in _replacements(project, recipe.outputs, replaces) if p != path
    ]
    rows = [
        "version = 1",
        f"tool = {json.dumps(recipe.tool)}",
        f"script = {json.dumps(recipe.script)}",
        f"outputs = {json.dumps(recipe.outputs)}",
        f"sources = {json.dumps(recipe.sources)}",
        f"note = {json.dumps(recipe.note)}",
    ]
    if recipe.seed is not None:
        rows.append(f"seed = {recipe.seed}")
    path.write_text("\n".join(rows) + "\n")
    await asyncio.to_thread(record, path, project, _fingerprint_runner())
    if replacements:
        history = project / "recipes/history"
        history.mkdir(exist_ok=True)
        for previous in replacements:
            previous.rename(history / previous.name)
    if lock:
        _lock(project)
    return {
        "source": recipe.outputs[0],
        "outputs": list(recipe.outputs),
        "recipe": path.relative_to(project).as_posix(),
        "status": "staged",
        "requires_review": True,
    }


async def edit_graph(
    project: Path,
    source: str,
    graph: dict[str, Any],
    output_directory: str,
    note: str = "",
) -> dict[str, Any]:
    enabled(project, "material-maker")
    target = confined(project, source)
    output = confined(project, output_directory)
    if (
        target.suffix != ".ptex"
        or Path(source).parts[0] != "art"
        or Path(output_directory).parts[0] != "art"
    ):
        raise ValueError(
            "Material Maker uses a native .ptex under art/ and an art/ export directory."
        )
    validate_arguments(graph)
    stored, runtime, inputs = material_maker.image_inputs(
        project, graph, source, output_directory
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    code = material_maker.graph_code(source, stored, output_directory, runtime)
    return await _scripted_export(
        project,
        "material-maker",
        source,
        code,
        output_directory,
        sources=inputs,
        note=note,
    )


async def edit_vector(
    project: Path, source: str, svg: str, export: str, note: str = ""
) -> dict[str, Any]:
    enabled(project, "inkscape")
    for relative in (source, export):
        if Path(relative).parts[0] != "art":
            raise ValueError("Vector source and exports belong under art/.")
        confined(project, relative).parent.mkdir(parents=True, exist_ok=True)
    if Path(source).suffix != ".svg" or Path(export).suffix != ".png":
        raise ValueError("Use native SVG and a PNG export.")
    validate_arguments(svg)
    inkscape.check_svg(svg)
    code = inkscape.vector_code(source, svg, export)
    return await _scripted_export(
        project, "inkscape", source, code, str(Path(export).parent), [export], note=note
    )


async def _scripted_export(
    project: Path,
    tool: str,
    source: str,
    code: str,
    directory: str,
    outputs: list[str] | None = None,
    sources: tuple[str, ...] = (),
    note: str = "",
) -> dict[str, Any]:
    before = _export_inventory(project, directory) if outputs is None else {}
    previous_outputs: set[str] = set()
    if outputs is None:
        for path in recipe_paths(project):
            previous = load_recipe(path)
            if previous.tool == tool and source in previous.outputs:
                previous_outputs.update(previous.outputs)
    identifier = hashlib.sha256((source + code).encode()).hexdigest()[:32]
    script = project / "recipes" / (identifier + ".py")
    script.parent.mkdir(exist_ok=True)
    # A failed run or recording puts the source, its exports, the recipes and
    # the lock back as they were.
    undo = (
        Undo(project, (source,), (directory,))
        if outputs is None
        else Undo(project, (source, *outputs))
    )
    try:
        script.write_text(code)
        result = await run_offline(
            ["python3", script.relative_to(project).as_posix()], project
        )
        if not result.ok:
            raise RuntimeError(result.output[-2000:])
        if outputs is None:
            after = _export_inventory(project, directory)
            outputs = sorted(
                relative
                for relative, digest in after.items()
                if relative != source
                and (before.get(relative) != digest or relative in previous_outputs)
            )
        if not outputs:
            raise ValueError("The application produced no exports.")
        recipe = Recipe(
            tool=tool,
            script=script.relative_to(project).as_posix(),
            outputs=(source, *outputs),
            sources=sources,
            note=note,
        )
        return await _record(project, recipe, script.with_suffix(".toml"))
    except BaseException:
        undo.restore()
        raise


def _export_inventory(project: Path, directory: str) -> dict[str, str]:
    outputs = {}
    for path in confined(project, directory).rglob("*"):
        if path.is_file():
            relative = path.relative_to(project).as_posix()
            target = confined(project, relative)
            with target.open("rb") as output:
                outputs[relative] = hashlib.file_digest(output, "sha256").hexdigest()
    return outputs
