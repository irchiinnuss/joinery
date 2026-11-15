"""The checks a change passes before the user sees it, as problems rather than errors.

The agent hands its change over with ``submit_for_review``; every check here
runs then, and each failure comes back to the agent as a ``Problem`` it can fix
in the same turn. Nothing here raises for a failed check, and nothing here
decides what happens next: that is the turn controller's job.
"""

from __future__ import annotations

import asyncio
import platform
import re
import secrets
import shutil
import tomllib
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from joinery import (
    complex_models,
    reasons,
    references,
    replays,
    saves,
    scene_variants,
)
from joinery import (
    scene_tests as scene_cache,
)
from joinery.art_approval import approved
from joinery.art_approval import fingerprint as bible_fingerprint
from joinery.authoring import run_offline
from joinery.bible_rules import check_rules, load_rules, ui_resources
from joinery.bible_schema import REFERENCE_DIR
from joinery.catalog import load_catalog
from joinery.deps import check_deps
from joinery.lockfile import check_assets
from joinery.policy import load_denylist
from joinery.puzzle import check as puzzle_check
from joinery.runner import Result
from joinery.transactions import DIRECTORY, FileTransaction, clone_tree
from joinery.turn import Problem, scene_route

if TYPE_CHECKING:
    from joinery.gateway import ProjectGateway

PROTECTED = {
    "joinery.toml",
    ".mcp.json",
    "policy",
    ".claude",
    ".codex",
    ".gemini",
    ".opencode",
}
#: Asset files wherever they land in the project: a change to one needs an
#: approved art bible and passes the provenance gate, like art/ and audio/.
ASSET_SUFFIXES = frozenset(
    {
        ".blend",
        ".glb",
        ".gltf",
        ".fbx",
        ".obj",
        ".dae",
        ".kra",
        ".xcf",
        ".psd",
        ".aseprite",
        ".ase",
        ".ptex",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".svg",
        ".bmp",
        ".tga",
        ".gif",
        ".exr",
        ".hdr",
        ".tif",
        ".tiff",
        ".dds",
        ".ktx",
        ".ktx2",
        ".wav",
        ".ogg",
        ".mp3",
        ".flac",
        ".opus",
        ".aif",
        ".aiff",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".mp4",
        ".webm",
        ".ogv",
    }
)
#: Files Godot parses on import: a change to one is import-checked.
GODOT_SUFFIXES = frozenset({".gd", ".tscn", ".tres", ".gdshader", ".glb"})
#: GUT's command-line runner; a project with it and a test folder runs its tests.
GUT_RUNNER = "addons/gut/gut_cmdln.gd"
TEST_DIRS = ("test", "tests")
OFFLINE_SECONDS = 180
#: SceneTree tests (``extends SceneTree`` under tests/): how many run, and how long each
#: may take on a worker that runs natively. ``[checks] scene_test_seconds`` sets it.
SCENE_TESTS_MAX = 20
SCENE_TEST_SECONDS = 120
#: The worker is linux/amd64; on an arm64 host it runs emulated, several times slower.
EMULATED_FACTOR = 2
_SCENE_TREE = re.compile(
    r'^[ \t]*(?:class_name[ \t]+\w+[ \t]+)?extends[ \t]+"?SceneTree"?[ \t]*(?:#.*)?$',
    re.MULTILINE,
)
#: A SceneTree test's own failure line ("FAIL: the door stayed shut").
_FAIL_LINE = re.compile(r"^\s*FAIL(?:ED)?\b", re.MULTILINE)
#: How a SceneTree test without a ``## Run:`` line runs: the blueprints' test
#: harness convention (fixed 60 fps, the game started, a settings file of its own).
SCENE_TEST_FPS = "60"
SCENE_TEST_ARGS = ("--autostart", "--settings-path=<file>")
#: The ``## Run: godot ... -- <args>`` line a test names its own command with.
_RUN_LINE = re.compile(r"^##\s*Run:\s*(.*)$")
#: One half of a test that needs a second game process (``## Client: --join=...``).
_PAIR_LINE = re.compile(r"^##\s*(?:Host|Client|Server):", re.MULTILINE)
_OPENXR_ON = re.compile(r"^openxr/enabled=true$", re.MULTILINE)


def is_reference(path: str) -> bool:
    """Under art/reference/: the user's own boards, allowed before approval.

    `art/reference/../hero.blend` is not: PurePath does not resolve `..`.
    """
    pure = PurePosixPath(path.replace("\\", "/").removeprefix("res://"))
    return ".." not in pure.parts and pure.is_relative_to(REFERENCE_DIR)


def approved_in(project: Path, stage: Path) -> bool:
    """The project's bible is approved and the stage carries that same bible."""
    try:
        return approved(project) and bible_fingerprint(project) == bible_fingerprint(
            stage
        )
    except ValueError:
        return False


def ui_changes(stage: Path, changes: list[dict[str, Any]]) -> bool:
    """Changed UI resources that the bible's accessibility rules check."""
    try:
        rules = load_rules(stage)
    except ValueError:
        return False  # lint reports invalid rules; they cannot block UI work
    files = [c["path"] for c in changes if DIRECTORY not in {c["before"], c["after"]}]
    return bool(ui_resources(rules, files))


def _paths(lines: list[str]) -> list[str]:
    """The file each "path: what is wrong" line names."""
    return list(dict.fromkeys(line.split(":", 1)[0] for line in lines if ":" in line))


def _broken(output: str, ok: bool) -> bool:
    return not ok or "SCRIPT ERROR:" in output or "Parse Error:" in output


def gut_dirs(stage: Path) -> list[str]:
    """The project's GUT test folders, when it has GUT; otherwise none."""
    if not (stage / GUT_RUNNER).is_file():
        return []
    return [name for name in TEST_DIRS if (stage / name).is_dir()]


def is_scene_tree_test(path: Path) -> bool:
    """A script whose own class is SceneTree: Godot runs it with ``-s``."""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return False
    return _SCENE_TREE.search(head) is not None


def scene_tree_tests(stage: Path) -> list[str]:
    """The project's SceneTree tests: scripts under ``tests/`` (or ``test/``) that
    extend SceneTree, by project path. Each runs headless on its own and passes
    when it quits with 0 and prints no ``FAIL`` line and no script error.

    The game's own tests (``tests/test_door.gd``) come before those in deeper
    folders (a feature's ``tests/features/...``), so the limit never drops them.
    A test that is one half of two game processes (``## Client: --join=...``)
    cannot run on its own and is left out."""
    found: list[str] = []
    for name in TEST_DIRS:
        folder = stage / name
        if not folder.is_dir():
            continue
        for path in folder.rglob("*.gd"):
            if path.is_file() and is_scene_tree_test(path):
                if scene_test_args(stage, path.relative_to(stage).as_posix()) is None:
                    continue
                found.append(path.relative_to(stage).as_posix())
    found.sort(key=lambda rel: (rel.count("/"), rel))
    return found[:SCENE_TESTS_MAX]


def _header(path: Path) -> list[str]:
    """The ``##`` comment lines at the top of a script."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:60]
    except OSError:
        return []
    return [line for line in lines if line.startswith("##")]


def scene_test_args(stage: Path, script: str) -> list[str] | None:
    """Godot's arguments for one SceneTree test, as its own ``## Run:`` line names
    them (``--fixed-fps 60 ... -- --autostart --settings-path=<file>``; optional
    ``[...]`` parts left out), else the harness convention. ``<file>`` stays for
    the runner to fill. None for a test that needs a second process or a value
    the check cannot know (``--port=<n>`` in the Run line)."""
    header = _header(stage / script)
    command = ""
    for index, line in enumerate(header):
        match = _RUN_LINE.match(line)
        if match is None:
            continue
        parts = [match.group(1)]
        for more in header[index + 1 :]:
            text = more.lstrip("#").strip()
            if not text or not more.startswith("##  "):
                break
            parts.append(text)
        command = " ".join(parts)
        break
    if not command:
        if _PAIR_LINE.search("\n".join(header)):
            return None
        return ["--fixed-fps", SCENE_TEST_FPS, "--", *SCENE_TEST_ARGS]
    tokens = re.sub(r"\[[^\]]*\]", " ", command).split()
    args: list[str] = []
    if "--fixed-fps" in tokens:
        at = tokens.index("--fixed-fps")
        args += ["--fixed-fps", tokens[at + 1] if at + 1 < len(tokens) else "60"]
    if "--" in tokens:
        extra = tokens[tokens.index("--") + 1 :]
        if any(re.search(r"<(?!file>)[^>]*>", arg) for arg in extra):
            return None
        args += ["--", *extra]
    return args


def scene_test_argv(stage: Path, script: str, settings: str) -> list[str]:
    """The whole Godot command for one SceneTree test in the offline worker."""
    args = scene_test_args(stage, script) or []
    project = stage / "project.godot"
    xr_off = (
        ["--xr-mode", "off"]
        if project.is_file() and _OPENXR_ON.search(project.read_text(errors="replace"))
        else []
    )
    filled = [arg.replace("<file>", settings) for arg in args]
    if "--" in filled:
        at = filled.index("--")
        before, after = filled[:at], filled[at:]
    else:
        before, after = filled, []
    return [
        "godot", "--headless", *xr_off, "--path", "/work", *before,
        "-s", "res://" + script, *after,
    ]  # fmt: skip


@dataclass
class Validation:
    changes: list[dict[str, str]] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    #: The art gate when asset work needs an approved art bible first.
    gate: dict[str, Any] | None = None
    #: Checks that did not apply to this change ("tests" without GUT).
    skipped: list[str] = field(default_factory=list)
    #: The checks that ran, in plain words ("art bible", "tests"), for the review.
    ran: list[str] = field(default_factory=list)
    #: Worth knowing but not the change's fault, so never a hold: tests that
    #: already failed before the change.
    info: list[str] = field(default_factory=list)
    #: The tests ``info`` says already failed, by project path.
    already_failing: list[str] = field(default_factory=list)


def keep_refusal(
    project: Path, tx: FileTransaction, changes: list[dict[str, str]]
) -> str:
    """Why an unfinished change may not wait for review ("" when it may): the
    checks no agent fix or user click gets around. Project policy files, asset
    work before the art bible is approved, an asset without a provenance entry
    and a denied dependency. The rest (import, lint, tests, the reviewer) are left
    for the user, and the kept change says they did not run."""
    protected = [c["path"] for c in changes if Path(c["path"]).parts[0] in PROTECTED]
    if protected:
        return "it changes project policy files (" + ", ".join(protected[:3]) + ")."
    assets = [
        c["path"]
        for c in changes
        if (
            Path(c["path"]).parts[0] in {"art", "audio"}
            and c["path"] != "art/bible.md"
            or Path(c["path"]).suffix.lower() in ASSET_SUFFIXES
        )
        and DIRECTORY not in {c["before"], c["after"]}
    ]
    if assets:
        if not approved_in(project, tx.stage) and not all(
            is_reference(p) for p in assets
        ):
            return "asset work needs the art bible approved first."
        problems = check_assets(tx.stage, load_catalog())
        if problems:
            return "asset provenance: " + "; ".join(problems[:3])
    problems = check_deps(tx.stage, load_denylist())
    if problems:
        return "dependencies: " + "; ".join(problems[:3])
    return ""


async def validate(
    gateway: ProjectGateway, tx: FileTransaction, live_scenes: set[str]
) -> Validation:
    """Every check for the staged change in ``tx``.

    The protected-path and live-conflict problems are final: no fix in this turn
    passes them. The art gate is the user's call, so it is returned as ``gate``.
    The rest are problems the agent can fix and submit again.
    """
    changes = tx.diff()
    found = Validation(changes=changes)
    if not changes:
        return found
    protected = [c["path"] for c in changes if Path(c["path"]).parts[0] in PROTECTED]
    if protected:
        found.problems.append(
            Problem(
                "protected",
                "Model tools cannot change project policy or MCP configuration. "
                "Use project setup.",
                protected,
                fixable=False,
            )
        )
        return found
    conflict = [
        c["path"]
        for c in changes
        if Path(c["path"]).suffix == ".tscn"
        and (
            "res://" + c["path"] in live_scenes
            or scene_route(c["path"], gateway.editor_context) == "live"
        )
    ]
    if conflict:
        found.problems.append(
            Problem(
                "live_conflict",
                "This turn changed the same scene live in the editor and as a saved "
                "file; one change can't do both.",
                conflict,
                fixable=False,
            )
        )
        return found
    # A scene and the model or texture it loads are decided together: never
    # review a scene that points at a file nobody is adding.
    gaps = references.missing(
        tx.stage,
        [c["path"] for c in changes if c["after"] not in {"", DIRECTORY}],
    )
    for scene, paths in references.users_of(
        tx.stage, [c["path"] for c in changes if not c["after"]]
    ).items():
        gaps.setdefault(scene, []).extend(paths)
    if gaps:
        found.problems.append(
            Problem(
                "references",
                references.refusal(gaps),
                sorted(gaps),
                items=reasons.references(gaps),
            )
        )
    found.ran.append("references")
    # Scene variants (inherited scenes) set properties only on nodes their base has.
    broken_variants = scene_variants.check(
        tx.stage, [c["path"] for c in changes if c["after"] not in {"", DIRECTORY}]
    )
    if broken_variants:
        found.problems.append(
            Problem(
                "variants",
                "A scene variant doesn't fit its base: " + " ".join(broken_variants),
                _paths(broken_variants),
                items=[reasons.variant(line) for line in broken_variants],
            )
        )
    # Grid puzzle levels and card deals must be winnable (ADR 0069).
    puzzles = puzzle_check.staged(
        tx.stage, [c["path"] for c in changes if c["after"] not in {"", DIRECTORY}]
    )
    if puzzles is not None:
        found.ran.append("puzzles")
        unsolvable = [f for f in puzzles.findings if f.severity == "error"]
        if unsolvable:
            found.problems.append(
                Problem(
                    "puzzles",
                    "A puzzle can't be won: " + " ".join(f.text for f in unsolvable),
                    list(dict.fromkeys(f.file for f in unsolvable)),
                    items=[f.reason() for f in unsolvable],
                )
            )
    # Complex objects are Blender (or kit) models, not piles of placeholder shapes.
    if complex_models.setting(gateway.project) == complex_models.BLENDER and any(
        c["path"].endswith(".tscn") and c["after"] for c in changes
    ):
        shapes = complex_models.check_scenes(
            gateway.project, tx.stage, tx.original, changes
        )
        if shapes is not None:
            found.problems.append(shapes)
    asset_changes = [
        change["path"]
        for change in changes
        if (
            Path(change["path"]).parts[0] in {"art", "audio"}
            and change["path"] != "art/bible.md"
            or Path(change["path"]).suffix.lower() in ASSET_SUFFIXES
        )
        and DIRECTORY not in {change["before"], change["after"]}
    ]
    bible_ok = approved_in(gateway.project, tx.stage)
    rules = False
    if asset_changes:
        # Reference boards come before the bible; they still need a lock entry.
        if not bible_ok and not all(is_reference(p) for p in asset_changes):
            found.gate = gateway.art_gate_event()
            return found
        problems = check_assets(tx.stage, load_catalog())
        found.ran.append("provenance")
        if problems:
            found.problems.append(
                Problem(
                    "assets",
                    "Asset validation failed: " + "; ".join(problems),
                    _paths(problems),
                    items=[reasons.provenance(line) for line in problems],
                )
            )
        rules = bible_ok
    else:
        # UI themes and scenes are checked for text contrast and size.
        rules = bible_ok and ui_changes(tx.stage, changes)
    problems = check_deps(tx.stage, load_denylist())
    found.ran.append("dependencies")
    if problems:
        found.problems.append(
            Problem(
                "dependencies",
                "Dependency validation failed: " + "; ".join(problems),
                _paths(problems),
                items=[reasons.dependency(line) for line in problems],
            )
        )
    godot = any(
        Path(change["path"]).suffix in GODOT_SUFFIXES
        or change["path"] == "project.godot"
        for change in changes
    )
    tests = gut_dirs(tx.stage) if godot else []
    # SceneTree tests run too, with or without GUT: one convention for done-when tests.
    scene_tests = scene_tree_tests(tx.stage) if godot else []
    if godot and not tests and not scene_tests:
        found.skipped.append("tests")
    importing = godot and not gateway.import_current(tx)
    # Recorded play sessions replay on a change to the game (ADR 0059).
    replaying = godot and replays.config(tx.stage)["replays"]
    # Kept saves load in a change that touches what saves are made of (ADR 0066).
    saving = (
        godot
        and saves.config(tx.stage)["saves"]
        and saves.relevant(tx.stage, tx.original, changes)
    )
    found.ran += [
        name
        for name, on in (
            ("art bible", rules),
            ("Godot import", importing or bool(tests) or bool(scene_tests)),
            ("tests", bool(tests) or bool(scene_tests)),
            ("replays", replaying),
            ("saves", saving),
        )
        if on
    ]
    found.problems += await checks(
        tx,
        changes,
        rules=rules,
        godot=importing,
        tests=tests,
        scene_tests=scene_tests,
        span=gateway.span,
        imported=lambda: gateway.mark_imported(tx),
        replays_on=replaying,
        saves_on=saving,
        info=found.info,
        failing=found.already_failing,
    )
    return found


async def checks(
    tx: FileTransaction,
    changes: list[dict[str, str]],
    *,
    rules: bool,
    godot: bool,
    tests: list[str] | None = None,
    span: Any = None,
    imported: Callable[[], None] | None = None,
    replays_on: bool = False,
    saves_on: bool = False,
    scene_tests: list[str] | None = None,
    info: list[str] | None = None,
    failing: list[str] | None = None,
) -> list[Problem]:
    """The art bible's measured rules beside Godot's import check, the project's tests,
    its recorded replays and its kept saves.

    Each runs in the offline worker and takes seconds. The rules mount the stage
    read-only and run alongside; the tests and replays need the import, so they follow it.
    ``imported`` is told when Godot imported the stage cleanly, so the next check
    of the same stage (a submit after a precheck) can skip that import.
    """

    def timed(phase: str) -> AbstractContextManager[None]:
        return span(phase) if span is not None else nullcontext()

    async def measured() -> list[Problem]:
        if not rules:
            return []
        with timed("review_rules"):
            found = await check_rules(
                tx.stage,
                [change["path"] for change in changes],
                lambda argv, root: worker_run(
                    argv, root, OFFLINE_SECONDS, readonly=True
                ),
            )
        # One problem per finding, each naming its file, so the author sees a
        # list it can work through rather than one long joined line.
        return [
            Problem(
                "art_bible",
                "Look check failed: " + line,
                _paths([line]),
                items=[reasons.art_bible(line)],
            )
            for line in found
        ]

    async def godot_checks() -> list[Problem]:
        found: list[Problem] = []
        testing = bool(tests) or bool(scene_tests)
        if godot or testing:
            with timed("review_import"):
                got = await worker_run(
                    ["godot", "--headless", "--path", "/work", "--editor", "--import"],
                    tx.stage,
                    OFFLINE_SECONDS,
                )
            worker_ready(got)
            if _broken(got.output, got.ok):
                found.append(
                    Problem(
                        "godot_import",
                        "Godot import/script validation failed: "
                        + (got.output[-4000:] or "Godot exited with an error."),
                        items=reasons.godot_import(got.output),
                    )
                )
                return found
            if imported is not None:
                imported()
        if tests:
            found += await run_tests(tx.stage, tests, timed)
        if scene_tests:
            found += await run_scene_tests(
                tx.stage, scene_tests, timed, tx=tx, info=info, failing=failing
            )
        if replays_on:
            found += await run_replays(tx, imported=godot or testing, timed=timed)
        if saves_on:
            found += await run_saves(
                tx, imported=godot or testing or replays_on, timed=timed
            )
        return found

    outcomes = await asyncio.gather(measured(), godot_checks(), return_exceptions=True)
    problems: list[Problem] = []
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
        problems += outcome
    return problems


#: What Docker prints when the offline worker itself can't start.
WORKER_DOWN = (
    "Cannot connect to the Docker daemon",
    "docker daemon is not running",
    "error during connect",
    "Unable to find image",
)


#: Seconds between tries when the offline worker didn't start (a busy Docker).
WORKER_RETRY_DELAYS = (2.0, 4.0, 8.0)


async def worker_run(
    argv: list[str], root: Path, seconds: int, **options: Any
) -> Result:
    """``run_offline``, tried again with backoff while the worker itself can't
    start: one Docker hiccup must not end a check, or the turn with it."""
    got = await run_offline(argv, root, seconds, **options)
    for delay in WORKER_RETRY_DELAYS:
        if got.ok or not any(sign in got.stderr for sign in WORKER_DOWN):
            break
        await asyncio.sleep(delay)
        got = await run_offline(argv, root, seconds, **options)
    return got


def worker_ready(got: Result) -> None:
    """Raise when the offline worker never ran: no change can fix that."""
    if not got.ok and any(sign in got.stderr for sign in WORKER_DOWN):
        raise RuntimeError("Docker isn't running, so Joinery's checks could not start.")


async def run_tests(
    stage: Path, folders: list[str], timed: Any = None
) -> list[Problem]:
    """Run the project's GUT tests; a failure or a timeout is a problem to fix."""
    argv = [
        "godot",
        "--headless",
        "--path",
        "/work",
        "-s",
        "res://" + GUT_RUNNER,
        "-gdir=" + ",".join("res://" + name for name in folders),
        "-ginclude_subdirs",
        "-gexit",
    ]
    with timed("review_tests") if timed is not None else nullcontext():
        got = await worker_run(argv, stage, OFFLINE_SECONDS)
    worker_ready(got)
    if got.ok and "SCRIPT ERROR:" not in got.output:
        return []
    return [
        Problem(
            "tests",
            "The project's tests failed: "
            + (got.output[-4000:] or "GUT exited with an error."),
            items=reasons.tests(got.output),
        )
    ]


def scene_test_seconds(project: Path) -> int:
    """Each SceneTree test's time limit: ``[checks] scene_test_seconds`` when the
    project sets it, otherwise the native limit, scaled up when the worker runs
    emulated (an arm64 host running the amd64 worker)."""
    try:
        raw = tomllib.loads((project / "joinery.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    checks = raw.get("checks", {})
    setting = checks.get("scene_test_seconds") if isinstance(checks, dict) else None
    if isinstance(setting, int) and not isinstance(setting, bool) and setting > 0:
        return setting
    emulated = platform.machine().lower() in {"arm64", "aarch64"}
    return SCENE_TEST_SECONDS * (EMULATED_FACTOR if emulated else 1)


async def run_scene_tests(
    stage: Path,
    scripts: list[str],
    timed: Any = None,
    *,
    tx: FileTransaction | None = None,
    info: list[str] | None = None,
    failing: list[str] | None = None,
) -> list[Problem]:
    """Run each SceneTree test headless (``godot -s``), several at once; one that
    exits non-zero, prints a ``FAIL`` line or a script error, or never quits fails.

    Only a test that passed before the change (or that the change adds) and fails
    now is a problem. One that failed before too goes in ``info`` as already
    failing. Results are cached by the content of the files each test can load
    (``joinery.scene_tests``), so a test the change can't affect never runs again.
    """

    seconds = scene_test_seconds(tx.project if tx is not None else stage)

    async def run(root: Path, script: str) -> Result:
        # A settings file of its own: no test sees another's. A run past the limit
        # raises TimeoutError (the worker is killed); the judge tries it once more.
        settings = f"/tmp/joinery-test-{secrets.token_hex(6)}.cfg"
        got = await worker_run(
            scene_test_argv(root, script, settings),
            root,
            seconds,
            warm=False,
        )
        worker_ready(got)
        return got

    async def baseline() -> Path | None:
        # The project as it was, while the change is still staged.
        if tx is None or tx.original != tx.project:
            return None
        copy = tx.directory / f"baseline-{secrets.token_hex(4)}"
        try:
            await asyncio.to_thread(clone_tree, tx.project, copy)
        except OSError:
            shutil.rmtree(copy, ignore_errors=True)
            return None
        return copy

    async def prepare(root: Path) -> None:
        got = await worker_run(
            ["godot", "--headless", "--path", "/work", "--editor", "--import"],
            root,
            OFFLINE_SECONDS,
            warm=False,
        )
        worker_ready(got)

    with timed("review_scene_tests") if timed is not None else nullcontext():
        verdict = await scene_cache.judge(
            project=tx.project if tx is not None else None,
            stage=stage,
            original=tx.original if tx is not None else None,
            scripts=scripts,
            args=lambda root, script: scene_test_args(root, script) or [],
            run=run,
            passed=scene_test_passed,
            baseline=baseline,
            prepare=prepare,
            seconds=seconds,
        )
    if verdict.already and info is not None:
        info.append(scene_cache.already_note(verdict.already))
    if failing is not None:
        failing.extend(verdict.already)
    return [
        Problem(
            "tests",
            f"The test res://{script} "
            + (
                output
                if script in verdict.timed_out
                else "failed: "
                + (
                    output
                    or "it exited with an error or didn't quit (end it with quit())."
                )
            ),
            [script],
            items=(
                reasons.test_timeout(script, seconds)
                if script in verdict.timed_out
                else reasons.tests(output)
            ),
        )
        for script, output in verdict.broken.items()
    ]


def scene_test_passed(got: Result) -> bool:
    """A SceneTree test's verdict: exit code 0, no FAIL line, no script error."""
    return (
        got.ok
        and not _broken(got.output, got.ok)
        and _FAIL_LINE.search(got.output) is None
    )


async def run_replays(
    tx: FileTransaction, *, imported: bool, timed: Any = None
) -> list[Problem]:
    """Replay ``tests/replays/`` on the stage; one that now diverges or errors is a
    problem naming the frame and what differed (ADR 0059)."""

    async def offline(argv: list[str], root: Path, seconds: int) -> Result:
        got = await worker_run(argv, root, seconds)
        worker_ready(got)
        return got

    with timed("review_replays") if timed is not None else nullcontext():
        outcomes = await replays.check_stage(
            tx.project, tx.stage, offline, OFFLINE_SECONDS * 2, imported=imported
        )
    failed = [o for o in outcomes if not o.ok]
    if not failed:
        return []
    return [
        Problem(
            "replays",
            replays.problems_text(outcomes),
            [f"{replays.FOLDER.as_posix()}/{o.name}{replays.SUFFIX}" for o in failed],
        )
    ]


async def run_saves(
    tx: FileTransaction, *, imported: bool, timed: Any = None
) -> list[Problem]:
    """Load ``tests/saves/`` in the staged game; a save that no longer loads, or loses
    data it loaded with before the change, is a problem naming it (ADR 0066)."""

    async def offline(argv: list[str], root: Path, seconds: int) -> Result:
        got = await worker_run(argv, root, seconds)
        worker_ready(got)
        return got

    with timed("review_saves") if timed is not None else nullcontext():
        outcomes = await saves.check_stage(
            tx.project, tx.stage, offline, OFFLINE_SECONDS * 2, imported=imported
        )
    failed = [o for o in outcomes if not o.ok]
    if not failed:
        return []
    return [
        Problem(
            "saves",
            saves.problems_text(outcomes),
            [o.path for o in failed],
            items=[
                reasons.saves(
                    o.path,
                    "load_failed" if o.status == "failed" else "data_lost",
                    o.detail + (" " + "; ".join(o.lost[:6]) if o.lost else ""),
                    o.data_version,
                )
                for o in failed
            ],
        )
    ]


async def check_and_open(gateway: ProjectGateway) -> dict[str, Any] | None:
    """Check the gateway's stage and open its review, renders done, with no agent turn.

    For scripts that drive the gateway directly (the integration checks): returns
    the ``file_preview``, the art gate when asset work needs approval first (the
    stage is dropped), or None when nothing changed. Any problem raises.
    """
    from joinery.review import render

    if gateway.transaction is None:
        return None
    checked = await gateway.submit_checks(set())
    if not checked.changes:
        await gateway.cancel()
        return None
    if checked.gate is not None:
        gateway.art_gate = None
        await gateway.cancel()
        return checked.gate
    if checked.problems:
        raise ValueError("; ".join(p.message for p in checked.problems))
    preview = gateway.open_review(gateway.transaction.summary)
    gate, gateway.art_gate = gateway.art_gate, None
    if gate is not None:
        preview["art_gate"] = {k: v for k, v in gate.items() if k != "kind"}
    _identifier, _media, jobs = gateway._media_jobs
    gateway._media_jobs = ("", [], [])
    await render(jobs, strict=True)
    preview.pop("media_pending", None)
    return preview
