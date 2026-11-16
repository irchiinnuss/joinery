"""One user request, run the same way for every provider.

The provider streams its turn; Joinery owns what happens around it: the turn
starts in the controller, live edits are admitted by the controller, and when
the provider is done Joinery submits whatever the agent left unsubmitted and
says plainly when a change didn't make it.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, Protocol

from bridge.instructions import CONTINUE_AFTER_FAILED_SUBMIT
from bridge.models import Selection
from bridge.protocol import (
    AgentEvent,
    Done,
    EditorAction,
    Execution,
    Message,
    MessageDelta,
    Prompt,
    SidebarMessage,
    Trail,
    Usage,
)
from joinery import done_when, metrics
from joinery.fallback import is_quota, provider_error
from joinery.headless import LEFT_CHANGE, PROVIDER_ERROR
from joinery.routing import routing_settings, step_model
from joinery.timing import TurnTiming
from joinery.turn import MAX_CONTINUATIONS, Problem, SubmitResult, TurnController


class AgentBridge(Protocol):
    def send(self, message: SidebarMessage) -> Any: ...

    def editor_result(self, request_id: str, result: dict[str, Any]) -> None: ...


Emit = Callable[[dict[str, Any]], None]
#: Runs a to-do's steps (0-based) as parallel tasks and returns how each came out
#: (bridge.steps.StepRun.run); raises ValueError when they can't run that way.
Steps = Callable[
    [done_when.Todo, list[int]], Coroutine[Any, Any, list[done_when.StepOutcome]]
]
#: Offers to carry a turn on with another agent after a quota error (given the
#: provider's words); returns that agent's bridge and its first prompt's text, or
#: None when the user said no or no other agent is signed in (ADR 0080).
Fallback = Callable[[str], Coroutine[Any, Any, "tuple[AgentBridge, str] | None"]]


#: Each check's failure in plain words; the raw output stays with the agent.
PLAIN = {
    "protected": "it changed project settings only project setup may change",
    "live_conflict": "it changed one scene both live and as a saved file",
    "references": "{paths} {verb} a file the change doesn't include",
    "assets": "an asset has no valid provenance record",
    "dependencies": "it adds a package Joinery doesn't allow",
    "art_bible": "it breaks the art direction's rules",
    "godot_import": "Godot couldn't import it",
    "tests": "the project's tests failed",
    "conflict": "another accepted change touched the same file",
    "reviewer": "the reviewer found problems",
}


def _plain(problem: Problem) -> str:
    paths = [Path(p).name for p in problem.paths[:2]]
    text = PLAIN.get(problem.check, "")
    if not text:
        return ""
    if "{paths}" in text:
        if not paths:
            return "it uses a file the change doesn't include"
        return text.format(
            paths=" and ".join(paths), verb="use" if len(paths) > 1 else "uses"
        )
    return text + (" (" + ", ".join(paths) + ")" if paths else "")


def failure_notice(result: SubmitResult) -> dict[str, Any]:
    """Joinery's own line when a change didn't pass: the agent's words aren't the last.

    Plain words only; the checks' raw output went to the agent."""
    if any(p.check in {"checks", "review"} for p in result.problems):
        text = (
            "Joinery couldn't check Journeyman's change, so nothing was changed. "
            + (
                "Make sure Docker is running, then ask again."
                if any(p.check == "checks" for p in result.problems)
                else "Ask again in a moment."
            )
        )
    else:
        # One line the chat can show whole ("Change not applied · <the first reason>"); the
        # reasons in full follow after a line break, behind the line's › in the sidebar.
        reasons = [r for r in dict.fromkeys(map(_plain, result.problems)) if r]
        text = "Change not applied" + (" · " + reasons[0] if reasons else "")
        if reasons:
            detail = "; ".join(reasons[:2])
            text += "\n" + detail[:1].upper() + detail[1:] + ". Nothing was changed."
    return {"kind": "notice", "level": "warn", "text": text}


def held_notice(result: SubmitResult) -> dict[str, Any]:
    """Joinery's own line when a finished change failed checks and waits for the user
    anyway (``TurnController._hold``): what failed, so they look before accepting."""
    reasons = [r for r in dict.fromkeys(map(_plain, result.problems)) if r]
    text = "Waiting for your review, but it failed a check" + (
        " · " + reasons[0] if reasons else ""
    )
    if reasons:
        detail = "; ".join(reasons[:2])
        text += (
            "\n"
            + detail[:1].upper()
            + detail[1:]
            + ". Look at the failed checks before "
            "you accept it."
        )
    return {"kind": "notice", "level": "warn", "text": text}


def _contents(result: SubmitResult, limit: int = 4) -> str:
    """The change's files in a line: "a.gd (new), b.png (changed) and 2 more"."""
    paths = [c["path"] for c in result.changes if c.get("path")]
    files = [
        f"{c['path']} ({c['change']})" if c.get("change") else c["path"]
        for c in result.changes
        if c.get("path")
        # A folder the change made shows through its files.
        and not any(p.startswith(c["path"].rstrip("/") + "/") for p in paths)
    ]
    shown = ", ".join(files[:limit])
    if len(files) > limit:
        shown += f" and {len(files) - limit} more"
    if result.live_edits:
        edits = f"{result.live_edits} live edit" + "s" * (result.live_edits > 1)
        shown = f"{shown} and {edits}" if shown else edits
    return shown or "its staged files"


def left_notice(result: SubmitResult) -> dict[str, Any] | None:
    """Joinery's own line when it submitted the change the agent left and that change
    waits for review, or was kept automatically. The agent may have said there was
    nothing to review, so the chat says what there is. None otherwise."""
    if result.status == "accepted":
        text = (
            "Change kept automatically · Journeyman stopped without submitting it, "
            "so Joinery checked it and auto-accept kept it\nIt holds "
        )
    elif result.status == "in_review":
        text = (
            "A change is waiting for your review · Journeyman stopped without "
            "submitting it, so Joinery put it up for review\nIt holds "
        )
    else:
        return None
    text += _contents(result) + "."
    if result.problems:
        reasons = [r for r in dict.fromkeys(map(_plain, result.problems)) if r]
        text += (
            " It failed a check"
            + (": " + "; ".join(reasons[:2]) if reasons else "")
            + ". Look at the failed checks before you accept it."
        )
    level = "warn" if result.problems else "info"
    return {"kind": "notice", "level": level, "text": text, "about": LEFT_CHANGE}


def limit_notice(words: str) -> dict[str, Any]:
    """Joinery's own line when a usage or rate limit ended the turn: which limit,
    when it lifts if the provider said, and the provider's words. ``joinery run``
    takes it as the run's error (``about`` = PROVIDER_ERROR)."""
    kind, reset, line = provider_error(words)
    return {
        "kind": "notice",
        "level": "warn",
        "text": line,
        "about": PROVIDER_ERROR,
        "error_kind": kind,
        "resets": reset,
    }


def continuation(prompt: Prompt, result: SubmitResult) -> Prompt:
    """The same turn, asked once more: fix what the checks found and submit."""
    problems = "\n".join(f"- {p.check}: {p.message}" for p in result.problems)
    unprovenanced = sorted(
        {path for p in result.problems if p.check == "assets" for path in p.paths}
    )
    if unprovenanced:
        # A render cut short (a stalled worker) has no recipe or lock entry: make
        # exactly those again with their tool; the rest of the change stays.
        problems += (
            "\nRedo exactly these renders with the tool that makes them, so each "
            "gets its recipe and joinery.lock entry: "
            + ", ".join(unprovenanced)
            + ". Leave the rest of the change as it is."
        )
    return prompt.model_copy(
        update={
            "text": CONTINUE_AFTER_FAILED_SUBMIT.format(problems=problems),
            "attachments": (),
            # The session already has the summary from this turn's first prompt.
            "primer": "",
        }
    )


def _light(controller: TurnController) -> str:
    project = getattr(controller.gateway, "project", None)
    if not isinstance(project, Path):
        return ""
    return routing_settings(project).light_model(controller.agent_id)


def fix_model(controller: TurnController, result: SubmitResult) -> str:
    """The light model for a fix every failure of which is routine (ADR 0090), or ""
    for the session's own model."""
    project = getattr(controller.gateway, "project", None)
    if not isinstance(project, Path):
        return ""
    return step_model(
        routing_settings(project),
        controller.agent_id,
        "fix",
        checks=[p.check for p in result.problems],
    )


def _usage(controller: TurnController, usage: Usage | None) -> metrics.Totals | None:
    """This provider prompt's tokens, from the session totals it reported."""
    if usage is None:
        return None
    now = (usage.input_tokens, usage.output_tokens, usage.cached_tokens)
    turn = metrics.usage_delta(controller.usage_totals, now)
    controller.usage_totals = now
    return turn


async def stream(
    controller: TurnController,
    bridge: AgentBridge,
    prompt: Prompt,
    emit: Emit,
    timing: TurnTiming,
    asked: int = 0,
) -> bool:
    """Stream one provider prompt to the sidebar; returns the provider's ``Done.ok``.

    The prompt is one agent-turn span of the change, with the tokens the
    provider reported for it."""
    ok = False
    usage: Usage | None = None
    attrs = metrics.agent_attributes(controller.agent_id, "lead")
    if prompt.selection.model and prompt.selection.model == _light(controller):
        attrs[metrics.MODEL_TIER] = "light"
    attrs[metrics.ROUND] = asked
    attrs[metrics.TASK] = getattr(controller.gateway, "task_id", "")
    with timing.trace.span(metrics.AGENT_TURN, "invoke_agent lead", attrs) as span:
        try:
            ok, usage = await _stream(controller, bridge, prompt, emit, timing)
        finally:
            if timing.model:
                span[metrics.REQUEST_MODEL] = timing.model
            if usage is not None and usage.model:
                span[metrics.RESPONSE_MODEL] = usage.model
            span.update(metrics.token_attributes(_usage(controller, usage)))
    return ok


async def _stream(
    controller: TurnController,
    bridge: AgentBridge,
    prompt: Prompt,
    emit: Emit,
    timing: TurnTiming,
) -> tuple[bool, Usage | None]:
    """The provider's events to the sidebar. A usage or quota limit it reports, in
    its words or by failing outright, is kept in ``controller.quota`` (ADR 0080)."""
    try:
        return await _events(controller, bridge, prompt, emit, timing)
    except RuntimeError as exc:
        if not is_quota(str(exc)):
            raise
        controller.quota = str(exc)
        emit(Message(text=str(exc), level="warn").model_dump())
        return False, None


async def _events(
    controller: TurnController,
    bridge: AgentBridge,
    prompt: Prompt,
    emit: Emit,
    timing: TurnTiming,
) -> tuple[bool, Usage | None]:
    gateway = controller.gateway
    ok = False
    usage: Usage | None = None
    event: AgentEvent
    async for event in bridge.send(prompt):
        if (
            isinstance(event, Message)
            and event.level != "info"
            and is_quota(event.text)
        ):
            controller.quota = event.text
        if isinstance(event, Execution):
            timing.model, timing.effort, timing.mode = (
                event.model,
                event.effort,
                event.mode,
            )
        if isinstance(event, (MessageDelta, Message)):
            timing.response()
        if isinstance(event, Trail) and event.picture:
            event = event.model_copy(
                update={"picture": picture_path(gateway, event.picture)}
            )
        if isinstance(event, Usage):
            controller.meter.update(event.context_used, event.context_window)
            usage = event
        if isinstance(event, EditorAction):
            # A provider's own scene tool (Codex) is admitted like edit_scene.
            try:
                scene = controller.admit_live_edit(event.context)
            except ValueError as exc:
                bridge.editor_result(
                    event.request_id, {"success": False, "message": str(exc)}
                )
                continue
            try:
                # A model made in this change is loaded from its stage (ADR 0055).
                staged = await gateway.staged_files(dict(event.payload))
            except ValueError as exc:
                controller.record_live_edit(scene, False)
                bridge.editor_result(
                    event.request_id, {"success": False, "message": str(exc)}
                )
                continue
            event = event.model_copy(update={"staged": staged})
            controller.expect_live(event.request_id, scene, event.payload)
        if isinstance(event, Done):
            ok = event.ok
            continue
        emit(event.model_dump())
    return ok, usage


def picture_path(gateway: Any, path: str) -> str:
    """A picture a tool call named, as a file the sidebar can load: a res:// path
    from the change's stage when it is there, else from the project; "" when the
    picture isn't on disk."""
    found: list[Path] = []
    if path.startswith("res://"):
        relative = path.removeprefix("res://")
        transaction = getattr(gateway, "transaction", None)
        if transaction is not None:
            found.append(Path(transaction.stage) / relative)
        project = getattr(gateway, "project", None)
        if project is not None:
            found.append(Path(project) / relative)
    else:
        found.append(Path(path))
    for candidate in found:
        if candidate.is_absolute() and candidate.is_file():
            return str(candidate)
    return ""


async def settle_compaction(controller: TurnController) -> None:
    """Wait for a session summary still being written.

    A Stop of the waiting request never cancels the summary itself (``wait``
    doesn't cancel what it waits for), so it is saved and the next request
    starts fresh."""
    task = controller.compaction
    if task is None:
        return
    if not task.done():
        controller.gateway.progress("Summarising the session…")
    await asyncio.wait({task})
    if controller.compaction is task:
        controller.compaction = None


async def run_turn(
    controller: TurnController,
    bridge: AgentBridge,
    prompt: Prompt,
    emit: Emit,
    timing: TurnTiming,
    compact: Callable[[], Coroutine[Any, Any, Any]] | None = None,
    *,
    request: str = "",
    chat_id: str = "",
    todo: done_when.Todo | None = None,
    steps: Steps | None = None,
    fallback: Fallback | None = None,
) -> str:
    """Run one user request; returns its outcome ("done" or "failed").

    ``fallback`` offers another agent when the provider stops on a usage or quota
    limit; the staged change waits meanwhile and the other agent carries it on.

    ``todo`` is the to-do the user pressed Go on (ADR 0060): its done-when items are
    checked once the change passes Joinery's checks. ``steps`` runs a to-do's
    independent steps as parallel tasks when the project asks for that (ADR 0072);
    the agent then hears back once, at the end or when a step fails.

    A turn whose change was dropped (it failed its checks, or needs the user
    first) is "failed" even when the provider finished well. The session
    summary goes ahead of the prompt only in the chat it summarises.

    When the provider is done, a change the agent didn't submit is submitted for
    it. If that fails with problems the agent can fix, the agent is asked once
    more in the same turn; a change that still fails is dropped, and Joinery says
    so in its own line.
    """
    gateway = controller.gateway
    # The turn's spans: the chat host shares its timing with the gateway already.
    lent = getattr(gateway, "timing", None) is None
    if lent:
        gateway.timing = timing
    outcome = "failed"
    try:
        outcome = await _run_turn(
            controller,
            bridge,
            prompt,
            emit,
            timing,
            compact,
            request,
            chat_id,
            todo,
            steps,
            fallback,
        )
    except asyncio.CancelledError:
        outcome = "stopped"
        raise
    finally:
        controller.close_send_back()
        _save_metrics(controller, timing, outcome)
        if lent and gateway.timing is timing:
            gateway.timing = None
    return outcome


def _save_metrics(controller: TurnController, timing: TurnTiming, outcome: str) -> None:
    """Write the change's spans; measuring never fails the change it measures."""
    # Only a change this turn put in review: a question asked while an earlier
    # change waits is a request of its own.
    last = controller.last_result
    transaction = last.transaction_id if last is not None else ""
    trace = timing.trace
    if trace.path is not None:
        return
    if getattr(controller, "todo", None) is not None:
        trace.attributes.setdefault(metrics.STEPS_MODE, "one_by_one")
    trace.attributes.update(
        {
            metrics.OUTCOME: outcome,
            metrics.TRANSACTION: transaction,
            metrics.AGENT: controller.agent_id,
            metrics.TASK: getattr(controller.gateway, "task_id", ""),
            metrics.RESPONSE_MODEL: timing.model,
            metrics.SUBMITS: controller.attempts
            + controller.review_rounds
            + controller.check_rounds,
            # The reviewer's and the done-when checks' send-backs alike.
            metrics.SEND_BACKS: controller.review_rounds + controller.check_rounds,
            metrics.PRECHECKS: getattr(controller, "prechecks", 0),
        }
    )
    project = getattr(controller.gateway, "project", None)
    if project is None:
        return  # a host without a project folder keeps no evidence
    with contextlib.suppress(OSError, ValueError):
        trace.save(project)


async def _together(
    controller: TurnController,
    bridge: AgentBridge,
    prompt: Prompt,
    emit: Emit,
    timing: TurnTiming,
    steps: Steps,
    todo: done_when.Todo,
    together: list[int],
) -> bool:
    """Run ``together`` as parallel tasks, then hand the agent what is left, once.

    Returns the agent's ``Done.ok``, or True when nothing was left for it."""
    from bridge.steps import made_note, unchecked

    trace = timing.trace
    trace.attributes[metrics.STEPS_MODE] = "together"
    trace.attributes[metrics.STEPS_TOGETHER] = len(together)
    controller.stepping = True
    try:
        outcomes = await steps(todo, together)
    except ValueError as exc:
        # Tasks are off: the agent makes every step itself, as before.
        trace.attributes[metrics.STEPS_MODE] = "one_by_one"
        trace.attributes[metrics.STEPS_TOGETHER] = 0
        emit({"kind": "notice", "level": "info", "text": str(exc)})
        controller.stepping = False
        lead = prompt.model_copy(
            update={"text": done_when.go_prompt(todo), "attachments": (), "primer": ""}
        )
        return await stream(controller, bridge, lead, emit, timing)
    finally:
        controller.stepping = False
    failed = [o for o in outcomes if not o.made]
    trace.attributes[metrics.STEPS_FAILED] = len(failed)
    for outcome in outcomes:
        start = int(outcome.started * 1e9) if outcome.started else time.time_ns()
        end = start + int(outcome.seconds * 1e9)
        trace.add(
            metrics.STEP,
            f"step {outcome.number}",
            start,
            end,
            {
                metrics.STEP_NUMBER: outcome.number,
                metrics.STEP_STATE: outcome.state,
                metrics.TASK: outcome.task_id,
            },
            failed=not outcome.made,
        )
    if not done_when.remaining(todo, outcomes):
        trace.attributes[metrics.STEPS_BACK] = False
        if todo.done_when:
            results = unchecked(todo, len(outcomes))
            emit(
                {
                    "kind": "todo",
                    "todo_id": todo.id,
                    "state": "checked",
                    "results": [r.as_dict() for r in results],
                    "summary": done_when.counts_line(results),
                }
            )
        emit(Message(text=made_note(outcomes)).model_dump())
        return True
    trace.attributes[metrics.STEPS_BACK] = True
    controller.gateway.progress("Going on with the steps left…")
    lead = prompt.model_copy(
        update={
            "text": done_when.steps_prompt(todo, outcomes),
            "attachments": (),
            "primer": "",
        }
    )
    return await stream(controller, bridge, lead, emit, timing, 1)


async def _carry_on(
    controller: TurnController,
    bridge: AgentBridge,
    prompt: Prompt,
    emit: Emit,
    timing: TurnTiming,
    fallback: Fallback | None,
) -> tuple[bool, AgentBridge]:
    """The provider stopped on a usage or quota limit: offer another agent and, if
    the user takes it (or the project names one), let it carry the turn on with the
    change staged so far. Returns its ``Done.ok`` and the bridge the turn now uses;
    without a fallback the turn ends as it would have, saying why (``limit_notice``)."""
    reason = ""
    while controller.quota and fallback is not None:
        reason, controller.quota = controller.quota, ""
        offered = await fallback(reason)
        if offered is None:
            emit(limit_notice(reason))
            return False, bridge
        bridge, text = offered
        # Token totals are per provider session; the new one starts from zero.
        controller.usage_totals = (0, 0, 0)
        carried = prompt.model_copy(
            update={"text": text, "primer": "", "selection": Selection()}
        )
        ok = await stream(controller, bridge, carried, emit, timing)
        if ok or not controller.quota:
            return ok, bridge
    reason, controller.quota = controller.quota or reason, ""
    if reason:
        emit(limit_notice(reason))
    return False, bridge


async def _run_turn(
    controller: TurnController,
    bridge: AgentBridge,
    prompt: Prompt,
    emit: Emit,
    timing: TurnTiming,
    compact: Callable[[], Coroutine[Any, Any, Any]] | None,
    request: str,
    chat_id: str,
    todo: done_when.Todo | None = None,
    steps: Steps | None = None,
    fallback: Fallback | None = None,
) -> str:
    # The session may be being summarised while the user reviewed; wait for it.
    await settle_compaction(controller)
    if controller.primer and controller.primer_chat == chat_id:
        prompt = prompt.model_copy(update={"primer": controller.primer})
    controller.primer = controller.primer_chat = ""
    # The reviewer reads the change with the same model choice.
    controller.selection = prompt.selection.model_dump()
    controller.begin(request or prompt.text, todo)
    controller.gateway.progress("Thinking through your request…")
    project = getattr(controller.gateway, "project", None)
    together = (
        done_when.runs_together(todo, done_when.run_steps_mode(project))
        if todo is not None and steps is not None and project is not None
        else []
    )
    if together:
        # Go on a to-do whose steps run together: the steps first, then the agent.
        assert todo is not None and steps is not None
        ok = await _together(
            controller, bridge, prompt, emit, timing, steps, todo, together
        )
    else:
        ok = await stream(controller, bridge, prompt, emit, timing)
        if not ok and controller.quota:
            ok, bridge = await _carry_on(
                controller, bridge, prompt, emit, timing, fallback
            )
        if controller.steps_together and controller.todo is not None and steps:
            # The agent's to-do ran on without Go: its steps run now, as tasks.
            together, controller.steps_together = controller.steps_together, []
            ok = await _together(
                controller,
                bridge,
                prompt,
                emit,
                timing,
                steps,
                controller.todo,
                together,
            )
    asked = 0
    allowed = MAX_CONTINUATIONS
    escalated = False
    while True:
        result = await controller.end(ok, last=asked >= allowed)
        if result is None or result.status != "failed" or result.final:
            break
        # Joinery submitted the change the agent left, and it failed: the agent
        # gets one chance in this turn to fix it.
        asked += 1
        timing.trace.attributes[metrics.CONTINUATIONS] = asked
        fix = continuation(prompt, result)
        light = "" if escalated else fix_model(controller, result)
        if light:
            # A routine fix (tests, imports, references) on the light model; if
            # its fix fails too, the main model gets one more go (ADR 0090).
            fix = fix.model_copy(
                update={"selection": fix.selection.model_copy(update={"model": light})}
            )
            allowed += 1
            escalated = True
        ok = await stream(controller, bridge, fix, emit, timing, asked)
        if not ok and controller.quota:
            ok, bridge = await _carry_on(
                controller, bridge, prompt, emit, timing, fallback
            )
    last = controller.last_result
    dropped = (
        last is not None and last.final and last.status in {"failed", "needs_user"}
    )
    # The agent may have said there is nothing to review: the chat says what is.
    said = left_notice(result) if result is not None and result is last else None
    if last is not None and last.status == "failed" and last.final:
        emit(failure_notice(last))
    elif said is not None:
        emit(said)
    elif last is not None and last.status == "in_review" and last.problems:
        emit(held_notice(last))
    emit(Done(ok=ok).model_dump())
    if compact is not None and controller.needs_compaction():
        # Runs while the user reviews; the next request waits for it.
        controller.compaction = asyncio.create_task(compact())
    return "done" if ok and not dropped else "failed"
