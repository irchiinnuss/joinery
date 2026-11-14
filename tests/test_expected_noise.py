"""Expected engine noise: one classifier (``console_errors.expected``) that every check
uses, so a VR game with no OpenXR runtime that starts on the desktop counts no error."""

from __future__ import annotations

import json
from pathlib import Path

from joinery import (
    console_errors,
    done_when,
    playtest_report,
    replays,
    saves,
    share_build,
)

#: What Godot 4.7 prints with the VR feature on and no OpenXR runtime (a real log).
XR_START = """Godot Engine v4.7.2.stable.official.ed1daf0bf - https://godotengine.org
Error [GENERAL | xrEnumerateInstanceExtensionProperties | OpenXR-Loader] : RuntimeInterface::LoadRuntimes - failed to load a runtime
ERROR: OpenXR: Failed to enumerate number of extension properties
   at: load_supported_extensions (modules/openxr/openxr_api.cpp:429)
WARNING: OpenXR was requested but failed to start.
HMD was not detected or a required feature was not supported.

Check logged errors in debugger for more details.

Godot will start in normal mode.

     at: initialize_openxr_module (modules/openxr/register_types.cpp:262)

VR: no OpenXR runtime or headset; playing on the desktop
"""
#: The same failure when the engine did not go on in desktop mode.
XR_NO_FALLBACK = """ERROR: OpenXR: Failed to enumerate number of extension properties
   at: load_supported_extensions (modules/openxr/openxr_api.cpp:429)
"""
GAME_ERROR = """SCRIPT ERROR: Invalid call. Nonexistent function 'open' in base 'Nil'.
   at: _on_body_entered (res://scripts/door.gd:12)
"""


def test_openxr_lines_are_expected_only_when_the_desktop_fallback_started() -> None:
    line = "ERROR: OpenXR: Failed to enumerate number of extension properties"
    assert console_errors.expected(line, XR_START)
    assert console_errors.expected(line.removeprefix("ERROR: "), XR_START)
    assert console_errors.expected("WARNING: OpenXR was requested but failed to start.", XR_START)
    assert not console_errors.expected(line, XR_NO_FALLBACK)
    assert not console_errors.expected("SCRIPT ERROR: Invalid call.", XR_START)
    pattern = replays._ERROR
    assert console_errors.error_lines(XR_START, pattern) == []
    assert console_errors.error_lines(XR_NO_FALLBACK, pattern) == [line]
    assert console_errors.error_lines(XR_START + GAME_ERROR, pattern) == [
        "SCRIPT ERROR: Invalid call. Nonexistent function 'open' in base 'Nil'."
    ]


def test_the_editor_error_list_ignores_it_too() -> None:
    entries = console_errors.parse(XR_START)
    assert entries, "the line is read as an error"
    assert all(console_errors.ignored(entry) for entry in entries)
    assert not all(console_errors.ignored(e) for e in console_errors.parse(XR_NO_FALLBACK))


def _report(output: str) -> dict[str, object]:
    bot = {"finished": True, "seconds_played": 60.0, "fps": 60, "log": []}
    return playtest_report.build(
        bot, output, 0, options={"seed": 1, "seconds": 60.0, "project": "P"}
    )


def test_a_playtest_reports_no_engine_error_for_it() -> None:
    report = _report(XR_START)
    assert [i for i in report["items"] if i["kind"] in {"error", "warning"}] == []
    failing = _report(XR_NO_FALLBACK)
    assert any(i["kind"] == "error" for i in failing["items"])


def test_done_when_no_errors_passes_with_it(tmp_path: Path) -> None:
    criterion = done_when.Criterion("d1", "No errors", "no_errors", seconds=60)
    report = {**_report(XR_START), "played_seconds": 60.0}
    result = done_when._played(criterion, report, tmp_path, "")
    assert result.status == "pass", result.evidence


def _replay_file(tmp_path: Path) -> replays.Replay:
    path = tmp_path / "walk.replay"
    path.write_text(
        json.dumps({"format": replays.FORMAT, "physics_ticks": 60, "seed": 1})
        + "\n"
        + json.dumps({"end": {"frame": 60, "errors": 0}})
        + "\n"
    )
    return replays.read(path)


def test_replay_run_does_not_count_it(tmp_path: Path) -> None:
    replay = _replay_file(tmp_path)
    result = json.dumps({"ok": True, "checks": 1, "errors": [], "divergence": {}})
    console = XR_START + replays.RESULT_PREFIX + result + "\n"
    assert replays.outcome("walk", console, 0, replay).status == "passed"
    broken = XR_NO_FALLBACK + replays.RESULT_PREFIX + result + "\n"
    assert replays.outcome("walk", broken, 0, replay).status == "errors"


def test_saves_check_does_not_count_it(tmp_path: Path) -> None:
    save = saves.Kept(tmp_path / "slot_1.json", "1.0", {}, {})
    line = saves.RESULT_PREFIX + json.dumps({"status": "loaded"})
    assert saves.judge(save, XR_START + line + "\n").status == "passed"
    assert saves.judge(save, XR_NO_FALLBACK + line + "\n").status == "failed"


def test_the_share_build_boot_check_does_not_count_it() -> None:
    assert share_build._first_error(XR_START) == ""
    assert share_build._first_error(XR_NO_FALLBACK).startswith("ERROR: OpenXR")
