"""Fewer model calls per request: richer first answers and batched reads.

Every model call re-sends the whole conversation, so a request's tokens follow its
number of calls. discover_tools lists each tool's arguments (no tool_schema round
trip for most calls), details reads several parts of a kept result at once, and long
results stay capped. The same gateway answers for every agent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joinery import compact
from joinery.gateway_rpc import (
    DISCOVER_DETAILED,
    DISCOVER_LISTED,
    search_tools,
    tool_arguments,
)

WRITE_FILE = {
    "type": "object",
    "properties": {
        "projectPath": {"type": "string"},
        "filePath": {"type": "string"},
        "content": {"type": "string"},
        "mode": {"enum": ["replace", "append"]},
        "lines": {"type": "array", "items": {"type": "integer"}},
        "size": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    },
    "required": ["projectPath", "filePath"],
}


def inventory(count: int) -> dict[str, object]:
    return {
        "servers": [{"name": "godot", "connected": True, "tools": []}],
        "tools": [
            {
                "server": "godot",
                "name": f"write_file_{i}",
                "description": "Write a file. " + "x" * 900,
                "inputSchema": WRITE_FILE,
            }
            for i in range(count)
        ],
    }


def test_tool_arguments_are_one_short_line() -> None:
    line = tool_arguments(WRITE_FILE)
    assert line.startswith("projectPath*: string, filePath*: string, content: string")
    assert 'mode: "replace"|"append"' in line
    assert "lines: integer[]" in line
    assert "size: integer" in line and "null" not in line
    assert tool_arguments(None) == "" and tool_arguments({"type": "object"}) == ""


def test_a_huge_schema_is_cut_and_points_at_tool_schema() -> None:
    schema = {"properties": {f"argument_{i}": {"type": "string"} for i in range(80)}}
    line = tool_arguments(schema)
    assert len(line) <= 420
    assert line.endswith("… (tool_schema)")


def test_discover_tools_gives_arguments_and_stays_small() -> None:
    found = search_tools(inventory(40), "write file")
    tools = found["tools"]
    assert len(tools) == DISCOVER_DETAILED
    assert all(t["args"].startswith("projectPath*") for t in tools)
    assert all(len(t["description"]) <= 300 for t in tools)
    # The rest by name only, and the total still counted.
    assert len(found["more"]) == DISCOVER_LISTED - DISCOVER_DETAILED
    assert found["total_matches"] == 40
    assert "tool_schema" in found["hint"]
    # Smaller than the old answer (24 tools with 500-character descriptions).
    assert len(json.dumps(found)) < 24 * 500


def test_a_short_inventory_has_no_more_list() -> None:
    assert "more" not in search_tools(inventory(3), "")


def _kept(tmp_path: Path) -> str:
    result = {
        "status": "problems",
        "problems": [
            {
                "rule": "tests",
                "message": "\n".join(f"line {n} of {i}" for n in range(400)),
            }
            for i in range(4)
        ],
    }
    summary = compact.compact(tmp_path, "precheck", result)
    assert compact.size(summary) < compact.size(result)
    return str(summary["details"]["ref"])


def test_long_results_stay_capped(tmp_path: Path) -> None:
    ref = _kept(tmp_path)
    assert ref.startswith("r")
    big = {"text": "y" * 100_000}
    assert compact.size(compact.compact(tmp_path, "call", big)) <= compact.BUDGET + 600


def test_details_reads_several_parts_in_one_call(tmp_path: Path) -> None:
    ref = _kept(tmp_path)
    paths = [f"problems/{i}/message" for i in range(3)]
    page = compact.details_many(tmp_path, ref, paths)
    assert [p["path"] for p in page["parts"]] == paths
    assert page["parts"][1]["lines"].startswith("line 0 of 1")
    # They share one page: the whole answer stays near one page's size.
    assert len(json.dumps(page)) < compact.PAGE_CHARS + 2000
    assert all("more" in p for p in page["parts"])


def test_details_names_a_missing_part_and_keeps_the_rest(tmp_path: Path) -> None:
    ref = _kept(tmp_path)
    page = compact.details_many(tmp_path, ref, ["problems/0/rule", "problems/9"])
    assert page["parts"][0]["lines"] == "tests"
    assert "No part" in page["parts"][1]["error"]


def test_details_refuses_too_many_paths_and_unknown_refs(tmp_path: Path) -> None:
    ref = _kept(tmp_path)
    with pytest.raises(ValueError):
        compact.details_many(tmp_path, ref, ["a"] * (compact.MAX_PATHS + 1))
    with pytest.raises(ValueError):
        compact.details_many(tmp_path, "nope", ["problems/0"])
