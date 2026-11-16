"""Short forms of check reports, tool operations, blockouts and the game's setup."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from joinery import compact, short_results


def texel(n: int) -> dict[str, Any]:
    return {
        "check": "art_bible",
        "message": f"Look check failed: game/hall.tscn: art/t{n}_albedo.png texel "
        "density 32 px/m, the look asks for 64 ± 20%",
        "paths": ["game/hall.tscn"],
        "items": [
            {
                "rule": "art_bible.texel_density",
                "file": "game/hall.tscn",
                "node": f"art/t{n}_albedo.png",
                "measured": "32 px/m",
                "expected": "64 ± 20%",
                "fix": "Rescale the UVs or the texture to the look's texel density.",
            }
        ],
    }


def precheck() -> dict[str, Any]:
    trace = "\n".join(f"  [{n}] _run (res://tests/play.gd:{n})" for n in range(30))
    return {
        "status": "problems",
        "changes": [
            {"path": f"art/props/p{n}.glb{suffix}", "change": "created"}
            for n in range(150)
            for suffix in ("", ".import")
        ],
        "problems": [
            *(texel(n) for n in range(9)),
            {
                "check": "tests",
                "message": "The test res://tests/core_test.gd failed: FAIL: menu\n"
                + trace,
                "paths": ["tests/core_test.gd"],
                "items": [{"rule": "tests.failed", "file": "", "fix": "Fix it."}],
            },
        ],
        "ran": ["references", "provenance", "art bible"],
        "attempts_left": 3,
    }


def test_a_check_report_counts_its_files_and_folds_alike_problems() -> None:
    short, cuts = short_results.shorten(precheck())
    assert short["changes"]["counts"] == {"created": 300}
    assert short["changes"]["first"][:2] == ["art/props/p0.glb", "art/props/p1.glb"]
    problems = short["problems"]
    assert len(problems) == 2
    first = problems[0]
    assert first["alike"] == 8
    assert first["alike_at"] == ["problems/1", "…", "problems/8"]
    assert first["items"][0]["fix"].startswith("Rescale the UVs")
    assert problems[1]["message"].endswith("(+30 lines)")
    assert short["attempts_left"] == 3 and short["status"] == "problems"
    assert any("alike folded" in cut for cut in cuts)
    assert compact.size(short) < compact.size(precheck()) / 5


def test_a_clean_small_report_is_left_alone() -> None:
    report = {"status": "passed", "changes": [{"path": "a.gd", "change": "modified"}]}
    report["problems"] = []
    assert short_results.shorten(report) == (report, [])


def painted_map() -> dict[str, Any]:
    inner = {
        "source": "art/maps/tin.kra",
        "outputs": ["art/maps/tin.kra", "art/maps/tin.png"],
        "recipe": "recipes/b.toml",
        "status": "staged",
        "requires_review": True,
    }
    meshes = [{"mesh": "Tin", "vertices": 336, "morph_targets": 0, "target_names": []}]
    return {
        "source": "art/sources/tin.blend",
        "outputs": ["art/sources/tin.blend", "art/props/tin.glb", "art/t/tin.png"],
        "recipe": "recipes/a.toml",
        "status": "staged",
        "requires_review": True,
        "models": {"art/props/tin.glb": meshes},
        "painting": inner,
        "added": {**inner, "outputs": ["art/sources/tin.blend"], "models": {}},
    }


def test_an_operation_keeps_its_files_not_its_steps_records() -> None:
    short, cuts = short_results.shorten(painted_map())
    assert "painting" not in short and "added" not in short
    assert short["also_made"] == ["art/maps/tin.kra", "art/maps/tin.png"]
    assert short["models"] == {"art/props/tin.glb": ["Tin: 336 vertices"]}
    assert short["recipe"] == "recipes/a.toml" and short["status"] == "staged"
    assert cuts


def test_a_tool_result_is_shortened_inside_its_text() -> None:
    result = {
        "content": [{"type": "text", "text": json.dumps(painted_map())}],
        "structuredContent": painted_map(),
        "isError": False,
    }
    short, cuts = short_results.shorten(result)
    text = json.loads(short["content"][0]["text"])
    assert "painting" not in text and "painting" not in short["structuredContent"]
    assert cuts


def test_a_blockout_lists_only_the_goals_not_reached() -> None:
    targets = [
        {"id": f"Goal{n}", "kind": "pickup", "reached": n < 6, "closest": 0.8}
        for n in range(13)
    ]
    fact = "Walkable: spawn reaches 6 of 13 goals"
    result = {
        "status": "staged",
        "walk": {"fact": fact, "targets": targets},
        "facts": [fact],
    }
    short, _ = short_results.shorten(result)
    assert short["walk"]["reached"] == 6
    assert [t["id"] for t in short["walk"]["targets"]] == [
        f"Goal{n}" for n in range(6, 13)
    ]
    assert "facts" not in short


def test_the_setup_counts_each_units_files() -> None:
    setup = {
        "game": {
            "blueprint": "first-person-3d",
            "units": {"_core": {"kind": "core", "files": ["a.gd", "b.gd"]}},
        }
    }
    short, _ = short_results.shorten(setup)
    assert short["game"]["units"]["_core"] == {"kind": "core", "files": 2}


def test_compact_keeps_the_full_report_for_details(tmp_path: Path) -> None:
    sent = compact.compact(tmp_path, "precheck", precheck())
    ref = sent["details"]["ref"]
    part = compact.details(tmp_path, ref, "problems/5/items/0/node")
    assert "art/t5_albedo.png" in part["lines"]
    # Even a result under the budget gets its short form, and a reference.
    small = compact.compact(tmp_path, "call", painted_map())
    assert "painting" not in small and small["details"]["ref"]
