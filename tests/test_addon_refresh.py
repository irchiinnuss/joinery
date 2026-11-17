"""A project's add-on is checked against Joinery's and updated from its own editor."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from joinery import __version__, addon_refresh, bootstrap, timeline
from joinery.bootstrap import update_addon


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("joinery.bootstrap.editor_running", lambda _project: False)
    (tmp_path / "project.godot").write_text("config_version=5\n")
    return tmp_path


def test_state_follows_the_project_copy(project: Path) -> None:
    assert addon_refresh.state(project)["state"] == "missing"
    update_addon(project)
    assert addon_refresh.state(project)["state"] == "current"
    # Godot's own files beside the add-on's never make it look old.
    (project / "addons/joinery/icons/ui/menu.svg.import").write_text("[remap]\n")
    assert addon_refresh.state(project)["state"] == "current"
    (project / "addons/joinery/plugin.gd").write_text("# an older add-on\n")
    report = addon_refresh.state(project)
    assert report["state"] == "stale"
    assert report["installed"] != report["available"]


def test_update_from_the_editor_waits_updates_and_reopens(project: Path) -> None:
    update_addon(project)
    (project / "addons/joinery/plugin.gd").write_text("# an older add-on\n")
    started: list[Path] = []
    scheduled = addon_refresh.schedule(
        project,
        wait_pid=0,
        editor="/opt/joinery/bin/godot",
        start=lambda plan: started.append(plan) or 42,
    )
    assert scheduled == {
        "state": "scheduled",
        "helper": 42,
        "project": str(project.resolve()),
    }
    plan = json.loads(started[0].read_text())
    assert plan["relaunch_argv"] == [
        str(Path("/opt/joinery/bin/godot")),  # The system's own separators.
        "--editor",
        "--path",
        str(project.resolve()),
    ]
    launched: list[list[str]] = []
    outcome = addon_refresh.run(
        started[0], launch=lambda argv, _home: launched.append(argv)
    )
    assert outcome["ok"] is True and outcome["state"] == "updated"
    assert addon_refresh.state(project)["state"] == "current"
    assert launched == [plan["relaunch_argv"]]
    assert not started[0].exists()


def test_a_failed_update_still_reopens_and_says_why(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    update_addon(project)
    (project / "addons/joinery/plugin.gd").write_text("# an older add-on\n")

    def refuse(_project: Path) -> dict[str, str]:
        raise ValueError("The disk is full.")

    monkeypatch.setattr("joinery.bootstrap.update_addon", refuse)
    plan = project / addon_refresh.PLAN
    plan.write_text(
        json.dumps({"project": str(project), "wait_pid": 0, "relaunch_argv": ["godot"]})
    )
    launched: list[list[str]] = []
    outcome = addon_refresh.run(plan, launch=lambda argv, _home: launched.append(argv))
    assert outcome["ok"] is False
    assert launched == [["godot"]]
    report = addon_refresh.state(project)
    assert report["state"] == "stale"
    assert report["last_error"] == "The disk is full."


def test_an_editor_that_stays_open_is_never_reopened_later(project: Path) -> None:
    plan = project / addon_refresh.PLAN
    plan.parent.mkdir()
    plan.write_text(
        json.dumps(
            {"project": str(project), "wait_pid": os.getpid(), "relaunch_argv": ["x"]}
        )
    )
    launched: list[list[str]] = []
    outcome = addon_refresh.run(
        plan, wait_seconds=0.0, launch=lambda argv, _home: launched.append(argv)
    )
    assert outcome["ok"] is False
    assert launched == []
    assert not (project / "addons/joinery").exists()


def test_a_mac_app_is_opened_as_an_app(tmp_path: Path) -> None:
    editor = "/Applications/Joinery.app/Contents/MacOS/Joinery"
    assert addon_refresh.relaunch_argv(editor, tmp_path) == [
        "open",
        "-n",
        str(Path("/Applications/Joinery.app")),
        "--args",
        "--editor",
        "--path",
        str(tmp_path),
    ]


def test_the_addon_ships_the_files_godot_would_generate() -> None:
    """Godot 4.7 writes a .uid beside every script and a .import beside every
    imported file, and both belong in version control. The add-on ships them, so
    a new project's git has nothing new after the first import.
    """
    from joinery.catalog import ROOT

    addon = ROOT / "godot_addon/joinery"
    scripts = [p for p in addon.rglob("*") if p.suffix in {".gd", ".gdshader"}]
    imported = [
        p
        for p in addon.rglob("*")
        if p.suffix in {".png", ".svg", ".ttf", ".otf", ".wav", ".ogg"}
    ]
    assert scripts and imported
    missing = [
        p.relative_to(addon).as_posix()
        for p in scripts
        if not Path(f"{p}.uid").is_file()
    ]
    missing += [
        p.relative_to(addon).as_posix()
        for p in imported
        if not Path(f"{p}.import").is_file()
    ]
    assert not missing, missing


@pytest.mark.skipif(timeline.git_executable() is None, reason="git is not installed")
def test_an_update_is_one_timeline_entry_and_leaves_nothing_loose(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    home = tmp_path_factory.mktemp("git-home")
    (home / "gitconfig").write_text(
        "[init]\n\tdefaultBranch = main\n[user]\n\tname = Joinery Test\n\temail = test@example.com\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=project, capture_output=True, text=True, check=True
        ).stdout

    git("init", "-q")
    update_addon(project)
    timeline.start(project)
    entries = len(git("log", "--format=%s").splitlines())
    # An older add-on, with a file this one no longer has; the user's own edit.
    (project / "addons/joinery/plugin.gd").write_text("# an older add-on\n")
    (project / "addons/joinery/gone.gd").write_text("# removed since\n")
    git("add", "-A")
    git("commit", "-q", "-m", "Older add-on")
    (project / "main.gd").write_text("extends Node\n")
    done = update_addon(project)
    assert done["state"] == "updated" and done["commit"]
    log = git("log", "--format=%s").splitlines()
    assert len(log) == entries + 2
    assert log[0] == bootstrap.ADDON_COMMIT.format(version=__version__)
    # Nothing of the add-on is left loose; the user's file is untouched.
    assert git("status", "--porcelain", "--", "addons") == ""
    assert "main.gd" in git("status", "--porcelain")
    assert "addons/joinery/gone.gd" in git("show", "--name-only", "--format=", "HEAD")
    # Already current: no new entry.
    assert "commit" not in update_addon(project)
    assert len(git("log", "--format=%s").splitlines()) == entries + 2
