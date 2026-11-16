"""A to-do's steps: short titles with their detail behind them, no step twice, the user's
edits as built, and real progress while the build runs (``todo_step``), the same for
every agent. Fake agents only: no inference."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from bridge.instructions import REVIEW_GUIDE
from joinery import done_when, headless
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.roles import role_violation
from joinery.turn import WAITING_FOR_GO, TurnController
from servers.joinery_registry.proxy import make_server
from tests.test_crew import project, wire

__all__ = ["project"]

LONG = (
    "Mower: pull-cord handle and fuel cap, modelled in res://art/mower/mower.blend at "
    "12 cm and 6 cm, placed on the engine block's top face"
)
TODO = {
    "steps": [
        {"title": "Mower body", "detail": "res://art/mower/mower.blend, 60 x 40 cm"},
        {"title": "Mower: handle and fuel cap", "detail": "12 cm handle, 6 cm cap"},
        {"title": "Place the mower in the shed"},
    ],
    "done_when": [{"text": "The mower looks right", "kind": "look"}],
}


def run(step: Callable[[], Any]) -> Any:
    return asyncio.run(step())


# --- Titles and details ----------------------------------------------------------------


def test_each_step_is_a_short_title_with_its_detail_behind_it() -> None:
    todo = done_when.proposal(TODO, "Make a mower")
    assert todo.steps == [
        "Mower body",
        "Mower: handle and fuel cap",
        "Place the mower in the shed",
    ]
    assert todo.details == [
        "res://art/mower/mower.blend, 60 x 40 cm",
        "12 cm handle, 6 cm cap",
        "",
    ]
    shown = todo.as_dict()
    assert shown["steps"] == todo.steps and shown["details"] == todo.details
    assert shown["step_states"] == ["", "", ""]
    # The build hears the whole of each step.
    built = done_when.build_prompt(todo)
    assert "1. Mower body: res://art/mower/mower.blend, 60 x 40 cm" in built
    assert "3. Place the mower in the shed\n" in built


def test_an_old_style_long_step_shows_its_first_clause_and_keeps_every_word() -> None:
    todo = done_when.proposal({"steps": [LONG, "Paint it red"]})
    assert todo.steps[0] == "Mower: pull-cord handle and fuel cap"
    assert len(todo.steps[0].split()) <= done_when.TITLE_WORDS
    assert todo.details[0] == LONG
    assert todo.steps[1] == "Paint it red" and todo.details[1] == ""
    # Nothing is lost on the way to the build.
    assert LONG in done_when.go_prompt(todo)
    # A run-on title is cut to its words, the rest kept as detail.
    run_on = done_when.short_title(
        "Model the left and the right wheel arches and the two big rear tyres together"
    )
    assert run_on.endswith("…") and len(run_on.split()) <= done_when.TITLE_WORDS


def test_a_step_said_twice_is_kept_once() -> None:
    todo = done_when.proposal(
        {
            "steps": [
                "Put a lever beside the gate",
                {"title": "Build room 1"},
                {"title": "Put a lever by the gate.", "detail": "Brass, 40 cm"},
                {"title": "Build room 2"},
                {"title": "Open the gate"},
            ],
            "after": [[], [], [1], [], [3]],
        }
    )
    # Rooms 1 and 2 differ in a number: both stay.
    assert todo.steps == [
        "Put a lever beside the gate",
        "Build room 1",
        "Build room 2",
        "Open the gate",
    ]
    # The repeat's detail joins the step it repeats; "after" follows the steps.
    assert todo.details[0] == "Brass, 40 cm"
    assert todo.after == [[], [], [], [1]]
    assert done_when.same_step("Mower body", "mower body!")
    assert not done_when.same_step("Mower body", "Mower handle")


def test_the_users_edits_are_what_gets_built() -> None:
    told = done_when.proposal(
        {
            "steps": [
                {"title": "Make a chair", "detail": "oak, 45 cm seat"},
                {"title": "Make a lamp", "detail": "brass"},
                {"title": "Put both on the shelf"},
            ],
            "after": [[], [], [1, 2]],
        }
    )
    edited = done_when.edited(
        told,
        {
            "steps": [
                {"title": "Make a lamp", "detail": "brass, 30 cm", "from": 2},
                {"title": "Make an oak chair", "detail": "oak, 45 cm seat", "from": 1},
                {"title": "Hang a picture", "detail": "over the shelf"},
                {"title": "make a LAMP"},
                {"title": "Put both on the shelf", "from": 3},
            ]
        },
    )
    # Moved, reworded, added; the repeat the user typed is dropped.
    assert edited.steps == [
        "Make a lamp",
        "Make an oak chair",
        "Hang a picture",
        "Put both on the shelf",
    ]
    assert edited.details[:3] == ["brass, 30 cm", "oak, 45 cm seat", "over the shelf"]
    # What each step needed follows it; the added one needs the one before it.
    assert edited.after == [[], [], [2], [1, 2]]
    built = done_when.go_prompt(edited)
    assert "1. Make a lamp: brass, 30 cm" in built and "3. Hang a picture" in built
    # The card that sends plain lines still works, and keeps each step's detail.
    plain = done_when.edited(
        told, {"steps": ["Make a chair", "Make a lamp", "Put both on the shelf"]}
    )
    assert plain.details == told.details and plain.after == told.after


# --- Progress while it is built ----------------------------------------------------------


def test_the_build_checks_each_step_off_as_it_goes(project: Path) -> None:
    async def go() -> None:
        events: list[dict[str, Any]] = []
        gateway = wire(ProjectGateway(project))
        gateway.keep_reviews = True
        controller = TurnController(gateway, events.append, "gemini")
        lead = GatewayRPC(gateway, None)
        worker = GatewayRPC(gateway, None, role="worker")
        await lead.start()
        await worker.start()
        try:
            controller.begin("Make a mower")
            with pytest.raises(RuntimeError, match="no to-do"):
                await rpc(lead.path, "todo_step", {"step": 1, "state": "done"})
            controller.propose_todo(TODO)
            assert controller.todo is not None
            answer = await rpc(lead.path, "todo_step", {"step": 1, "state": "started"})
            assert answer == {"status": "ok", "finished": 0, "steps": 3}
            await rpc(lead.path, "todo_step", {"step": 1, "state": "done"})
            answer = await rpc(
                lead.path,
                "todo_step",
                {"step": 3, "state": "skipped", "note": "The shed isn't built yet"},
            )
            assert answer["finished"] == 2
            for bad in (
                {"step": 0, "state": "done"},
                {"step": 4, "state": "done"},
                {"step": 2, "state": "finished"},
            ):
                with pytest.raises(RuntimeError):
                    await rpc(lead.path, "todo_step", bad)
            with pytest.raises(RuntimeError, match="Only Journeyman's own request"):
                await rpc(worker.path, "todo_step", {"step": 2, "state": "done"})
            marks = [e for e in events if e.get("kind") == "todo" and "step" in e]
            assert [(e["step"], e["step_state"]) for e in marks] == [
                (1, "started"),
                (1, "done"),
                (3, "skipped"),
            ]
            # The to-do's own state is left as it was: no "state" on a step event.
            assert all("state" not in e for e in marks)
            assert marks[-1]["step_states"] == ["done", "", "skipped"]
            assert marks[-1]["note"] == "The shed isn't built yet"
            # Once the change is checked every step not skipped is done.
            controller.todo_event("checking")
            assert events[-1]["step_states"] == ["done", "done", "skipped"]
        finally:
            await lead.close()
            await worker.close()
            await gateway.close()

    run(go)


def test_a_step_waits_for_go_and_a_reworked_list_updates_the_same_card(
    project: Path,
) -> None:
    async def go() -> None:
        events: list[dict[str, Any]] = []
        gateway = wire(ProjectGateway(project))
        controller = TurnController(gateway, events.append, "codex")
        try:
            controller.begin("Make a mower")
            first = controller.propose_todo(TODO)
            assert first["status"] == "waiting_for_go"
            with pytest.raises(ValueError, match=WAITING_FOR_GO[:20]):
                controller.todo_step({"step": 1, "state": "started"})
            await controller.end(True)
            # The user answers with a change to the list instead of Go: the agent's
            # new list takes the old one's place on the same card.
            controller.begin("Add a grass box too")
            again = controller.propose_todo(TODO)
            assert again["todo_id"] == first["todo_id"]
            shown = [e for e in events if e.get("kind") == "todo"]
            assert [e.get("state") for e in shown] == ["waiting", "dropped", "waiting"]
            assert len({e["todo_id"] for e in shown}) == 1
        finally:
            await gateway.close()

    run(go)


def test_only_the_lead_checks_off_steps_and_every_agent_is_told() -> None:
    assert role_violation("worker", "mcp", "joinery-registry", "todo_step")
    assert role_violation("lead", "mcp", "joinery-registry", "todo_step") is None
    todo = done_when.proposal(TODO)
    for prompt in (
        done_when.go_prompt(todo),
        done_when.build_prompt(todo),
        done_when.steps_prompt(todo, []),
    ):
        assert "todo_step(step, 'started')" in prompt
    assert "short title" in REVIEW_GUIDE and "detail" in REVIEW_GUIDE


def test_joinery_run_prints_the_step_progress() -> None:
    todo = done_when.proposal(TODO)
    todo.mark(1, "done")
    line = headless.describe(done_when.step_event(todo, 2))
    assert line == "  ✓ Step 2 of 3 done: Mower: handle and fuel cap"
    todo.mark(2, "skipped")
    line = headless.describe(done_when.step_event(todo, 3, "No shed"))
    assert line == "  Step 3 of 3 skipped: Place the mower in the shed (No shed)"


def test_the_tools_pass_titles_details_and_steps_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[str, dict[str, Any]]] = []

    async def fake(_socket: Path, method: str, params: dict[str, Any]) -> Any:
        asked.append((method, params))
        return {"status": "ok"}

    monkeypatch.setattr("servers.joinery_registry.proxy.rpc", fake)

    async def go() -> None:
        async with Client(make_server(tmp_path / "gateway.sock")) as client:
            await client.call_tool(
                "propose_todo",
                {
                    "steps": [
                        {"title": "Mower body", "detail": "60 x 40 cm"},
                        "Paint it",
                    ]
                },
            )
            result = await client.call_tool(
                "todo_step", {"step": 2, "state": "skipped", "note": "Already red"}
            )
            (text,) = result.content
            assert isinstance(text, TextContent)
            assert json.loads(text.text)["status"] == "ok"
            bad = await client.call_tool("todo_step", {"step": 1, "state": "half"})
            assert bad.is_error

    run(go)
    assert asked == [
        (
            "propose_todo",
            {
                "steps": [{"title": "Mower body", "detail": "60 x 40 cm"}, "Paint it"],
                "done_when": [],
            },
        ),
        ("todo_step", {"step": 2, "state": "skipped", "note": "Already red"}),
    ]
