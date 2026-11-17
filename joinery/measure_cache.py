"""The art bible's worker measurements of one change, kept per file between checks.

A change is checked many times before the user sees it: every precheck, then the
submit, each running the bible's measured rules on every changed asset. The
measurements (a `.blend` read in Blender, image pixels and audio loudness read by
ffmpeg) run in the offline creative worker, emulated on Apple Silicon, so a round
took a minute or two even when the agent had fixed one script since the last.

Each measuring command loops over its files and prints one block per file, opened
by a ``JOINERY_FILE:<path>`` line. This keeps each file's block under a key of the
command, the worker image and the file's content, so the next round runs the
command only on files whose content changed and replays the rest. The rules are
applied afresh every time to the same text, so a result can't differ from a run of
the whole command. Conservative on purpose:

- a `.blend` file's key covers every `.blend` in the tree too, since one can link
  another's data;
- only a run that succeeded is kept, and only a file whose block says something;
- a command that isn't a per-file loop (a music mix measured as one) is never kept.

The cache lives with the transaction (its directory goes when the change is
decided), so nothing carries over to another change.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path

from joinery.runner import Result

Measure = Callable[[list[str], Path], Awaitable[Result]]
#: Bump when what a key covers changes.
VERSION = "1"
MARKER = "JOINERY_FILE:"
#: Folders never searched for linked `.blend` files.
_SKIP = frozenset({".git", ".godot", ".joinery", "__pycache__"})


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def blends(root: Path) -> str:
    """One hash over every `.blend` in ``root`` (by path and content)."""
    found: list[str] = []
    for directory, folders, names in os.walk(root):
        folders[:] = sorted(f for f in folders if f not in _SKIP)
        for name in sorted(names):
            if name.lower().endswith(".blend"):
                path = Path(directory) / name
                found.append(f"{path.relative_to(root).as_posix()}:{_file_hash(path)}")
    return hashlib.sha256("\n".join(found).encode()).hexdigest()


def blocks(stdout: str) -> dict[str, str]:
    """Each file's block of a per-file loop's output, marker line included."""
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in stdout.splitlines():
        if line.startswith(MARKER):
            current = found.setdefault(line.removeprefix(MARKER).strip(), [])
        if current is not None:
            current.append(line)
    return {path: "\n".join(lines) for path, lines in found.items()}


def kept(block: str) -> bool:
    """A block worth keeping: it measured something, and nothing failed in it (a
    file that could not be measured is measured again next time)."""
    lines = block.splitlines()[1:]
    return any(line.strip() for line in lines) and not any(
        "Traceback" in line or "Error" in line for line in lines
    )


class MeasureCache:
    """Per-file measurements of one transaction, in ``path`` (a JSON file)."""

    def __init__(self, path: Path | None, image: str) -> None:
        self.path = path
        self.image = image
        self.entries: dict[str, str] = {}
        #: Files answered from the cache, and files measured, in this check.
        self.hits = 0
        self.misses = 0
        if path is not None and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if isinstance(data, dict):
                self.entries = {k: v for k, v in data.items() if isinstance(v, str)}

    def key(self, head: list[str], root: Path, file: str, linked: str) -> str | None:
        target = root / file
        if not target.is_file():
            return None
        parts = [VERSION, self.image, *head, file, _file_hash(target)]
        if file.lower().endswith(".blend"):
            parts.append(linked)
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()

    def wrap(self, measure: Measure) -> Measure:
        """``measure``, answering each file it measured before from the cache."""

        async def cached(argv: list[str], root: Path) -> Result:
            if argv[:2] != ["sh", "-c"] or len(argv) <= 4:
                return await measure(argv, root)
            head, files = argv[:4], argv[4:]
            linked = (
                blends(root) if any(f.lower().endswith(".blend") for f in files) else ""
            )
            keys = {f: self.key(head, root, f, linked) for f in files}
            known = {
                f: self.entries[k]
                for f, k in keys.items()
                if k is not None and k in self.entries
            }
            missing = [f for f in files if f not in known]
            self.hits += len(known)
            self.misses += len(missing)
            replayed = "\n".join(known[f] for f in files if f in known)
            if not missing:
                return Result(True, replayed + "\n", "", 0.0)
            got = await measure([*head, *missing], root)
            if got.ok:
                found = blocks(got.stdout)
                for file in missing:
                    key = keys[file]
                    block = found.get(file, "")
                    if key is not None and kept(block):
                        self.entries[key] = block
            stdout = replayed + "\n" + got.stdout if replayed else got.stdout
            return Result(got.ok, stdout, got.stderr, got.seconds)

        return cached

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
