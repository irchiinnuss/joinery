"""The request-size measurement replays recorded calls under each cut."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "request_size_eval", ROOT / "evals/request_size.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["request_size_eval"] = module
    spec.loader.exec_module(module)
    return module


def user(text: str) -> dict[str, Any]:
    return {"type": "user", "message": {"content": text}}


def call(ident: str, context: int, tool: str = "", given: Any = None) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if tool:
        content.append(
            {"type": "tool_use", "id": f"u{ident}", "name": tool, "input": given or {}}
        )
    usage = {"input_tokens": 10, "cache_read_input_tokens": context - 10}
    return {
        "type": "assistant",
        "message": {"id": ident, "usage": usage, "content": content},
    }


def result(ident: str, text: str) -> dict[str, Any]:
    block = {"type": "tool_result", "tool_use_id": f"u{ident}", "content": text}
    return {"type": "user", "message": {"content": [block]}}


def test_a_continued_request_replays_from_the_digest(tmp_path: Path) -> None:
    rs = load()
    report = {
        "status": "problems",
        "changes": [{"path": f"a{n}.png", "change": "created"} for n in range(300)],
        "problems": [],
    }
    lines = [
        user("Project map …\nUser: Make the Scattergun"),
        call("1", 23_000),
        call("2", 40_000, "precheck"),
        result("2", json.dumps(report)),
        call("3", 80_000),
        user("(unchanged)\nUser: Now the Rivet Driver"),
        call("4", 130_000),
        call("5", 150_000),
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    sessions = [rs.read_session(path)]
    rs.classify(sessions)
    assert [t.kind for t in sessions[0]] == ["fresh", "continued"]
    assert sessions[0][0].results[0].short_chars < sessions[0][0].results[0].chars
    before = rs.replay(sessions, set())[0]
    fresh = rs.replay(sessions, {"fresh"})[0]
    trim = rs.replay(sessions, {"trim"})[0]
    assert before.tokens == [143_000, 280_000]
    start = 23_000 + rs.DIGEST_TOKENS
    assert fresh.tokens[1] == start + (start + 20_000)
    assert trim.tokens[0] < before.tokens[0] and trim.tokens[1] < before.tokens[1]
    assert "| **all** |" in rs.report(tmp_path)
