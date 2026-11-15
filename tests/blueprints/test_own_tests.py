"""A fresh project passes its own SceneTree tests, whatever features it has.

Every submit runs the project's SceneTree tests (``joinery.submission``), so a
template test that fails on a fresh project holds every change. Each blueprint
is made with no features and with every feature it supports, and every test
the submit would run is run headless here, the way the submit runs it (its
``## Run:`` line, a settings file of its own), several at once.
"""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from joinery import blueprints, submission
from tests.blueprints.godot_run import (
    godot_binary,
    godot_env,
    import_project,
    make_project,
    needs_godot,
)

TIMEOUT = 300


def _combos() -> list[tuple[str, tuple[str, ...]]]:
    catalogue = blueprints._load([])
    picked: list[tuple[str, tuple[str, ...]]] = []
    for blueprint_id in sorted(catalogue.blueprints):
        every = tuple(
            sorted(f.id for f in catalogue.features.values() if f.supports(blueprint_id))
        )
        for subset in dict.fromkeys([(), every]):
            if not blueprints._resolve(catalogue, blueprint_id, subset).problems:
                picked.append((blueprint_id, subset))
    return picked


def run_own_tests(project: Path) -> dict[str, str]:
    """Run every SceneTree test the submit would run; the failed ones, with output."""
    binary = godot_binary()
    assert binary is not None
    import_project(project)
    scripts = submission.scene_tree_tests(project)
    assert scripts, "a fresh project ships SceneTree tests"

    def run(script: str) -> tuple[str, bool, str]:
        settings = project.parent / f".settings-{script.replace('/', '_')}.cfg"
        argv = [
            str(binary) if arg == "godot" else str(project) if arg == "/work" else arg
            for arg in submission.scene_test_argv(project, script, str(settings))
        ]
        try:
            done = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                env=godot_env(project),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return script, False, "timed out"
        got = submission.Result(done.returncode == 0, done.stdout, done.stderr, 0.0)
        return script, submission.scene_test_passed(got), got.output

    workers = max(2, min(6, (os.cpu_count() or 2) // 2))
    with ThreadPoolExecutor(workers) as pool:
        outcomes = list(pool.map(run, scripts))
    return {script: output for script, ok, output in outcomes if not ok}


@needs_godot
@pytest.mark.parametrize(
    ("blueprint", "features"),
    _combos(),
    ids=lambda value: value if isinstance(value, str) else ("all" if value else "none"),
)
def test_fresh_project_passes_its_own_scene_tests(
    tmp_path: Path, blueprint: str, features: tuple[str, ...]
) -> None:
    project = make_project(tmp_path, blueprint, features)
    failed = run_own_tests(project)
    report = {
        script: "\n".join(
            line
            for line in output.splitlines()
            if "FAIL" in line or "ERROR" in line
        )[:1500]
        or output[-1500:]
        for script, output in failed.items()
    }
    assert not failed, report
