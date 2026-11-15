"""The submit's SceneTree tests: cached, only those a change can affect, in parallel,
and judged against the project as it was before the change.

A fresh template test that already failed before a change must not hold that
change: only a test that passed before and fails after it is the change's
problem. One that failed before and still fails is said as a note ("already
failing"); a test the change adds has no "before" and must pass.

Each test's result is cached by a key over the content of the files it can
depend on (``dependencies``): the test, the files it names by ``res://`` path or
``uid://``, the classes it uses by ``class_name``, and so on through every text
file reached, starting from ``project.godot`` (autoloads, the main scene, the
game's hooks) for every test. A change to a file outside that set leaves the key
alone, so the test is not run again. When a test's files can't be read the key
covers every file the game can load. The same key before and after the change
means the change can't affect the test: one run answers for both.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from joinery.runner import Result

#: Results by key, per project; Joinery's own cache (gitignored with .joinery/).
CACHE = ".joinery/cache/scene-tests.json"
CACHE_MAX = 600
#: Bump when what a key covers changes, so older results are not reused.
KEY_VERSION = "1"
#: How many tests run at once, each in a worker of its own.
PARALLEL = max(1, min(4, (os.cpu_count() or 2) // 2))
#: Folders whose files no game run reads.
SKIP_DIRS = frozenset(
    {".git", ".godot", ".joinery", ".venv", "__pycache__", ".pytest_cache", "out"}
)
#: Native sources and notes Godot never loads: a change to one affects no test.
NOT_LOADED = frozenset(
    {
        ".blend",
        ".blend1",
        ".kra",
        ".kra~",
        ".xcf",
        ".psd",
        ".aseprite",
        ".ase",
        ".ptex",
        ".md",
        ".txt",
        ".scd",
        ".mid",
        ".midi",
        ".fxp",
        ".lock",
        ".uid",
    }
)
#: Files whose text names other files.
TEXT = frozenset(
    {
        ".gd",
        ".tscn",
        ".tres",
        ".godot",
        ".cfg",
        ".gdshader",
        ".gdshaderinc",
        ".gdextension",
        ".json",
        ".import",
    }
)
#: Read by every run whether named or not.
IMPLICIT = ("project.godot", "default_bus_layout.tres", "override.cfg")
_RES = re.compile(r"res://([^\"'\s)\]}<>|]*)")
_UID = re.compile(r"uid://[0-9a-z]+")
_UID_HEAD = re.compile(
    r'^\[gd_(?:scene|resource)[^\]]*\buid="(uid://[0-9a-z]+)"', re.MULTILINE
)
_CLASS = re.compile(r"^[ \t]*class_name[ \t]+(\w+)", re.MULTILINE)
_WORD = re.compile(r"\b[A-Z]\w*\b")
_FAIL_TAIL = 2000

#: path -> ((size, mtime_ns), sha256), so an unchanged file is hashed once.
_hashes: dict[str, tuple[tuple[int, int], str]] = {}


def file_hash(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    mark = (stat.st_size, stat.st_mtime_ns)
    known = _hashes.get(str(path))
    if known is not None and known[0] == mark:
        return known[1]
    digest = hashlib.sha256()
    try:
        with path.open("rb") as reader:
            for chunk in iter(lambda: reader.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return "missing"
    _hashes[str(path)] = (mark, digest.hexdigest())
    return digest.hexdigest()


class Tree:
    """The files of one project tree a game run can load, and what names what."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: list[str] = []
        for directory, folders, names in os.walk(root):
            folders[:] = [f for f in folders if f not in SKIP_DIRS]
            base = Path(directory)
            for name in names:
                if Path(name).suffix.lower() in NOT_LOADED:
                    continue
                self.files.append((base / name).relative_to(root).as_posix())
        self.files.sort()
        self._set = set(self.files)
        self._classes: dict[str, str] | None = None
        self._uids: dict[str, str] | None = None
        self._refs: dict[str, set[str]] = {}

    def _text(self, rel: str) -> str:
        try:
            return (self.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    @property
    def classes(self) -> dict[str, str]:
        if self._classes is None:
            self._classes = {}
            for rel in self.files:
                if rel.endswith(".gd"):
                    for name in _CLASS.findall(self._text(rel)[:4000]):
                        self._classes.setdefault(name, rel)
        return self._classes

    @property
    def uids(self) -> dict[str, str]:
        if self._uids is None:
            self._uids = {}
            for directory, folders, names in os.walk(self.root):
                folders[:] = [f for f in folders if f not in SKIP_DIRS]
                for name in names:
                    if name.endswith(".uid"):
                        path = Path(directory) / name
                        try:
                            uid = path.read_text(encoding="utf-8").strip()
                        except OSError:
                            continue
                        rel = path.relative_to(self.root).as_posix()[: -len(".uid")]
                        self._uids[uid] = rel
            for rel in self.files:
                if rel.endswith((".tscn", ".tres")):
                    head = _UID_HEAD.search(self._text(rel)[:400])
                    if head:
                        self._uids.setdefault(head.group(1), rel)
        return self._uids

    def _named(self, rel: str) -> set[str]:
        """The project files ``rel``'s text points at."""
        known = self._refs.get(rel)
        if known is not None:
            return known
        found: set[str] = set()
        text = self._text(rel)
        if rel.endswith((".gd", ".gdshader", ".gdshaderinc")):
            # A comment line names files but never loads them.
            text = "\n".join(
                line
                for line in text.splitlines()
                if not line.lstrip().startswith(("#", "//"))
            )
        for target in _RES.findall(text):
            target = target.rstrip(".,;:`'")
            # A bare "res://" is a path check (begins_with) or prose, not a load.
            if not target:
                continue
            if target in self._set:
                found.add(target)
            # A folder (or a path built from one): everything under it.
            prefix = target if target.endswith("/") else target + "/"
            if target.endswith("/") or (self.root / target).is_dir():
                found.update(f for f in self.files if f.startswith(prefix))
            elif target and target not in self._set and "/" in target:
                folder = target.rsplit("/", 1)[0] + "/"
                if any(ch in target for ch in "%{"):
                    found.update(f for f in self.files if f.startswith(folder))
        for uid in _UID.findall(text):
            if (target := self.uids.get(uid)) is not None:
                found.add(target)
        if rel.endswith(".gd"):
            classes = self.classes
            for word in set(_WORD.findall(text)):
                if (target := classes.get(word)) is not None:
                    found.add(target)
        # Godot reads an imported file's settings beside it.
        for target in list(found):
            if target + ".import" in self._set:
                found.add(target + ".import")
        found.discard(rel)
        self._refs[rel] = found
        return found

    def dependencies(self, script: str) -> list[str]:
        """Every file a run of ``script`` can load, by project path."""
        if script not in self._set:
            return list(self.files)
        seen: set[str] = set()
        todo = [script, *(f for f in IMPLICIT if f in self._set)]
        todo += [f for f in self.files if f.endswith(".gdextension")]
        while todo:
            rel = todo.pop()
            if rel in seen:
                continue
            seen.add(rel)
            if Path(rel).suffix.lower() in TEXT:
                todo.extend(self._named(rel) - seen)
        return sorted(seen)

    def key(self, script: str, args: Sequence[str]) -> str:
        """A key over the test's command and the content of its files."""
        digest = hashlib.sha256()
        digest.update(f"{KEY_VERSION}\0{script}\0{' '.join(args)}\0".encode())
        for rel in self.dependencies(script):
            digest.update(f"{rel}\0{file_hash(self.root / rel)}\0".encode())
        return digest.hexdigest()


@dataclass
class Outcome:
    passed: bool
    output: str = ""
    #: The test never finished in time, on two tries: a fail of this run only,
    #: never cached (a busy machine is not the test's content).
    timed_out: bool = False


@dataclass
class Verdict:
    """What the change did to the tests."""

    #: Failing now and not before (or new): script -> output.
    broken: dict[str, str] = field(default_factory=dict)
    #: Failing before the change and after it, unchanged by it.
    already: list[str] = field(default_factory=list)
    #: Tests whose result came from the cache.
    cached: list[str] = field(default_factory=list)
    #: Tests that ran.
    ran: list[str] = field(default_factory=list)
    #: Tests whose run now timed out (twice), broken or already failing.
    timed_out: list[str] = field(default_factory=list)


class Cache:
    """Results by key, kept in the project's .joinery/cache."""

    def __init__(self, project: Path | None) -> None:
        self.path = project / CACHE if project is not None else None
        self.entries: dict[str, dict[str, object]] = {}
        if self.path is not None and self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.entries = {
                        k: v for k, v in data.items() if isinstance(v, dict)
                    }
            except (OSError, ValueError):
                self.entries = {}

    def get(self, key: str) -> Outcome | None:
        entry = self.entries.get(key)
        if entry is None:
            return None
        return Outcome(bool(entry.get("passed")), str(entry.get("output", "")))

    def put(self, key: str, outcome: Outcome) -> None:
        if outcome.timed_out:
            return
        self.entries.pop(key, None)
        self.entries[key] = {"passed": outcome.passed, "output": outcome.output}
        while len(self.entries) > CACHE_MAX:
            self.entries.pop(next(iter(self.entries)))

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(4)}")
            temporary.write_text(json.dumps(self.entries), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            pass


#: Runs one test on a tree: (tree root, script) -> its result.
Runner = Callable[[Path, str], Awaitable[Result]]
#: Imports a tree before its tests run (the baseline copy has no import yet).
Importer = Callable[[Path], Awaitable[None]]


def timeout_text(seconds: int) -> str:
    """What a test that never finished says, in place of its output."""
    return (
        f"timed out after {seconds} s, twice: it never called quit(), or it needs "
        "longer than that here. End it with quit(), or raise "
        "[checks] scene_test_seconds in joinery.toml."
    )


async def _run_all(
    root: Path,
    scripts: Iterable[str],
    run: Runner,
    passed: Callable[[Result], bool],
    seconds: int,
) -> dict[str, Outcome]:
    """Run each test; one that times out runs once more before it counts, and a
    second time-out is that test's result alone: the others still count."""
    gate = asyncio.Semaphore(PARALLEL)

    async def one(script: str) -> tuple[str, Outcome]:
        for _try in range(2):
            try:
                async with gate:
                    got = await run(root, script)
            except TimeoutError:
                continue
            return script, Outcome(passed(got), got.output[-_FAIL_TAIL:].strip())
        return script, Outcome(False, timeout_text(seconds), timed_out=True)

    tasks = [asyncio.ensure_future(one(s)) for s in scripts]
    try:
        pairs = await asyncio.gather(*tasks)
    except BaseException:
        # One test's error ends the rest: none keeps a worker running unwatched.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return dict(pairs)


async def judge(
    *,
    project: Path | None,
    stage: Path,
    original: Path | None,
    scripts: Sequence[str],
    args: Callable[[Path, str], Sequence[str]],
    run: Runner,
    passed: Callable[[Result], bool],
    baseline: Callable[[], Awaitable[Path | None]] | None = None,
    prepare: Importer | None = None,
    seconds: int = 120,
) -> Verdict:
    """Run ``scripts`` on ``stage`` and judge each failure against ``original``.

    ``args`` gives a test's arguments in a tree (part of its key); ``run`` runs one
    test; ``passed`` reads its result. ``baseline`` makes a runnable copy of the
    project as it was (None when it can't) and ``prepare`` imports it, only when
    a failing test has no cached result from before the change. ``seconds`` is
    each run's time limit, said in a time-out's result.
    """
    cache = Cache(project)
    verdict = Verdict()
    now = Tree(stage)
    keys = {s: now.key(s, args(stage, s)) for s in scripts}
    results: dict[str, Outcome] = {}
    for script, key in keys.items():
        hit = cache.get(key)
        if hit is not None:
            results[script] = hit
            verdict.cached.append(script)
    fresh = [s for s in scripts if s not in results]
    if fresh:
        for script, outcome in (
            await _run_all(stage, fresh, run, passed, seconds)
        ).items():
            results[script] = outcome
            cache.put(keys[script], outcome)
            verdict.ran.append(script)
    failing = [s for s in scripts if not results[s].passed]
    before: dict[str, Outcome] = {}
    if failing and original is not None:
        then = Tree(original)
        unknown: dict[str, str] = {}
        for script in failing:
            if not (original / script).is_file():
                continue  # new with this change: no "before"
            key = then.key(script, args(original, script))
            hit = results[script] if key == keys[script] else cache.get(key)
            if hit is not None:
                before[script] = hit
            else:
                unknown[script] = key
        if unknown and baseline is not None:
            copy = await baseline()
            if copy is not None:
                try:
                    if prepare is not None:
                        await prepare(copy)
                    ran = await _run_all(copy, list(unknown), run, passed, seconds)
                    for script, outcome in ran.items():
                        before[script] = outcome
                        cache.put(unknown[script], outcome)
                finally:
                    shutil.rmtree(copy, ignore_errors=True)
    cache.save()
    for script in failing:
        if results[script].timed_out:
            verdict.timed_out.append(script)
        if script in before and not before[script].passed:
            verdict.already.append(script)
        else:
            verdict.broken[script] = results[script].output
    return verdict


def already_note(scripts: Sequence[str]) -> str:
    """The note for tests that failed before the change too."""
    names = ", ".join("res://" + s for s in scripts[:5])
    more = f" and {len(scripts) - 5} more" if len(scripts) > 5 else ""
    return f"Already failing before this change, so not held for it: {names}{more}."
