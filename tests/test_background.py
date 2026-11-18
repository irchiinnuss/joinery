"""Fire-and-forget tasks stay referenced until they end, and their failures are logged."""

import asyncio
import gc
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge.error_fixer import ErrorFixer
from joinery import background
from joinery.gateway_rpc import GatewayRPC, rpc


def test_a_task_nobody_holds_survives_garbage_collection() -> None:
    async def run() -> list[str]:
        went_on: list[str] = []
        release = asyncio.Event()

        async def wait_then_note() -> None:
            await release.wait()
            went_on.append("finished")

        background.spawn(wait_then_note(), name="held")
        await asyncio.sleep(0)
        gc.collect()
        release.set()
        for _ in range(5):
            await asyncio.sleep(0)
        return went_on

    assert asyncio.run(run()) == ["finished"]
    assert not background.RUNNING


def test_a_failing_task_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    async def fail() -> None:
        raise RuntimeError("the pump broke")

    async def run() -> None:
        task = background.spawn(fail(), name="breaks")
        await asyncio.wait([task])

    with caplog.at_level(logging.ERROR, logger="joinery.background"):
        asyncio.run(run())
    assert "breaks" in caplog.text
    assert "the pump broke" in caplog.text
    assert not background.RUNNING


def test_a_cancelled_task_ends_quietly(caplog: pytest.LogCaptureFixture) -> None:
    async def run() -> None:
        task = background.spawn(asyncio.sleep(30), name="cancelled")
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.wait([task])

    with caplog.at_level(logging.ERROR, logger="joinery.background"):
        asyncio.run(run())
    assert caplog.text == ""
    assert not background.RUNNING


def test_spawn_needs_a_loop_like_create_task() -> None:
    async def nothing() -> None:
        pass

    work = nothing()
    with pytest.raises(RuntimeError):
        background.spawn(work)
    work.close()


def test_the_policy_stop_is_held_while_it_runs(tmp_path: Path) -> None:
    """The chat host's stop handler is kept alive until it has told the user."""

    async def run() -> list[str]:
        release = asyncio.Event()
        reasons: list[str] = []

        async def on_stop(reason: str) -> None:
            await release.wait()
            reasons.append(reason)

        async def close() -> None:
            pass

        endpoint = GatewayRPC(SimpleNamespace(close=close), on_stop)
        try:
            await endpoint.start()
            with pytest.raises(RuntimeError, match="A generative service."):
                await rpc(
                    endpoint.path, "policy_stop", {"reason": "A generative service."}
                )
            held = [t for t in background.RUNNING if t.get_name() == "policy-stop"]
            assert len(held) == 1
            gc.collect()
            release.set()
            await asyncio.wait(held, timeout=2)
        finally:
            await endpoint.close()
        return reasons

    assert asyncio.run(run()) == ["A generative service."]


def test_the_error_fixer_repump_is_held(tmp_path: Path) -> None:
    async def run() -> int:
        fixer = ErrorFixer.__new__(ErrorFixer)
        pumped = asyncio.Event()

        async def pump_and_publish() -> None:
            pumped.set()

        fixer._pump_and_publish = pump_and_publish  # type: ignore[method-assign]
        fixer._repump()
        held = [t for t in background.RUNNING if t.get_name() == "error-fixer-pump"]
        await asyncio.wait_for(pumped.wait(), 2)
        return len(held)

    assert asyncio.run(run()) == 1
