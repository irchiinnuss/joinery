"""After Refresh tools, every screen sees the new answer, and a refresh that fails says why.

The sidebar asks `joinery status --background`, which answers from the last
report for 30 seconds; doctor always asks afresh. A refresh must age that report
out, or the sidebar keeps its pre-refresh "needs a refresh" while doctor says
ready. And an app the refresh couldn't set up is a failed refresh, not "done".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from joinery import bootstrap, doctor, readiness, tool_refresh
from tests.test_readiness import FakeHost, project


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "cache"
    monkeypatch.setenv("JOINERY_READINESS_CACHE", str(folder))
    return folder


def _states(report: dict[str, Any]) -> dict[str, str]:
    return {t["id"]: t["state"] for t in report["tools"] if not t.get("custom")}


def test_a_refresh_ages_out_the_answer_screens_poll(tmp_path: Path) -> None:
    root = project(tmp_path, ["krita"])
    host = FakeHost(engine=tmp_path / "godot")
    built = dict(host.images)
    host.images = {}
    before = readiness.current(root, host=host, background=False)
    assert (
        before["notice"]["text"]
        == "Godot and Krita need a refresh before they can work."
    )
    # The refresh builds the image; within 30 s the snapshot still says otherwise.
    host.images = built
    assert readiness.current(root, host=host, background=False).get("cached")
    readiness.expire_snapshot()
    after = readiness.current(root, host=host, background=False)
    assert "cached" not in after and after["notice"] is None
    # What doctor says (always fresh) and what the sidebar says now agree.
    fresh = doctor.readiness_report(root / "joinery.toml", host=host)
    assert _states(after) == _states(fresh)
    # Screens that only read the last entries still have them meanwhile.
    readiness.expire_snapshot()
    assert set(readiness.cached_tools()) == {"godot", "krita"}


def test_expiring_nothing_is_harmless(cache: Path) -> None:
    readiness.expire_snapshot()
    assert not (cache / "status.json").exists()


def _quiet_docker(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Docker answers, every image is current, and nothing really spawns."""

    class Done:
        returncode = 0
        stdout = ""

    refreshed: list[tuple[Any, ...]] = []
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *_a, **_k: Done())
    monkeypatch.setattr(bootstrap, "_current", lambda _tag, _rev: True)
    monkeypatch.setattr(
        readiness, "spawn_refresh", lambda *a, **k: refreshed.append((a, k)) is None
    )
    monkeypatch.delenv("JOINERY_SANDBOX_REV", raising=False)
    return refreshed


def test_an_app_that_could_not_be_set_up_fails_the_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    root = project(tmp_path, ["godot", "krita", "blender"])
    refreshed = _quiet_docker(monkeypatch)

    def install(tool: str) -> None:
        if tool in {"godot", "blender"}:
            raise ValueError("The MCP checkout does not match the pinned revision.")

    monkeypatch.setattr(bootstrap, "install", install)
    with pytest.raises(tool_refresh.RefreshFailed) as failed:
        bootstrap.setup(root)
    assert str(failed.value) == (
        "Couldn't set up Blender and Godot: "
        "The MCP checkout does not match the pinned revision."
    )
    state = json.loads((cache / "refresh.json").read_text())
    assert state["state"] == "failed" and state["error"] == str(failed.value)
    # The tools are still checked again, so screens show where things stand.
    assert refreshed
    monkeypatch.delenv("JOINERY_SANDBOX_REV", raising=False)


def test_a_refresh_with_nothing_to_build_still_expires_the_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    root = project(tmp_path, ["krita"])
    host = FakeHost(engine=tmp_path / "godot")
    readiness.status(root, host=host)
    assert readiness.current(root, host=host, background=False).get("cached")
    _quiet_docker(monkeypatch)
    monkeypatch.setattr(bootstrap, "install", lambda _tool: None)
    result = bootstrap.setup(root)
    assert result["built"] == [] and result["unavailable"] == []
    assert json.loads((cache / "refresh.json").read_text())["state"] == "done"
    assert not readiness.current(root, host=host, background=False).get("cached")
    monkeypatch.delenv("JOINERY_SANDBOX_REV", raising=False)
