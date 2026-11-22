"""``joinery check remote``: Joinery Remote's records never reach git (ADR 0093).

A project's ``.joinery/remote/`` (its device audit log) is never staged or committed,
and a project with its own repository ignores it. The per-user Remote folder (device
token hashes, pairing codes, the push key) is never inside the project or this checkout,
and none of its files is tracked here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from joinery.remote.paths import home_path

#: The per-user folder's files, which no repository may hold.
PRIVATE_NAMES = ("devices.json", "devices.lock", "vapid.pem", "known.json")


def _git(where: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(where), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def check_private(project: Path, repo: Path) -> list[str]:
    """What breaks the rule, one line each; [] when nothing does."""
    errors: list[str] = []
    for folder in {project.resolve(), repo.resolve()}:
        if not folder.is_dir():
            continue
        if _git(folder, "rev-parse", "--is-inside-work-tree").returncode != 0:
            continue
        # The index holds what is committed and what is staged alike.
        listed = _git(
            folder,
            "ls-files",
            "--cached",
            "--",
            ":(glob)**/.joinery/remote/**",
            ":(glob).joinery/remote/**",
            *(f":(glob)**/{name}" for name in PRIVATE_NAMES),
        )
        for line in sorted(set(listed.stdout.splitlines())):
            errors.append(
                f"{folder / line}: Joinery Remote's records never go into git. "
                f"Unstage it: git rm --cached {line}"
            )
    if (project / ".git").exists():
        # A project with its own timeline ignores .joinery/* (joinery.timeline).
        probe = ".joinery/remote/audit.jsonl"
        if _git(project, "check-ignore", "-q", "--no-index", probe).returncode != 0:
            errors.append(
                f"{project}: .joinery/remote/ isn't ignored by git. Add `.joinery/*` "
                "to .gitignore."
            )
    home = home_path()
    for folder in (project, repo):
        if folder.is_dir() and _inside(home, folder):
            errors.append(
                f"{home}: Joinery Remote's private folder is inside {folder}. Keep it "
                "outside any project or checkout (JOINERY_REMOTE_HOME)."
            )
    return errors
