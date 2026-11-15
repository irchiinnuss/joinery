"""Project rules for some files only: where they are written, which files they hold
for, how they reach the agent (the turn and gateway results, reads and writes alike),
that the stable prefix never carries them, and editing them as a reviewed change."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge import provider_process
from bridge.protocol import Prompt
from joinery import cli, memory, scoped_rules
from joinery.gateway import ProjectGateway, result_text
from joinery.transactions import FileTransaction
from tests.test_review_view import project, run, wire  # noqa: F401  (fixture)

MEMORY = """\
# Journeyman

## Rules
- Signals are snake_case.

## When working on game/ai/**, game/enemies/*.gd
- Enemies use the behaviour feature, never a hand-rolled state machine.

## Facts
- The player is 1.8 m tall.
"""
PLAIN = """\
# Journeyman

## Rules
- Signals are snake_case.

## Facts
- The player is 1.8 m tall.
"""


def write_memory(root: Path, text: str = MEMORY) -> None:
    (root / "project.godot").write_text("config_version=5\n")
    (root / memory.FILE).write_text(text, encoding="utf-8")


def test_sections_and_rule_files_are_read(tmp_path: Path) -> None:
    write_memory(tmp_path)
    folder = tmp_path / scoped_rules.FOLDER
    folder.mkdir(parents=True)
    (folder / "ui.md").write_text(
        "paths: game/ui/**\nEvery screen anchors to the safe area.\n"
    )
    (folder / "broken.md").write_text("Forgot the paths line.\n")
    rules = scoped_rules.load(tmp_path)
    assert [(r.globs, r.source) for r in rules] == [
        (("game/ai/**", "game/enemies/*.gd"), "JOURNEYMAN.md"),
        (("game/ui/**",), ".joinery/rules/ui.md"),
    ]
    assert rules[0].text.startswith("- Enemies use the behaviour feature")
    assert scoped_rules.problems(tmp_path) == [
        ".joinery/rules/broken.md: the first line must be 'paths: …'"
    ]


@pytest.mark.parametrize(
    ("glob", "path", "hit"),
    [
        ("game/ai/**", "game/ai/brain/chase.gd", True),
        ("game/ai/**", "game/aim.gd", False),
        ("game/enemies/*.gd", "game/enemies/bat.gd", True),
        ("game/enemies/*.gd", "game/enemies/bats/bat.gd", False),
        ("*.gdshader", "art/water/water.gdshader", True),
        ("**/ui/*.tscn", "game/ui/menu.tscn", True),
        ("game/ui/", "game/ui/hud/hp.tscn", True),
        ("game/ai/**", "res://game/ai/x.gd", True),
    ],
)
def test_patterns(glob: str, path: str, hit: bool) -> None:
    rule = scoped_rules.Rule((glob,), "x", "JOURNEYMAN.md")
    assert bool(scoped_rules.matching([rule], [path])) is hit


def test_scoped_sections_stay_out_of_the_stable_prefix(tmp_path: Path) -> None:
    """The memory block and the turn's whole stable text are byte-identical with or
    without rules for particular files, so adding one never breaks the cache."""
    with_rules, without = tmp_path / "a", tmp_path / "b"
    for root, text in ((with_rules, MEMORY), (without, PLAIN)):
        root.mkdir()
        write_memory(root, text)
    folder = with_rules / scoped_rules.FOLDER
    folder.mkdir(parents=True)
    (folder / "ui.md").write_text("paths: game/ui/**\nAnchor to the safe area.\n")
    assert memory.block(with_rules) == memory.block(without)
    assert "behaviour feature" not in memory.block(with_rules)

    def prompt(root: Path) -> Prompt:
        return Prompt(
            text="Make the bat faster.",
            project_dir=str(root),
            editor_context={"scene_path": "res://game/enemies/bat.tscn"},
            target_context=({"id": "res://game/enemies/bat.gd"},),
        )

    stable_a = provider_process.stable_text(prompt(with_rules))
    stable_b = provider_process.stable_text(prompt(without))
    assert stable_a.replace(str(with_rules), "P") == stable_b.replace(str(without), "P")
    assert "behaviour feature" not in stable_a
    # The rule rides in the turn's own part instead.
    turn = provider_process.turn_part(prompt(with_rules))
    assert "behaviour feature" in turn
    assert "game/enemies/bat.gd matches" in turn
    assert "behaviour feature" not in provider_process.turn_part(prompt(without))


def test_memory_limit_counts_only_the_general_part(tmp_path: Path) -> None:
    write_memory(tmp_path, PLAIN + "\n## When working on game/**\n" + "- x\n" * 4000)
    found = memory.status(tmp_path)
    assert not found["over"] and found["scoped_rules"] == 1
    memory.remembered(memory.read(tmp_path), "Nodes are PascalCase.")


def test_the_gateway_gives_rules_on_writes_new_files_and_reads(
    project: Path,  # noqa: F811
) -> None:
    write_memory(project)

    async def step() -> list[str]:
        gateway = wire(ProjectGateway(project))
        try:
            arguments = {
                "projectPath": str(project),
                "filePath": "game/enemies/bat.gd",
                "content": "extends Node\n",
            }
            # A file that doesn't exist yet: the rule still comes with its creation.
            created = await gateway.call("godot", "write_file", arguments)
            again = await gateway.call(
                "godot", "write_file", {**arguments, "content": "extends Node2D\n"}
            )
            other = await gateway.call(
                "godot",
                "write_file",
                {**arguments, "filePath": "game/hud.gd", "content": "extends Node\n"},
            )
            return [result_text(r) for r in (created, again, other)]
        finally:
            await gateway.close()

    created, again, other = run(step)
    assert "Project rules for the files this call touched" in created
    assert "behaviour feature" in created and "game/enemies/bat.gd matches" in created
    # Once per turn, and never for a file no rule names.
    assert "behaviour feature" not in again
    assert "behaviour feature" not in other

    async def read() -> str:
        gateway = wire(ProjectGateway(project))
        try:
            (project / "game/ai").mkdir(parents=True)
            (project / "game/ai/chase.gd").write_text("extends Node\n")
            result = await gateway.call(
                "godot",
                "read_file",
                {"projectPath": str(project), "filePath": "res://game/ai/chase.gd"},
            )
            return result_text(result)
        finally:
            await gateway.close()

    assert "behaviour feature" in run(read)


def test_remember_for_files_adds_a_scoped_rule(tmp_path: Path) -> None:
    parsed = scoped_rules.parse_remember("for game/ai/**: Bosses telegraph attacks.")
    assert parsed == (("game/ai/**",), "Bosses telegraph attacks.")
    assert scoped_rules.parse_remember("Signals are snake_case.") is None
    text = scoped_rules.added(MEMORY, ("game/ai/**", "game/enemies/*.gd"), "Use seeds.")
    section = text.split("## When working on game/ai/**", 1)[1].split("## Facts")[0]
    assert section.strip().splitlines()[-1] == "- Use seeds."
    fresh = scoped_rules.added(PLAIN, ("*.gdshader",), "Shaders stay unshaded.")
    assert fresh.endswith("## When working on *.gdshader\n- Shaders stay unshaded.\n")
    with pytest.raises(ValueError, match="already has that rule"):
        scoped_rules.added(
            MEMORY,
            ("game/ai/**", "game/enemies/*.gd"),
            "Enemies use the behaviour feature, never a hand-rolled state machine.",
        )


def test_rules_add_is_a_reviewed_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_memory(tmp_path, PLAIN)
    code = cli.main(
        [
            "rules",
            "--project",
            str(tmp_path),
            "add",
            "--path",
            "game/ui/**",
            "Every",
            "screen",
            "anchors",
            "to",
            "the",
            "safe",
            "area.",
            "--json",
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["state"] == "review"
    # Nothing changes until the user accepts it.
    assert "game/ui" not in (tmp_path / memory.FILE).read_text()
    tx = FileTransaction.load(tmp_path, data["transaction_id"])
    staged = (tx.stage / memory.FILE).read_text()
    assert (
        "## When working on game/ui/**\n- Every screen anchors to the safe area."
        in staged
    )
    tx.reject()

    assert (
        cli.main(["rules", "--project", str(tmp_path), "match", "game/ui/x.tscn"]) == 0
    )
    assert "No rule holds" in capsys.readouterr().out
