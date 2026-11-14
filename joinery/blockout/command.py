"""``joinery blockout``: check a layout, rebuild its scene, draw its plan, walk it.

The layout file is the blockout's recipe, so rebuilding replays it exactly: the same
layout always writes the same scene bytes.

    joinery blockout check design/blockouts/level.toml --project P
    joinery blockout build design/blockouts/level.toml --project P [--scene res://…]
    joinery blockout walk design/blockouts/level.toml --project P
    joinery blockout grow --sample art/reference/my_map.png --size 32x24 --seed 7
    joinery blockout grow design/blockouts/grown.toml   # grow it again from its recipe

``grow`` writes a new layout grown from the user's own example (ADR 0069); check,
build and walk then treat it like any other layout.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from joinery import blockout, core_update, playtest
from joinery.blockout import build as scene_build
from joinery.blockout import grow
from joinery.blockout.grow import GrowError
from joinery.blockout.layout import LayoutError
from joinery.blockout.player import player_scene


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=["check", "build", "walk", "grow"])
    parser.add_argument(
        "layout",
        type=Path,
        nargs="?",
        help="a layout file (TOML); for grow, a grown layout to grow again",
    )
    parser.add_argument(
        "--sample", default="", help="grow: your own example (PNG, grid or layout TOML)"
    )
    parser.add_argument("--size", default="32x24", help="grow: WxH in cells")
    parser.add_argument("--seed", type=int, default=1, help="grow: the seed")
    parser.add_argument("--name", default="grown", help="grow: the layout's name")
    parser.add_argument(
        "--mode", choices=["3d", "2d-topdown", "2d-side"], help="grow: the level's view"
    )
    parser.add_argument("--cell", type=float, help="grow: plan units per cell")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--scene", default="", help="the res:// scene to build into")
    parser.add_argument(
        "--blueprint", default="", help="check with this starting point's player"
    )
    parser.add_argument("--picture", type=Path, help="also write the plan as PNG")
    parser.add_argument("--json", action="store_true")


def _summary(built: blockout.Built) -> dict[str, Any]:
    reach = built.checked.reach
    return {
        "ok": built.ok,
        "scene": built.scene,
        "layout_file": built.layout_file,
        "counts": built.counts(),
        "fact": reach.fact() if reach is not None else "",
        "problems": [p.as_dict() for p in built.checked.problems],
    }


def _size(text: str) -> tuple[int, int]:
    width, _, height = text.lower().partition("x")
    try:
        return int(width), int(height)
    except ValueError:
        raise GrowError(
            f"--size {text}: write it as WIDTHxHEIGHT, such as 32x24."
        ) from None


def _grow(args: argparse.Namespace, project: Path) -> int:
    """``joinery blockout grow``: grow a layout from a sample, or again from its recipe."""
    try:
        if args.layout is not None:
            path: Path = args.layout
            if not path.is_absolute() and not path.is_file():
                path = project / path
            made = grow.regrow(
                project,
                path.read_text(encoding="utf-8"),
                blueprint=args.blueprint or None,
            )
        else:
            if not args.sample:
                raise GrowError("grow needs --sample (your own example) or a layout.")
            width, height = _size(args.size)
            made = grow.make(
                project,
                args.sample,
                width=width,
                height=height,
                seed=args.seed,
                name=args.name,
                mode=args.mode,
                blueprint=args.blueprint or None,
                scene=args.scene or None,
                cell=args.cell,
            )
    except (OSError, GrowError, LayoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    built = made.built
    text = built.files[built.layout_file]
    target = project / built.layout_file
    same = target.is_file() and target.read_bytes() == text
    if args.layout is None or not same:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text)
    if args.picture:
        args.picture.write_bytes(built.plan_png)
    data = _summary(built)
    recipe = made.grown.layout.grown
    assert recipe is not None
    data["grown"] = recipe.model_dump()
    data["same_as_before"] = same
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(
            f"Grew {built.layout_file} ({recipe.size[0]}x{recipe.size[1]} cells) from "
            f"{recipe.sample} at seed {recipe.seed} (try {recipe.tries})."
        )
        if data["fact"]:
            print(data["fact"])
        if args.layout is not None:
            print(
                "Same bytes as before." if same else "Differs from the file; rewritten."
            )
    return 0 if built.ok else 1


def main(args: argparse.Namespace) -> int:
    project: Path = args.project.resolve()
    if args.action == "grow":
        return _grow(args, project)
    if args.layout is None:
        print(f"blockout {args.action} needs a layout file.", file=sys.stderr)
        return 2
    layout_path: Path = args.layout
    if not layout_path.is_absolute() and not layout_path.is_file():
        layout_path = project / layout_path
    try:
        built = blockout.make(
            project,
            layout_path.read_text(encoding="utf-8"),
            scene=args.scene or None,
            blueprint=args.blueprint or None,
        )
    except (OSError, LayoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.picture:
        args.picture.write_bytes(built.plan_png)
    data = _summary(built)
    if built.ok and args.action == "build":
        files, kept = blockout.merged_files(project, built)
        for relative, content in files.items():
            target = project / relative
            if relative == blockout.SCRIPT_FILE and target.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            if relative == blockout.SCRIPT_FILE:
                # Joinery owns it: `joinery update-core` updates it while unedited.
                core_update.remember(project, project, {relative: content})
        data["written"] = sorted(files)
        data["kept"] = kept
    if built.ok and args.action == "walk":
        data["walk"] = _walk(project, built)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        for problem in data["problems"]:
            print(f"[{problem['severity']}] {problem['text']}")
        if data["fact"]:
            print(data["fact"])
        walk = data.get("walk", {})
        for target in walk.get("route", []):
            state = "reached" if target.get("reached") else "missed"
            print(f"- {target.get('kind')} {target.get('id')}: {state} in Godot")
        if walk.get("reason"):
            print(walk["reason"])
        for written in data.get("written", []):
            print(f"Wrote {written}")
        for note in data.get("kept", []):
            print(note)
    walked = data.get("walk")
    if walked is not None and (walked.get("reason") or not walked.get("route")):
        return 1
    return 0 if built.ok else 1


def _planned(route: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"id": t["id"], "kind": t["kind"], "at": t["at"], "waypoints": t["waypoints"]}
        for t in route.get("targets", [])
    ]


def _walk(project: Path, built: blockout.Built) -> dict[str, Any]:
    """Walk the layout as it builds now, in Godot, with the game's own player.

    The scene is built into a scratch folder inside the project (``.joinery/walk/``,
    hidden from Godot's import and git, removed afterwards), so the walk never needs
    a ``build`` first and never sees an older scene. Returns the planned route, what
    the player reached, and a plain ``reason`` whenever the walk couldn't say.
    """
    route = blockout.route(built)
    data: dict[str, Any] = {"status": "", "planned": _planned(route), "route": []}
    reach = built.checked.reach
    if reach is None or not reach.targets:
        data["reason"] = (
            "The layout has nothing to walk to: add a goal, pickup, coin or checkpoint."
        )
        return data
    if not route.get("targets"):
        data["reason"] = (
            f"On the plan the spawn reaches none of its targets ({reach.fact()}). "
            "Fix the layout's problems first."
        )
        return data
    if not player_scene(project):
        data["reason"] = (
            "The game spawns no player into levels (no game/hooks/player_scene in "
            "project.godot), so there's no one to walk the route."
        )
        return data
    try:
        playtest.godot_binary()
    except (OSError, ValueError) as exc:
        data["reason"] = f"Godot couldn't run, so only the plan was walked: {exc}"
        return data
    scratch = project / WALK_DIR / uuid.uuid4().hex[:12]
    try:
        scene = _scratch_scene(project, built, scratch)
        with tempfile.TemporaryDirectory(prefix="joinery-walk-") as folder:
            route_file = Path(folder) / "route.json"
            route_file.write_text(json.dumps(route), encoding="utf-8")
            report = playtest.run(
                playtest.Options(
                    project=project,
                    seconds=min(25.0 * len(route["targets"]) + 10, 150),
                    seed=1,
                    scene=scene,
                    route=route_file,
                    out=Path(folder) / "run",
                )
            )
    except (OSError, ValueError, RuntimeError) as exc:
        data["reason"] = f"The walk didn't run: {exc}"
        return data
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        with contextlib.suppress(OSError):
            (project / WALK_DIR).rmdir()
    data["status"] = report.get("status", "")
    data["route"] = report.get("route", []) or []
    if not data["route"]:
        why = [
            f"{item.get('title')}: {item.get('detail')}"
            for item in report.get("items", [])
            if item.get("severity") in {"critical", "high"}
        ]
        data["reason"] = (
            "The player never started the route ("
            + (why[0] if why else f"the game {data['status'] or 'did not run'}")
            + ")."
        )
    else:
        missed = [r for r in data["route"] if not r.get("reached")]
        if missed:
            data["reason"] = (
                "In Godot the player didn't reach "
                + ", ".join(f"{r.get('kind')} {r.get('id')}" for r in missed)
                + "; the way there is too narrow, too high or too far to jump for "
                "this player."
            )
    return data


WALK_DIR = ".joinery/walk"


def _scratch_scene(project: Path, built: blockout.Built, scratch: Path) -> str:
    """Write the built scene (and the level script it uses) into ``scratch``."""
    scratch.mkdir(parents=True, exist_ok=True)
    script = scene_build.SCRIPT_SOURCE.read_bytes()
    (scratch / "blockout_level.gd").write_bytes(script)
    relative = scratch.relative_to(project).as_posix()
    text = built.files[built.scene.removeprefix("res://")].decode("utf-8")
    text = text.replace(
        f'path="{scene_build.SCRIPT_PATH}"',
        f'path="res://{relative}/blockout_level.gd"',
    )
    (scratch / "level.tscn").write_text(text, encoding="utf-8")
    return f"res://{relative}/level.tscn"
