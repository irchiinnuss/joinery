"""Turn timing: a phase's figure is wall-clock time, never more than the turn."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from joinery.timing import TurnTiming, covered


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [100.0]
    monkeypatch.setattr("joinery.timing.time.monotonic", lambda: now[0])
    return now


def test_overlapping_intervals_count_once() -> None:
    assert covered([]) == 0.0
    assert covered([(0, 10), (5, 15), (20, 25), (21, 22)]) == 20.0
    assert covered([(3, 4), (0, 10)]) == 10.0


def test_parallel_runs_of_a_phase_never_exceed_the_turn(clock: list[float]) -> None:
    timing = TurnTiming()

    async def precheck(gate: asyncio.Event) -> None:
        with timing.span("precheck"):
            await gate.wait()

    async def go() -> None:
        # Three tasks check at once, from 100 s to 110 s.
        gate = asyncio.Event()
        runs = [asyncio.create_task(precheck(gate)) for _ in range(3)]
        await asyncio.sleep(0)
        clock[0] = 110.0
        gate.set()
        await asyncio.gather(*runs)

    asyncio.run(go())
    clock[0] = 112.0
    report = timing.report("done")
    assert report["total_ms"] == 12_000
    assert report["phases_ms"] == {"precheck": 10_000}


def test_a_phase_inside_another_is_its_part_not_a_phase_of_its_own(
    clock: list[float],
) -> None:
    timing = TurnTiming()
    with timing.span("precheck"):
        clock[0] = 101.0
        with timing.span("review_scene_tests"):
            clock[0] = 108.0
        # The same phase again inside itself (the turn's and the gateway's).
        with timing.span("precheck"):
            clock[0] = 109.0
        clock[0] = 110.0
    with timing.span("submit"):
        clock[0] = 115.0
    report = timing.report("done")
    assert report["phases_ms"] == {"precheck": 10_000, "submit": 5_000}
    assert report["nested_ms"] == {"review_scene_tests": 7_000}
    assert report["within"] == {"review_scene_tests": "precheck"}
    assert sum(report["phases_ms"].values()) <= report["total_ms"]
    # Old readers keep seconds per top-level phase.
    assert timing.phases == {"precheck": 10.0, "submit": 5.0}


def test_a_nested_phase_in_a_task_started_inside_its_outer_phase(
    clock: list[float],
) -> None:
    timing = TurnTiming()

    async def tests() -> None:
        with timing.span("review_scene_tests"):
            clock[0] = 104.0

    async def go() -> None:
        with timing.span("precheck"):
            await asyncio.create_task(tests())
            clock[0] = 105.0

    asyncio.run(go())
    report = timing.report("done")
    assert report["phases_ms"] == {"precheck": 5_000}
    assert report["nested_ms"] == {"review_scene_tests": 4_000}


def test_an_open_span_counts_up_to_now_and_another_turns_spans_are_no_parent(
    clock: list[float], tmp_path: Path
) -> None:
    lead, task = TurnTiming(), TurnTiming()
    with lead.span("tools"):
        clock[0] = 102.0
        with task.span("precheck"):
            clock[0] = 103.0
            assert task.report("running")["phases_ms"] == {"precheck": 1_000}
    assert task.report("done")["nested_ms"] == {}
    assert lead.save(tmp_path, "done")["phases_ms"] == {"tools": 3_000}
