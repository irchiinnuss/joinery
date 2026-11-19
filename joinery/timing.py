"""Local latency evidence: fixed labels and durations, never prompts or payloads.

A phase's figure is wall-clock time: the union of its spans, so parallel runs (the
prechecks of several tasks at once) count once and no phase is ever longer than the
turn. A span opened inside another phase's span (``review_scene_tests`` inside
``precheck``) is that phase's part, not a phase of its own: it is left out of
``phases_ms`` and given in ``nested_ms``, with its outer phase in ``within``.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from joinery.metrics import PHASE_KINDS, Trace

#: The phase whose span the current code runs in (copied into tasks it starts).
_OUTER: contextvars.ContextVar[tuple[str, str, str] | None] = contextvars.ContextVar(
    "joinery_timing_outer", default=None
)


def covered(intervals: Iterable[tuple[float, float]]) -> float:
    """Seconds covered by ``intervals`` (start, end), overlaps counted once."""
    total, reach = 0.0, float("-inf")
    for start, end in sorted(intervals):
        if end <= reach:
            continue
        total += end - max(start, reach)
        reach = end
    return total


class TurnTiming:
    def __init__(self) -> None:
        self.id = uuid.uuid4().hex
        self.started = time.monotonic()
        #: Every span: (phase, outer phase or "", start, end or None while open).
        self._spans: list[list[Any]] = []
        self.first_response: float | None = None
        self.model = ""
        self.effort = ""
        self.mode = ""
        #: The same work as spans, for the change's cost (joinery.metrics).
        self.trace = Trace(self.id)

    @contextmanager
    def span(
        self, phase: str, name: str = "", attributes: dict[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Time a phase; it is also one span of the change, whose attributes the
        yielded dict takes."""
        outer = _OUTER.get()
        # Only this turn's own spans make a phase nested; a phase inside itself is
        # where its outer span is.
        within = ""
        if outer is not None and outer[0] == self.id:
            within = outer[2] if outer[1] == phase else outer[1]
        record: list[Any] = [phase, within, time.monotonic(), None]
        self._spans.append(record)
        token = _OUTER.set((self.id, phase, within))
        try:
            with self.trace.span(
                PHASE_KINDS.get(phase, phase), name or phase, attributes
            ) as attrs:
                yield attrs
        finally:
            record[3] = time.monotonic()
            with contextlib.suppress(ValueError):  # Closed in another context.
                _OUTER.reset(token)

    def _union(self, nested: bool) -> dict[str, float]:
        now = time.monotonic()
        spans: dict[str, list[tuple[float, float]]] = {}
        for phase, within, start, end in self._spans:
            if bool(within) == nested:
                spans.setdefault(phase, []).append((start, now if end is None else end))
        return {phase: covered(intervals) for phase, intervals in spans.items()}

    @property
    def phases(self) -> dict[str, float]:
        """Seconds per top-level phase, wall-clock (overlapping runs counted once)."""
        return self._union(nested=False)

    @property
    def nested(self) -> dict[str, float]:
        """Seconds per phase that ran inside another phase, wall-clock."""
        return self._union(nested=True)

    def within(self) -> dict[str, str]:
        """Each nested phase's outer phase (the first one it was seen in)."""
        found: dict[str, str] = {}
        for phase, outer, _start, _end in self._spans:
            if outer:
                found.setdefault(phase, outer)
        return found

    def response(self) -> None:
        if self.first_response is None:
            self.first_response = time.monotonic() - self.started

    def report(self, outcome: str) -> dict[str, Any]:
        return {
            "kind": "timing",
            "id": self.id,
            "outcome": outcome,
            "model": self.model,
            "effort": self.effort,
            "mode": self.mode,
            "total_ms": round((time.monotonic() - self.started) * 1000),
            "first_response_ms": None
            if self.first_response is None
            else round(self.first_response * 1000),
            "phases_ms": {
                key: round(value * 1000) for key, value in self.phases.items()
            },
            "nested_ms": {
                key: round(value * 1000) for key, value in self.nested.items()
            },
            "within": self.within(),
        }

    def save(self, project: Path, outcome: str) -> dict[str, Any]:
        report = self.report(outcome)
        target = project / ".joinery/latency.jsonl"
        if target.is_symlink() or not target.resolve().is_relative_to(
            project.resolve()
        ):
            raise ValueError("Latency evidence must stay inside this project.")
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = (
            target.read_text().splitlines()[-199:]
            if target.exists() and target.stat().st_size < 2_000_000
            else []
        )
        target.write_text("\n".join([*lines, json.dumps(report)]) + "\n")
        return report
