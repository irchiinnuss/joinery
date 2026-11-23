"""Whole reads of long files are refused for one call, in every agent's shape."""

from pathlib import Path
from typing import Any

import pytest

from joinery import hook, read_budget
from joinery.policy import Severity
from joinery.shell_words import pipelines

RECIPE = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "game").mkdir()
    (tmp_path / "game/long.gd").write_text(
        "".join(f"var line_{n} := {n}\n" for n in range(900))
    )
    (tmp_path / "game/short.gd").write_text("extends Node\n" * 40)
    (tmp_path / "game/wide.gd").write_text(("x" * 200 + "\n") * 80)
    (tmp_path / "recipes/history").mkdir(parents=True)
    (tmp_path / f"recipes/{RECIPE}.py").write_text("print('op')\n" * 1000)
    (tmp_path / f"recipes/history/{RECIPE}.toml").write_text('tool = "blender"\n')
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("JOINERY_STAGE", "none")
    monkeypatch.delenv("JOINERY_AGENT_ROLE", raising=False)
    return tmp_path


def claude_read(path: Path, **extra: Any) -> dict[str, Any]:
    return {"tool_name": "Read", "tool_input": {"file_path": str(path), **extra}}


def bash(command: str, cwd: Path) -> dict[str, Any]:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}


def test_whole_read_of_a_long_file_is_denied_with_its_length(project: Path) -> None:
    verdict = hook.violation(claude_read(project / "game/long.gd"))
    assert verdict is not None and verdict.severity is Severity.DENY
    assert "game/long.gd has 900 lines" in verdict.reason
    assert "search_project" in verdict.reason and "offset and limit" in verdict.reason


def test_wide_file_counts_by_characters(project: Path) -> None:
    verdict = hook.violation(claude_read(project / "game/wide.gd"))
    assert verdict is not None and "80 lines (16k characters)" in verdict.reason


def test_small_files_and_ranges_pass(project: Path) -> None:
    assert hook.violation(claude_read(project / "game/short.gd")) is None
    ranged = claude_read(project / "game/long.gd", offset=300, limit=200)
    assert hook.violation(ranged) is None


def test_offset_alone_is_not_a_range(project: Path) -> None:
    assert hook.violation(claude_read(project / "game/long.gd", offset=10)) is not None


@pytest.mark.parametrize(
    ("tool", "key"),
    [("read_file", "absolute_path"), ("read_file", "file_path"), ("read", "filePath")],
)
def test_gemini_and_opencode_reads(project: Path, tool: str, key: str) -> None:
    raw = {"tool_name": tool, "tool_input": {key: str(project / "game/long.gd")}}
    assert hook.violation(raw) is not None
    raw["tool_input"]["limit"] = 100
    assert hook.violation(raw) is None


def test_relative_path_resolves_against_cwd(project: Path) -> None:
    raw = {"tool_name": "Read", "tool_input": {"file_path": "game/long.gd"}}
    raw["cwd"] = str(project)
    assert hook.violation(raw) is not None


def test_pictures_are_not_judged(project: Path) -> None:
    (project / "big.png").write_bytes(b"\x89PNG" + b"a\n" * 50_000)
    assert hook.violation(claude_read(project / "big.png")) is None


def test_sessions_joinery_did_not_start_are_not_judged(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JOINERY_STAGE")
    assert hook.violation(claude_read(project / "game/long.gd")) is None


@pytest.mark.parametrize(
    "command",
    [
        "cat game/long.gd",
        "/bin/cat -n game/long.gd",
        "head -n 2000 game/long.gd",
        "head -2000 game/long.gd",
        "tail -n +1 game/long.gd",
        "cd game && cat long.gd",
        "ls; cat game/long.gd",
    ],
)
def test_whole_shell_reads_are_denied(project: Path, command: str) -> None:
    verdict = hook.violation(bash(command, project))
    assert verdict is not None and "too long to read whole" in verdict.reason


@pytest.mark.parametrize(
    "command",
    [
        "cat game/short.gd",
        "head -n 80 game/long.gd",
        "head game/long.gd",
        "tail -n 50 game/long.gd",
        "sed -n '1,200p' game/long.gd",
        "cat game/long.gd | grep line_5",
        "wc -l game/long.gd",
    ],
)
def test_bounded_shell_reads_pass(project: Path, command: str) -> None:
    assert hook.violation(bash(command, project)) is None


def test_recipe_refusal_points_at_find_recipe(project: Path) -> None:
    verdict = hook.violation(claude_read(project / f"recipes/{RECIPE}.py"))
    assert verdict is not None
    assert f"find_recipe(recipe='{RECIPE}')" in verdict.reason


def test_missing_recipe_file_names_the_ones_that_exist(project: Path) -> None:
    verdict = hook.violation(claude_read(project / f"recipes/{RECIPE}.toml", limit=5))
    assert verdict is not None
    assert f"recipes/{RECIPE}.py" in verdict.reason
    assert f"recipes/history/{RECIPE}.toml" in verdict.reason
    grep = {
        "tool_name": "Grep",
        "tool_input": {"pattern": "x", "path": str(project / f"recipes/{RECIPE}.toml")},
    }
    assert hook.violation(grep) is not None
    shell = hook.violation(bash(f"grep -n tool recipes/{RECIPE}.toml", project))
    assert shell is not None and "doesn't exist" in shell.reason


def test_missing_recipe_in_the_staged_change(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = "fedcba9876543210fedcba9876543210"
    stage = project / ".joinery/transactions/t1/stage/recipes"
    stage.mkdir(parents=True)
    (stage / f"{other}.toml").write_text("")
    monkeypatch.setenv("JOINERY_STAGE", "t1")
    verdict = read_budget.missing_recipe(
        project / f"recipes/{other}.toml", project.resolve()
    )
    assert verdict is not None
    assert f".joinery/transactions/t1/stage/recipes/{other}.toml" in verdict.reason


def test_pipelines_group_piped_commands() -> None:
    assert pipelines("cat a | rg x; head b") == [
        [["cat", "a"], ["rg", "x"]],
        [["head", "b"]],
    ]
    assert pipelines("cat $(ls)") is None
