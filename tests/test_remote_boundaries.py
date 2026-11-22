"""Joinery Remote's boundaries (ADR 0093): nothing it keeps reaches git, nothing listens
beyond loopback, and nothing but Web Push leaves the machine."""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from joinery.remote.privacy import check_private

ROOT = Path(__file__).resolve().parents[1]
#: Modules that could reach another machine.
NETWORK = {
    "urllib.request",
    "http.client",
    "httpx",
    "requests",
    "aiohttp",
    "websockets",
    "socket",
    "smtplib",
    "ftplib",
    "ssl",
}
#: The one module allowed to send off the machine: Web Push to the push services.
SENDS = {"joinery/remote/push.py"}


def remote_sources() -> list[Path]:
    return [
        *sorted((ROOT / "joinery/remote").glob("*.py")),
        ROOT / "bridge/remote_channel.py",
    ]


def imported(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def test_nothing_but_web_push_reaches_the_network() -> None:
    reaching = {
        str(path.relative_to(ROOT)): sorted(imported(path) & NETWORK)
        for path in remote_sources()
        if imported(path) & NETWORK
    }
    assert set(reaching) <= SENDS, reaching
    assert reaching == {"joinery/remote/push.py": ["urllib.request"]}


def test_every_connection_and_listener_is_loopback() -> None:
    calls = re.compile(r"(open_connection|start_server)\(\s*([^,)]*)")
    for path in remote_sources():
        for match in calls.finditer(path.read_text(encoding="utf-8")):
            target = match.group(2).strip()
            assert target in {'"127.0.0.1"', "self._client"}, (path, match.group(0))
    text = (ROOT / "bridge/remote_channel.py").read_text(encoding="utf-8")
    assert 'start_server(\n                    self._client, "127.0.0.1"' in text


def test_the_server_binds_loopback_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    from joinery.remote import server
    from joinery.remote.store import Store

    seen: dict[str, Any] = {}

    class Config:
        def __init__(self, _app: Any, **kwargs: Any) -> None:
            seen.update(kwargs)

    class Server:
        def __init__(self, _config: Any) -> None:
            self.should_exit = False

        def run(self) -> None:
            pass

    monkeypatch.setattr(uvicorn, "Config", Config)
    monkeypatch.setattr(uvicorn, "Server", Server)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    assert server.run(Store(home), 47615, home) == 0
    assert seen["host"] == "127.0.0.1" and seen["proxy_headers"] is False


def test_the_page_loads_nothing_from_elsewhere() -> None:
    for path in (ROOT / "joinery/remote/static").rglob("*"):
        if path.suffix in {".html", ".js", ".css", ".webmanifest"}:
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"https?://", text), path


def git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)


def test_check_remote_finds_records_staged_in_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from joinery.timeline import ensure_ignores

    monkeypatch.setenv("JOINERY_REMOTE_HOME", str(tmp_path / "private"))
    project = tmp_path / "game"
    (project / ".joinery/remote").mkdir(parents=True)
    git(project, "init", "-q")
    ensure_ignores(project)
    (project / ".joinery/remote/audit.jsonl").write_text("{}\n")
    assert check_private(project, project) == []
    # Forced past the ignore rule: found.
    git(project, "add", "-f", ".joinery/remote/audit.jsonl")
    [error] = check_private(project, project)
    assert "audit.jsonl" in error and "never go into git" in error
    git(project, "rm", "-q", "--cached", ".joinery/remote/audit.jsonl")
    # A project that doesn't ignore .joinery/*.
    (project / ".gitignore").write_text("")
    [error] = check_private(project, project)
    assert "isn't ignored" in error


def test_check_remote_finds_the_private_folder_in_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "game"
    project.mkdir()
    monkeypatch.setenv("JOINERY_REMOTE_HOME", str(project / "remote-home"))
    [error] = check_private(project, tmp_path / "elsewhere")
    assert "private folder is inside" in error


def test_check_remote_runs_in_check_all(capsys: pytest.CaptureFixture[str]) -> None:
    from joinery.cli import main

    assert main(["check", "remote"]) == 0
    assert "check remote: ok" in capsys.readouterr().out
