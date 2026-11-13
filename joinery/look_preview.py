"""The look at a glance, for the sidebar: the palette as colours and the reference board.

The art direction page leads with these (a strip of swatches, then the reference
pictures), and the art gate in the chat shows a miniature of the same two. Both
read the bible's own words: the Palette section's `| Role | Hex | Use |` table,
with the checked rules' `[palette.roles]` filling hex values the table leaves out.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from joinery import bible_lint

#: The strip shows at most this many roles.
MAX_ROLES = 5
#: The art gate shows at most this many reference pictures.
GATE_REFERENCES = 3
_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
#: Roles about the interface or game state rather than the art: shown last.
_SECONDARY = ("ui", "danger", "safe", "warning", "error")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _palette_section(text: str) -> str:
    """The body of the bible's Palette section ("## 7. Palette (checked)")."""
    lines = text.splitlines()
    body: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            heading = re.sub(r"^##\s+(?:\d+[a-z]?\.\s*)?", "", line).strip().lower()
            inside = heading.startswith("palette")
            continue
        if inside:
            body.append(line)
    return "\n".join(body)


def _key(role: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", role.lower()).strip("_")


def palette_roles(
    text: str, rules_roles: dict[str, str] | None = None
) -> list[dict[str, str]]:
    """The palette's roles as [{role, hex, use}], art roles first, at most MAX_ROLES.

    A role whose hex is not written yet keeps an empty hex (an empty slot). No table
    and no checked roles gives [] (nothing chosen yet).
    """
    roles = dict(rules_roles or {})
    rows: list[dict[str, str]] = []
    for line in _palette_section(text).splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _cells(line) + ["", ""]
        role = cells[0]
        if not role or role.lower() == "role" or re.fullmatch(r":?-{3,}:?", role):
            continue
        found = _HEX.fullmatch(cells[1])
        note = ""
        named = re.match(r"^(.*?)\s*\((.*)\)\s*$", role)
        if named is not None:
            role, note = named.group(1), named.group(2)
        hex_value = found.group(0) if found is not None else ""
        if not hex_value:
            hex_value = roles.get(_key(role), "")
        use = cells[2] or note
        rows.append({"role": role, "hex": hex_value.lower(), "use": use})
    if not rows:
        rows = [
            {
                "role": name.replace("_", " ").capitalize(),
                "hex": value.lower(),
                "use": "",
            }
            for name, value in roles.items()
            if _HEX.fullmatch(value)
        ]
    art = [row for row in rows if not row["role"].lower().startswith(_SECONDARY)]
    rest = [row for row in rows if row not in art and row["hex"]]
    chosen = (art + rest)[:MAX_ROLES]
    if not any(row["hex"] for row in chosen):
        return [] if not rows else chosen
    return chosen


def look(project: Path, report: dict[str, Any] | None = None) -> dict[str, Any]:
    """{palette, references} for the bible in `project` ({} parts when it has none)."""
    path = project / bible_lint.BIBLE
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {"palette": [], "references": []}
    if report is None:
        try:
            report = bible_lint.summary(project)
        except (OSError, ValueError):
            report = {}
    rules = report.get("rules") or {}
    palette = rules.get("palette") if isinstance(rules, dict) else None
    roles = palette.get("roles") if isinstance(palette, dict) else None
    return {
        "palette": palette_roles(text, roles if isinstance(roles, dict) else None),
        "references": [
            {
                "id": str(row.get("id", "")),
                "reference": str(row.get("reference", "")),
                "kind": str(row.get("kind", "")),
                "take": str(row.get("take", "")),
                "avoid": str(row.get("avoid", "")),
                "file": str(row.get("file", "")) if row.get("file_exists") else "",
            }
            for row in report.get("references", [])
        ],
    }


def gate_preview(project: Path) -> dict[str, Any]:
    """The art gate's miniature: the palette, and the first pictures on the board."""
    found = look(project)
    pictures = [ref for ref in found["references"] if ref["file"]]
    return {"palette": found["palette"], "references": pictures[:GATE_REFERENCES]}
