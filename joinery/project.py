"""Create Joinery game projects. Plain Godot projects, plus Joinery's own files."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from joinery import (
    bible,
    brief,
    game_setup,
    genre_questions,
    memory,
    setup_imports,
    source_folders,
)
from joinery.catalog import ROOT, Catalog
from joinery.installer import Selection, write_mcp_config, write_selection
from joinery.wiring import write_agent_wiring, write_memory_stubs

TEMPLATE = ROOT / "templates" / "project"
ASSET_DIRS = ("art", "audio", "recipes")
#: Tells the editor addon to show the first-project tour on the first open (tour.gd),
#: which removes it. In .joinery/, so it is never part of the game.
TOUR_PENDING = ".joinery/tour-pending"


#: `answers` key -> the file beside the project the starting point consumes.
ANSWER_FILES = {"brief": brief.ANSWERS_FILE, "bible": bible.ANSWERS_FILE}


def read_answers(
    source: Path,
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    """`joinery new --answers FILE`: the user's brief and look answers, and files.

    FILE is one JSON object: ``brief`` and ``bible`` are the objects the wizard
    writes for `game apply` (``.joinery-brief-answers.json``,
    ``.joinery-bible-answers.json``); ``references`` (``[{"path", "note"}]``) and
    ``design_docs`` (``[{"path"}]`` or paths) are the user's own files, relative
    to FILE's folder. Returns (answers, ``PATH=note`` references, documents).
    Checked whole before anything is written.
    """
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Can't read {source}: {exc.strerror}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}: not valid JSON: {exc}") from exc
    known = {*ANSWER_FILES, "references", "design_docs"}
    if not isinstance(raw, dict) or not raw or set(raw) - known:
        raise ValueError(
            f"{source}: expected an object with any of {', '.join(sorted(known))}."
        )
    answers: dict[str, dict[str, Any]] = {}
    for key in ANSWER_FILES:
        if key in raw:
            if not isinstance(raw[key], dict):
                raise ValueError(f"{source}: {key} must be an object of answers.")
            answers[key] = raw[key]
    base = source.resolve().parent

    def where(item: object, what: str) -> tuple[str, str]:
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError(f"{source}: each of {what} needs a path.")  # noqa: TRY004
        note = item.get("note", "")
        return str(base / Path(item["path"]).expanduser()), str(note or "")

    lists = {key: raw.get(key, []) for key in ("references", "design_docs")}
    for key, value in lists.items():
        if not isinstance(value, list):
            raise ValueError(f"{source}: {key} must be a list.")  # noqa: TRY004
    references = [
        f"{p}={n}" if n else p
        for p, n in (where(i, "references") for i in lists["references"])
    ]
    documents = [where(i, "design_docs")[0] for i in lists["design_docs"]]
    return answers, references, documents


def mark_tour_pending(path: Path) -> None:
    """A new project: the editor shows the first-project tour once. Never fails."""
    try:
        marker = path / TOUR_PENDING
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("")
    except OSError:
        pass


def create_project(
    path: Path,
    name: str,
    selection: Selection,
    catalog: Catalog,
    *,
    blueprint: str | None = None,
    features: Sequence[str] = (),
    steam_app_id: str | None = None,
    connect: str | None = None,
    report: game_setup.Report | None = None,
    wizard: bool = False,
    references: Sequence[tuple[Path, str]] = (),
    documents: Sequence[Path] = (),
    answers: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    """Scaffold a project. Refuses a non-empty folder so nothing is overwritten.

    The creation-only gate is wired for every supported agent (Claude Code,
    Codex, Gemini CLI, opencode), so Rule Zero holds whichever one the user runs.
    With `blueprint`, the project starts as that playable greybox game with the
    chosen features, its game brief and the art bible draft with the
    blueprint's preset; without, it stays an empty Godot project.

    With `wizard` (what `joinery new` does) it is exactly the project the
    new-project wizard makes (`game_setup.apply_choices`): the Joinery addon
    goes in with its MIT licence and the plugin on, and with Steam its
    settings are written and the pinned GodotSteam extension is downloaded.
    Harnesses that only boot the game leave these out.

    `references` ((path, note) pairs) and `documents` are the user's own
    reference images and design documents (`setup_imports`): checked before
    anything is written, then copied in with the starting point.

    `answers` (``{"brief": {...}, "bible": {...}}``, see `read_answers`) are the
    user's own answers to the wizard's brief and look questions, the same objects
    `game apply` reads from its answers files: they go into those files, which the
    starting point consumes, so the look drafted from them is approved at creation
    exactly as in the wizard.
    """
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"{path} is not empty. Pick a new or empty folder.")
    if '"' in name or "\n" in name:
        raise ValueError("The project name cannot contain quotes or line breaks.")
    if blueprint is None and features:
        raise ValueError(
            "Features need a blueprint. Add --blueprint (see joinery blueprints)."
        )
    if blueprint is not None:
        # Refuse an unknown blueprint or a bad feature mix before any file exists.
        game_setup.check_choices(blueprint, features, steam_app_id, connect)
    elif steam_app_id or connect:
        raise ValueError(
            "Steam settings need a blueprint. Add --blueprint and --features steam."
        )
    if (references or documents or answers) and blueprint is None:
        raise ValueError(
            "References and design documents go on the look and the game brief, "
            "which come with a starting point. Add --blueprint (blank is an empty game)."
        )
    if answers and blueprint is not None and "brief" in answers:
        # The question round is checked whole before the project gets anything.
        genre_questions.resolve(blueprint, answers["brief"].get(brief.QUESTIONS_KEY))
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    try:
        # Every picked file is checked before the project gets anything.
        imports = setup_imports.plan(path, references, documents)
        _scaffold(path, name, selection)
        for key, file_name in ANSWER_FILES.items():
            if answers and key in answers:
                (path / file_name).write_text(
                    json.dumps(dict(answers[key]), indent=2) + "\n", encoding="utf-8"
                )
        if wizard:
            game_setup.install_addon(path, report)
        if blueprint is not None:
            game_setup.apply_choices(
                path,
                blueprint,
                features,
                steam_app_id,
                connect,
                addon=False,
                steam_setup=wizard,
                report=report,
                imports=imports,
            )
    except BaseException:
        # The folder was empty or absent, so everything in it is ours: leave
        # no half-made project behind (a blueprint can still refuse at apply).
        if existed:
            for child in path.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        else:
            shutil.rmtree(path)
        raise


#: A new project's .gitignore: Godot's cache, local output, and the junk files
#: operating systems drop into folders, so they never land in the timeline.
GITIGNORE = (
    ".godot/\nout/\n\n# Operating system files\n.DS_Store\n._*\nThumbs.db\ndesktop.ini\n"
)


def _scaffold(path: Path, name: str, selection: Selection) -> None:
    for directory in ASSET_DIRS:
        (path / directory).mkdir()
        (path / directory / ".gitkeep").touch()
    # The game brief lives in design/. Native sources, recipes and reference
    # boards stay out of Godot's imports (source_folders).
    (path / "design").mkdir()
    (path / "design" / ".gitkeep").touch()
    source_folders.mark(path, create=True)
    text = (TEMPLATE / "project.godot").read_text().replace("{{name}}", name)
    (path / "project.godot").write_text(text)
    (path / "joinery.lock").write_text("version = 1\n")
    (path / ".gitignore").write_text(GITIGNORE)
    (path / ".gitattributes").write_text((ROOT / ".gitattributes").read_text())
    write_agent_wiring(path)
    # Project memory every agent reads, and each agent's own file pointing at it.
    memory.ensure(path)
    write_memory_stubs(path)
    write_selection(selection, path / "joinery.toml")
    write_mcp_config(selection, path)
