"""The add-on reads command output and files without printing parse errors.

``JSON.parse_string`` prints "Parse JSON failed" for every text that isn't JSON.
The sidebar reads `joinery … --json` output (``joinery status`` prints one object
over many lines) and files still being written many times a minute, so each
such call filled the editor's Output, and the console-error feed, with errors.
The add-on reads JSON through ``command_job.gd`` (``parse``, ``object``) instead.
"""

from __future__ import annotations

from pathlib import Path

ADDON = Path(__file__).resolve().parents[1] / "godot_addon" / "joinery"
#: Reads only the JSON the sidebar itself wrote into an edit's expectations.
KNOWN = {"plugin.gd"}


def test_the_addon_never_parses_json_out_loud() -> None:
    loud = [
        f"{path.relative_to(ADDON)}:{number}"
        for path in sorted(ADDON.rglob("*.gd"))
        if path.name not in KNOWN
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if "JSON.parse_string(" in line and not line.lstrip().startswith("#")
    ]
    assert loud == []
