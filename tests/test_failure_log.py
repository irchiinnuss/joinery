"""A creative tool's failed run: the error leads, the log follows without the chatter.

The Krita and Blender logs are as Yard Day's agent got them; the sclang ones follow
sclang 3.13's own wording around a patch that raised and one that doesn't parse.
"""

from __future__ import annotations

from pathlib import Path

from joinery import compact, failure_log

LOGS = Path(__file__).parent / "fixtures" / "tool_logs"
PREFIX = "krita did not complete the document operation"


def _log(name: str) -> str:
    return (LOGS / f"{name}.txt").read_text()


def _lead(message: str) -> str:
    """What the message says before its log."""
    return message.split("\n\nLog:\n", 1)[0]


def test_a_krita_script_error_leads_with_the_exception_and_its_line() -> None:
    message = failure_log.failure(PREFIX, _log("krita_assert"), app="Krita")
    assert _lead(message) == (
        f"{PREFIX}: AssertionError: Create or open a native Krita document. "
        "at recipes/9a7df0f17c424ab1b04775d65da7e8b0.py line 71: "
        "assert document is not None, 'Create or open a native Krita document'."
    )
    # The log follows, without Krita's start-up chatter.
    log = message.split("\n\nLog:\n", 1)[1]
    assert log.startswith("Traceback (most recent call last):")
    assert "krita.lib.resources" not in log and "PYTHONPATH" not in log
    assert "QObject::startTimer" not in log and "unknown action" not in log


def test_an_error_outside_the_operation_script_names_no_line() -> None:
    lead = _lead(failure_log.failure(PREFIX, _log("krita_unicode"), app="Krita"))
    assert lead == (
        f"{PREFIX}: UnicodeDecodeError: 'ascii' codec can't decode byte 0xce in "
        "position 9437: ordinal not in range(128)."
    )


def test_a_blender_traceback_leads_with_the_exception() -> None:
    lead = _lead(
        failure_log.failure(
            "blender did not complete the document operation",
            _log("blender_traceback"),
        )
    )
    assert lead.startswith(
        "blender did not complete the document operation: "
        "TypeError: exceptions must derive from BaseException. "
        "at recipes/3778d5e0a35c450da5c27eeef2373a14.py line 140: "
    )


def test_an_sclang_error_leads_with_its_error_lines() -> None:
    message = failure_log.failure(
        "supercollider did not complete the document operation",
        _log("sclang_error"),
        app="SuperCollider",
    )
    assert _lead(message) == (
        "supercollider did not complete the document operation: "
        "ERROR: Message 'ar' not understood. "
        "the patch raised an error; see the SuperCollider error above."
    )
    log = message.split("\n\nLog:\n", 1)[1]
    assert log.startswith("ERROR: Message 'ar' not understood.")
    for chatter in ("compiling class library", "Found 821", "Welcome to", "clientID"):
        assert chatter not in log


def test_an_sclang_parse_error_says_where() -> None:
    lead = _lead(failure_log.failure("supercollider failed", _log("sclang_parse")))
    assert lead == (
        "supercollider failed: ERROR: syntax error, unexpected NAME, expecting ')'. "
        "line 4 char 9. "
        "the patch does not parse; see the SuperCollider error above."
    )


def test_sclang_stopped_while_compiling_says_so() -> None:
    # Yard Day's: the whole log was the compile's first line.
    message = failure_log.failure(
        "supercollider did not complete the document operation",
        _log("sclang_stopped"),
    )
    assert message == (
        "supercollider did not complete the document operation: sclang stopped "
        "while compiling its class library, before the patch ran, and printed no "
        "error."
    )


def test_a_log_with_no_error_line_gives_its_last_line() -> None:
    message = failure_log.failure("x failed", "starting\nnative save failed\n", app="X")
    assert _lead(message) == (
        "x failed: X printed no error; its last line was: native save failed"
    )
    assert failure_log.failure("x failed", "") == "x failed: The app printed nothing."


def test_a_long_log_keeps_its_end() -> None:
    long = "\n".join(f"step {i}" for i in range(5000)) + "\nRuntimeError: boom"
    message = failure_log.failure("x failed", long)
    assert _lead(message) == "x failed: RuntimeError: boom."
    log = message.split("\n\nLog:\n", 1)[1]
    assert log.startswith("…") and log.endswith("RuntimeError: boom")
    assert len(log) <= failure_log.LOG_KEEP + 1


def test_through_the_gateway_a_long_failure_keeps_its_lead_and_pages_the_log(
    tmp_path: Path,
) -> None:
    # A tool error as the gateway serialises it: the summary keeps the first lines,
    # so the error is read first; the whole log is one details call away.
    noisy = "\n".join(f"render tile {i} of 400" for i in range(400))
    message = failure_log.failure(PREFIX, noisy + "\n" + _log("krita_assert"))
    data = {"content": [{"type": "text", "text": message}], "isError": True}
    short = compact.compact(tmp_path, "call", data)
    text = short["content"][0]["text"]
    assert text.startswith(f"{PREFIX}: AssertionError: Create or open")
    ref = short["details"]["ref"]
    page = compact.details(tmp_path, ref, "content/0/text", limit=1000)
    assert "render tile 200 of 400" in str(page)
