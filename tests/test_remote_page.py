"""The phone page Joinery Remote serves (joinery/remote/static, ADR 0093)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from joinery.remote.server import PAGE_FILES, STATIC

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "joinery/remote/static"


def test_every_page_file_is_shipped_and_nothing_else_is_served() -> None:
    on_disk = {p.relative_to(PAGE).as_posix() for p in PAGE.rglob("*") if p.is_file()}
    assert set(PAGE_FILES) == on_disk
    assert set(STATIC) == {"/" if n == "index.html" else "/" + n for n in PAGE_FILES}


def test_the_page_loads_nothing_from_elsewhere() -> None:
    for name in PAGE_FILES:
        if not name.endswith((".html", ".css", ".js", ".webmanifest")):
            continue
        text = (PAGE / name).read_text(encoding="utf-8")
        assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", text), name
        # Text goes in as text: no HTML is ever built from data.
        assert "innerHTML" not in text and "insertAdjacentHTML" not in text, name
        assert "eval(" not in text and "new Function" not in text, name
    index = (PAGE / "index.html").read_text(encoding="utf-8")
    # The CSP has no 'unsafe-inline': no inline script or handler.
    assert not re.search(r"<script(?![^>]*\bsrc=)", index)
    assert not re.search(r"\son[a-z]+=", index)


def test_the_service_worker_keeps_only_the_page() -> None:
    worker = (PAGE / "sw.js").read_text(encoding="utf-8")
    kept = re.search(r"const FILES = \[(.*?)\];", worker, re.DOTALL)
    assert kept is not None
    files = re.findall(r'"([^"]+)"', kept.group(1))
    assert set(files) <= set(STATIC)
    assert 'startsWith("/api/")' in worker


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_state_model() -> None:
    done = subprocess.run(
        ["node", "--test", str(ROOT / "tests/remote_page/state.test.mjs")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_page_in_a_browser(tmp_path: Path) -> None:
    """Pair, projects, the chat, a change opened then accepted, Go, in dark and light
    (tests/remote_page/browse.py, headless Chrome)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "browse", ROOT / "tests/remote_page/browse.py"
    )
    assert spec is not None and spec.loader is not None
    browse = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(browse)
    if not browse.chrome() or shutil.which("node") is None:
        pytest.skip("needs Chrome and node")
    seen = browse.run(tmp_path)
    failed = [c["what"] for c in seen["checks"] if not c["ok"]]
    assert not failed and not seen["errors"], (failed, seen["errors"])
    assert len(seen["checks"]) >= 28
