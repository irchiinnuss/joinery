"""Fast, read-only search of a game project's text files (``search_project``).

The registry tool every agent has, whatever its own shell may do: a regular
expression (or plain text) over the project's scripts, scenes, resources, docs and
settings, narrowed by a folder and a glob, with the matches capped so one search
never floods the context. Ripgrep runs it when it is installed; otherwise the same
walk runs here. Binary files, Godot's import cache, Joinery's own records and
``addons/`` are skipped, so results are the game's own sources.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

#: Folders never searched: caches, records and the engine-side add-ons.
SKIP_DIRS = frozenset({".git", ".godot", ".joinery", "addons", "node_modules"})
#: Matches returned at most, and the most a caller may ask for.
DEFAULT_LIMIT = 30
MAX_LIMIT = 60
#: Matching lines counted at most (past the ones returned), for per-file counts.
COUNT_CAP = 2000
#: Files beyond the returned matches named with their counts.
OTHER_FILES = 10
#: Characters of one matching line kept.
LINE_CHARS = 120
#: Files bigger than this are not text sources; skipped.
MAX_FILE_BYTES = 2_000_000
#: Text suffixes a search reads (the rest are assets: models, images, sound).
TEXT = frozenset(
    {
        ".gd",
        ".cs",
        ".gdshader",
        ".gdshaderinc",
        ".tscn",
        ".tres",
        ".godot",
        ".cfg",
        ".toml",
        ".json",
        ".md",
        ".txt",
        ".csv",
        ".po",
        ".pot",
        ".py",
        ".scd",
        ".yaml",
        ".yml",
        ".lock",
        ".gdns",
        ".gdextension",
    }
)


@dataclass
class Found:
    """What a search returns: the first ``limit`` matches [{path, line, text}], and
    how many lines matched in each file (counted on past the cap). Given to the
    agent grouped by file, ``"line: text"`` under each path, so a path is said once."""

    matches: list[dict[str, Any]] = field(default_factory=list)
    #: Matching lines per file, all of them (up to ``COUNT_CAP`` in all).
    counts: dict[str, int] = field(default_factory=dict)
    truncated: bool = False

    def add(self, path: str, line: int, text: str, limit: int) -> bool:
        """Count a match, and keep it while under ``limit``; False once counting
        should stop."""
        self.counts[path] = self.counts.get(path, 0) + 1
        if len(self.matches) < limit:
            self.matches.append({"path": path, "line": line, "text": text})
        else:
            self.truncated = True
        return sum(self.counts.values()) < COUNT_CAP

    def as_dict(self) -> dict[str, Any]:
        grouped: dict[str, list[str]] = {}
        for m in self.matches:
            grouped.setdefault(m["path"], []).append(f"{m['line']}: {m['text']}")
        for path, lines in grouped.items():
            more = self.counts.get(path, len(lines)) - len(lines)
            if more > 0:
                lines.append(f"… {more} more in this file")
        total = sum(self.counts.values())
        data: dict[str, Any] = {
            "matches": grouped,
            "count": len(self.matches),
            "files": len(self.counts),
        }
        if self.truncated:
            data["truncated"] = True
            data["total"] = f"{total}+" if total >= COUNT_CAP else total
            unshown = sorted(
                (p for p in self.counts if p not in grouped),
                key=lambda p: -self.counts[p],
            )
            if unshown:
                data["other_files"] = {p: self.counts[p] for p in unshown[:OTHER_FILES]}
                if len(unshown) > OTHER_FILES:
                    data["other_files"]["…"] = len(unshown) - OTHER_FILES
            data["next"] = "More: narrow path or glob, or a more exact pattern."
        return data


def _safe_dir(project: Path, path: str) -> Path:
    root = project.resolve()
    target = (root / path.strip().lstrip("/").removeprefix("res://")).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Search inside the project.")
    if any(part in SKIP_DIRS for part in target.relative_to(root).parts):
        raise ValueError("That folder holds caches or records, not game sources.")
    return target


def _compile(pattern: str, regex: bool, ignore_case: bool) -> re.Pattern[str]:
    if not pattern:
        raise ValueError("Give a pattern to search for.")
    try:
        return re.compile(
            pattern if regex else re.escape(pattern),
            re.IGNORECASE if ignore_case else 0,
        )
    except re.error as exc:
        raise ValueError(
            f"The pattern is not a valid regular expression: {exc}"
        ) from exc


def _walk(root: Path, base: Path, glob: str) -> list[Path]:
    if root.is_file():
        return [root]
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(base)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        if not path.is_file() or path.suffix.lower() not in TEXT:
            continue
        if glob and not _glob(rel.as_posix(), glob):
            continue
        found.append(path)
    return found


def _glob(rel: str, glob: str) -> bool:
    name = PurePosixPath(rel).name
    return fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(name, glob)


def _line(text: str) -> str:
    text = text.rstrip("\n").strip()
    return text if len(text) <= LINE_CHARS else text[:LINE_CHARS] + "…"


def _python(
    base: Path, root: Path, compiled: re.Pattern[str], glob: str, limit: int
) -> Found:
    found = Found()
    for path in _walk(root, base, glob):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = path.relative_to(base).as_posix()
        for number, text in enumerate(lines, 1):
            if compiled.search(text) and not found.add(rel, number, _line(text), limit):
                return found
    return found


def _ripgrep(
    rg: str,
    base: Path,
    root: Path,
    pattern: str,
    regex: bool,
    ignore_case: bool,
    glob: str,
    limit: int,
) -> Found | None:
    """The same search through ripgrep; None when ripgrep can't run it."""
    args = [rg, "--json", "--no-config", "--max-filesize", str(MAX_FILE_BYTES)]
    args += ["--ignore-case"] if ignore_case else ["--case-sensitive"]
    if not regex:
        args.append("--fixed-strings")
    for skip in sorted(SKIP_DIRS):
        args += ["--glob", f"!{skip}/"]
    args += ["--glob", "!*.{glb,gltf,png,jpg,jpeg,webp,wav,ogg,mp3,blend,kra,exr,zip}"]
    if glob:
        args += ["--glob", glob]
    args += ["--", pattern, str(root)]
    try:
        done = subprocess.run(
            args,
            cwd=base,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode not in {0, 1}:
        return None
    found = Found()
    for raw in done.stdout.splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data", {})
        path = Path(str(data.get("path", {}).get("text", "")))
        try:
            rel = (path if path.is_absolute() else base / path).relative_to(base)
        except ValueError:
            continue
        if not found.add(
            rel.as_posix(),
            int(data.get("line_number") or 0),
            _line(str(data.get("lines", {}).get("text", ""))),
            limit,
        ):
            break
    return found


def search(
    project: Path,
    pattern: str,
    *,
    path: str = "",
    glob: str = "",
    regex: bool = True,
    ignore_case: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Lines matching ``pattern`` under ``project/path`` in files matching ``glob``."""
    base = project.resolve()
    root = _safe_dir(base, path)
    if not root.exists():
        raise ValueError(f"No {path!r} in the project.")
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    compiled = _compile(pattern, regex, ignore_case)
    rg = shutil.which("rg")
    found = (
        _ripgrep(rg, base, root, pattern, regex, ignore_case, glob, limit)
        if rg and root.is_dir()
        else None
    )
    if found is None:
        found = _python(base, root, compiled, glob, limit)
    return found.as_dict()
