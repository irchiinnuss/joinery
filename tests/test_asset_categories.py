"""design/asset_categories.toml: the Assets view's categories, as agents read them."""

from __future__ import annotations

from pathlib import Path

from joinery import asset_categories, project_map

#: What the editor writes (asset_categories.gd serialize), byte for byte.
EDITOR_FILE = """# The Assets view's categories, in order. Joinery keeps this file; editing it by hand
# is fine. Each asset is listed by its source file, in one category at most.

[[category]]
name = "Yard props"
assets = [
    "res://art/sources/gas_can.blend",
    "res://art/sources/push_mower.blend",
]

[[category]]
name = "Say \\"hi\\""
assets = []
"""


def _write(project: Path, text: str) -> None:
    path = project / asset_categories.PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_reads_the_editors_file_in_order(tmp_path: Path) -> None:
    _write(tmp_path, EDITOR_FILE)
    assert asset_categories.load(tmp_path) == [
        ("Yard props", ["art/sources/gas_can.blend", "art/sources/push_mower.blend"]),
        ('Say "hi"', []),
    ]


def test_missing_or_broken_file_is_no_categories(tmp_path: Path) -> None:
    assert asset_categories.load(tmp_path) == []
    _write(tmp_path, "[[category]\nname = ")
    assert asset_categories.load(tmp_path) == []


def test_tolerates_hand_edits(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "[meta]\nversion = 3\n[[category]]\nname = 'Yard props'\n"
        'assets = ["res://a.blend", 4]\ncolour = 1\n'
        '[[category]]\nname = ""\nassets = ["res://b.blend"]\n'
        '[[category]]\nname = "Yard props"\nassets = ["res://c.blend", "res://a.blend"]\n'
        '[[category]]\nname = "Other"\nassets = ["res://a.blend"]\n',
    )
    assert asset_categories.load(tmp_path) == [
        ("Yard props", ["a.blend", "c.blend"]),
        ("Other", []),
    ]


def test_project_map_lists_categories(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text('[application]\nconfig/name="Yard"\n')
    _write(tmp_path, EDITOR_FILE)
    text = project_map.build(tmp_path)
    assert "Asset categories (design/asset_categories.toml):" in text
    assert "- Yard props: gas_can.blend, push_mower.blend" in text
    assert '- Say "hi": (empty)' in text
