"""The look at a glance: palette roles as colours, and the art gate's miniature."""

from __future__ import annotations

from pathlib import Path

from joinery import look_preview

TABLE = (
    "# Art Bible\n\n## 7. Palette (checked)\n| Role | Hex | Use |\n|---|---|---|\n"
    "| Primary | #…… | |\n| Secondary | #…… | |\n"
    "\n## 8. Lighting recipes\n| Role | Hex | Use |\n| Not | #000000 | palette |\n"
)


def test_roles_without_hex_are_empty_slots_and_checked_roles_fill_them() -> None:
    roles = look_preview.palette_roles(TABLE)
    assert roles == [
        {"role": "Primary", "hex": "", "use": ""},
        {"role": "Secondary", "hex": "", "use": ""},
    ]
    filled = look_preview.palette_roles(TABLE, {"primary": "#AABBCC"})
    assert filled[0]["hex"] == "#aabbcc" and filled[1]["hex"] == ""


def test_checked_roles_alone_make_the_strip_and_no_palette_is_empty() -> None:
    text = "# Art Bible\n\n## 7. Palette\nWarm wood, sea blue accent.\n"
    assert look_preview.palette_roles(text) == []
    roles = look_preview.palette_roles(
        text, {"shadow_tint": "#112233", "ui_text": "#fff"}
    )
    assert [r["role"] for r in roles] == ["Shadow tint", "Ui text"]


def test_the_strip_keeps_five_roles_art_first() -> None:
    rows = "".join(f"| UI {n} | #00000{n} | |\n" for n in range(3))
    rows += "".join(f"| Art {n} | #11111{n} | |\n" for n in range(4))
    text = "## 7. Palette\n| Role | Hex | Use |\n|---|---|---|\n" + rows
    roles = look_preview.palette_roles(text)
    assert [r["role"] for r in roles] == ["Art 0", "Art 1", "Art 2", "Art 3", "UI 0"]


def test_gate_preview_keeps_three_pictures(tmp_path: Path) -> None:
    (tmp_path / "art/reference").mkdir(parents=True)
    rows = ""
    for n in range(4):
        (tmp_path / f"art/reference/r{n}.png").write_bytes(b"png")
        rows += f"| R{n} | ref {n} | photo | take | not | art/reference/r{n}.png |\n"
    (tmp_path / "art/bible.md").write_text(
        "# Art Bible\n<!-- joinery-bible: 2 -->\n\n## 3. References\n"
        "| # | Reference | Kind | Take this | Not that | File |\n"
        "|---|---|---|---|---|---|\n"
        + rows
        + "| R9 | Hob | game | shapes | greens | |\n"
    )
    preview = look_preview.gate_preview(tmp_path)
    assert [r["id"] for r in preview["references"]] == ["R0", "R1", "R2"]
    assert preview["palette"] == []


def test_no_bible_has_nothing_to_show(tmp_path: Path) -> None:
    assert look_preview.gate_preview(tmp_path) == {
        "palette": [],
        "references": [],
        "state": "missing",
        "note": "",
    }
