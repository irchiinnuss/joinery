"""A test run as a person follows it: the test running now in words, each result as
it lands, and one line at the end.

While Joinery runs a change's scene tests, the sidebar's live line says what is being
checked ("Checking mower starts on lawn"), a test that fails says so by name with its
one-line reason as it happens, and the run ends as "14 checks: 13 passed, 1 failed —
hose reaches flowerbed". The opened trail lists every result, grouped by folder when
there are several. Each event carries the whole run so far, so whoever shows it
(the sidebar, ``joinery run``) only ever replaces what it had.
"""

from __future__ import annotations

import contextlib
import re
import secrets
from collections.abc import Callable, Sequence
from pathlib import PurePosixPath
from typing import Any

#: The longest reason a failure shows.
REASON_WIDTH = 90
#: Lines of a test's output that say why it failed, best first.
_WHY = (
    re.compile(
        r"^\s*(?:FAIL(?:ED)?|ASSERT(?:ION)?(?: FAILED)?)\b[:\s-]*(.+)$", re.IGNORECASE
    ),
    re.compile(
        r"^\s*(?:SCRIPT ERROR|ERROR|Parse Error|Compile Error):\s*(.+)$", re.IGNORECASE
    ),
    re.compile(r"\b(?:expected|got)\b.+", re.IGNORECASE),
)
_PATH = re.compile(r"(?:res://|/)[^\s:'\"]*/([^\s/:'\"]+)")


def title(script: str) -> str:
    """A test script's name in words ("tests/test_mower_starts_on_lawn.gd" ->
    "mower starts on lawn")."""
    stem = PurePosixPath(str(script).removeprefix("res://")).stem
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem)
    words = " ".join(spaced.replace("_", " ").replace("-", " ").split()).lower()
    words = re.sub(r"^(?:scene test|test|check)s? |(?: test| check)s?$", "", words)
    return words or stem.lower()


def area(script: str) -> str:
    """The folder a test sits in, in words ("tests/garden/test_hose.gd" -> "garden");
    "" for tests in the tests folder itself."""
    parent = PurePosixPath(str(script).removeprefix("res://")).parent
    name = parent.name
    if not name or name.lower() in {"tests", "test", "scene_tests", "."}:
        return ""
    return " ".join(name.replace("_", " ").replace("-", " ").split()).lower()


def reason(output: str) -> str:
    """Why a test failed, in one short line from what it printed."""
    lines = [line.strip() for line in str(output).splitlines() if line.strip()]
    for pattern in _WHY:
        for line in lines:
            found = pattern.search(line)
            if found:
                return _clip(found.group(found.lastindex or 0))
    tail = [line for line in lines if not line.startswith(("at:", "Godot Engine"))]
    return _clip(tail[-1]) if tail else "it exited with an error"


def _clip(text: str) -> str:
    line = _PATH.sub(r"\1", " ".join(text.split()))
    line = line.removeprefix("res://").strip(" :-")
    if len(line) > REASON_WIDTH:
        line = line[: REASON_WIDTH - 1].rstrip() + "…"
    return line


class TestRun:
    """One run of a change's tests, told to ``emit`` as {"kind": "tests"} events:
    the run's ``id``, its ``label``, the test running ``now`` in words, ``total``,
    ``results`` so far ({name, area, passed, reason, and unfinished when so}), ``done`` and its
    ``summary``. An ``unfinished`` test is one the check's time stopped or never
    started: neither passed nor failed."""

    __test__ = False  # not a pytest class

    def __init__(
        self,
        label: str,
        scripts: Sequence[str],
        emit: Callable[[dict[str, Any]], None],
    ) -> None:
        self.id = "tests-" + secrets.token_hex(4)
        self.label = label
        self.order = list(scripts)
        self.emit = emit
        self.running: list[str] = []
        self.results: dict[str, dict[str, Any]] = {}
        self.done = False

    def started(self, script: str) -> None:
        """``script`` started: it is the line's test now."""
        if script not in self.running:
            self.running.append(script)
        self._send()

    def finished(
        self, script: str, passed: bool, output: str = "", unfinished: bool = False
    ) -> None:
        """``script`` ended; a failure says why in one line. ``unfinished``: the
        check's time ran out before it did (or before it started)."""
        if script in self.running:
            self.running.remove(script)
        if unfinished:
            why = "stopped, the check's time ran out"
            if "did not start" in output:
                why = "not started, the check's time ran out"
        else:
            why = "" if passed else reason(output)
        self.results[script] = {
            "name": title(script),
            "area": area(script),
            "passed": passed and not unfinished,
            "reason": why,
        }
        if unfinished:
            self.results[script]["unfinished"] = True
        self._send()

    def end(self) -> None:
        """Every test has run (or the run stopped): the summary line."""
        self.running.clear()
        self.done = True
        self._send()

    def summary(self) -> str:
        """ "14 checks: 13 passed, 1 failed — hose reaches flowerbed"; "3 checks
        passed"; "1 check passed"; "14 checks: 12 passed, 2 not run in time —
        mower starts on lawn, hose reaches flowerbed"."""
        results = [self.results[s] for s in self.order if s in self.results]
        count = len(results)
        noun = "check" if count == 1 else "checks"
        cut = [r["name"] for r in results if r.get("unfinished")]
        failed = [
            r["name"] for r in results if not r["passed"] and not r.get("unfinished")
        ]
        if not failed and not cut:
            return f"{count} {noun} passed" if count else "No checks ran"
        parts = [f"{count - len(failed) - len(cut)} passed"]
        if failed:
            parts.append(f"{len(failed)} failed — {_names(failed)}")
        if cut:
            parts.append(f"{len(cut)} not run in time — {_names(cut)}")
        # A "; " between parts once names follow more than the last one.
        return f"{count} {noun}: " + ("; " if cut and failed else ", ").join(parts)

    def event(self) -> dict[str, Any]:
        now = title(self.running[-1]) if self.running else ""
        return {
            "kind": "tests",
            "id": self.id,
            "label": self.label,
            "now": f"Checking {now}" if now else "",
            "total": len(self.order),
            "results": [self.results[s] for s in self.order if s in self.results],
            "done": self.done,
            "summary": self.summary() if self.done else "",
        }

    def _send(self) -> None:
        # Telling never stops the tests it tells of.
        with contextlib.suppress(Exception):
            self.emit(self.event())


def _names(names: list[str]) -> str:
    return ", ".join(names[:2]) + (
        f" and {len(names) - 2} more" if len(names) > 2 else ""
    )


def lines(event: dict[str, Any], seen: set[str]) -> list[str]:
    """A tests event as ``joinery run`` prints it: nothing while a test runs, each
    result once as it lands, the summary at the end. ``seen`` holds the results
    printed before (the caller keeps it across a run's events)."""
    out: list[str] = []
    for result in event.get("results") or []:
        where = f"{result.get('area')}: " if result.get("area") else ""
        key = f"{event.get('id', '')}/{where}{result.get('name', '')}"
        if key in seen:
            continue
        seen.add(key)
        if result.get("passed"):
            out.append(f"    ✓ {where}{result.get('name', '')}")
        elif result.get("unfinished"):
            why = result.get("reason") or "not run in time"
            out.append(f"    – {where}{result.get('name', '')} — {why}")
        else:
            why = result.get("reason") or "failed"
            out.append(f"    ✗ {where}{result.get('name', '')} — {why}")
    if event.get("done"):
        out.append(f"  {event.get('summary', '')}")
    return out
