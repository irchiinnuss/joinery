"""``joinery run``: one request through the real chat host, with no editor (ADR 0077).

    joinery run --project P --request "Make the door red" [--agent A] [--model M]
                [--effort E] [--json] [--timeout S] [--max-time S] [--chat ID|last]
                [--go auto|ask|never]
    joinery run --project P --idle [--json]     # start what waits for idle, now

``--timeout`` is an idle timeout: the run stops after that many seconds with no
event from the host (no tool call, no progress, no text), however long the work
takes while it keeps moving. ``--max-time`` caps the whole run (0: no cap). When a
run stops early or the host crashes, the change it had applied so far is kept for
review with a note (``ProjectGateway.keep_unfinished``) rather than discarded;
only the checks nobody can get around (project policy, the art-bible gate,
provenance, denied dependencies) still refuse it.

``--chat`` continues an earlier chat (its id from ``joinery chats list`` or the
run's own output, or ``last``): the agent resumes that session, or starts from its
summary, instead of rediscovering the project. Every run prints its chat id.
A to-do that waits for Go: ``--go ask`` asks on the terminal, ``auto`` goes ahead,
``never`` leaves it waiting and says so. The default asks when stdin is a terminal
and goes ahead otherwise, since nobody else can say Go.

It starts ``joinery chat`` for the project, the same host the editor talks to, sends
the request and answers what only an editor could do (a live scene edit, a look at the
viewport) with "no editor is open", so the agent stages saved files instead. When the
turn is over and nothing is still at work, the host closes and **keeps** the staged
change waiting for review (``FileTransaction.hold``): it shows in the Review tab the
next time the editor opens, or ``joinery transactions accept|reject``. It never
accepts. With ``--json`` each host event is one line on stdout (turn states, tool
calls, checks, token use, the change for review), then a ``run_result`` line.

The benchmark (``joinery.benchmark.LiveDriver``) drives the host the same way and
accepts each change, so its cost is recorded; ``joinery command run`` and the idle
queue go through here. The editor can't be connected to the project at the same time:
one host owns a project (``transactions.ProjectLease``).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from joinery.catalog import ROOT

#: The host events a run reports; the rest are the sidebar's own bookkeeping.
EVENTS = frozenset(
    {
        "connection",
        "execution",
        "turn_state",
        "step",
        "tool_activity",
        "progress",
        "check_problems",
        "usage",
        "message",
        "notice",
        "todo",
        "task",
        "art_gate",
        "file_preview",
        "file_decision",
        "done",
        "timing",
        "quiet",
        "chat",
        "kept",
    }
)
NO_EDITOR = "No editor is open in this run; stage saved files."
#: Seconds a run may go without any event from the host before it is stopped.
TIMEOUT = 900.0
#: Seconds the host gets, once a run stops, to keep what it applied and close.
CLOSE_GRACE = 90.0
#: What a stopped run says about the work it kept.
STOPPED_NOTE = "The run stopped before the agent finished ({why}); {kept}."


@dataclass(frozen=True)
class Pin:
    """The agent and model a run uses ("" for the project's own choice)."""

    agent: str
    model: str = ""
    effort: str = ""


@dataclass
class Outcome:
    """What a run left: the change waiting for review, if any, and how it went."""

    #: "review" (a change waits), "accepted", "rejected", "none" (nothing changed)
    #: or "failed".
    state: str = "none"
    transaction_id: str = ""
    summary: str = ""
    #: The agent's last reply.
    reply: str = ""
    #: The turn's outcome as the host timed it ("done", "failed", "stopped", …).
    outcome: str = ""
    error: str = ""
    #: Changes parallel tasks left for review: [{task_id, title, transaction_id}].
    tasks: list[dict[str, str]] = field(default_factory=list)
    #: The chat the request went to: ``joinery run --chat <id>`` continues it.
    chat_id: str = ""
    #: Why the change waits unfinished (a timeout, a crash), when it does.
    note: str = ""
    #: A to-do still waiting for Go when the run ended ("" for none).
    todo_waiting: str = ""
    #: The change waits unfinished: the host kept it when the turn failed or stopped.
    kept: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"kind": "run_result", **asdict(self)}


def chat_command(project: Path, pin: Pin, *, keep_reviews: bool = True) -> list[str]:
    """The chat host the editor runs, for ``project`` with the pinned agent."""
    return [
        sys.executable,
        "-c",
        "import sys; from joinery.cli import main; sys.exit(main(sys.argv[1:]))",
        "chat",
        "--project",
        str(project),
        "--agent",
        pin.agent,
        "--addon-protocol",
        "2",
        *(["--keep-reviews"] if keep_reviews else []),
    ]


def _no_editor(event: dict[str, Any]) -> dict[str, Any] | None:
    """The answer to a request only the editor can serve."""
    kind = event.get("kind")
    if kind == "editor_action":
        answer = "editor_result"
    elif kind == "editor_view":
        answer = "editor_view_result"
    else:
        return None
    return {
        "kind": answer,
        "request_id": event.get("request_id", ""),
        "result": {"success": False, "message": NO_EDITOR},
    }


#: Who says Go on a to-do waiting for it: "auto" goes ahead, "never" leaves it
#: waiting, and a callable asks (the terminal) and returns True for Go.
GoPolicy = str | Callable[[dict[str, Any]], bool]


async def drive(
    command: list[str],
    first: list[dict[str, Any]],
    *,
    accept: bool = False,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    timeout: float = TIMEOUT,
    max_time: float = 0.0,
    go: GoPolicy = "auto",
    project: Path | None = None,
) -> Outcome:
    """Start the host, send ``first`` once it is connected, and follow it.

    With ``accept`` (the benchmark) the change is accepted when the turn ends and the
    run is over once it is decided. Otherwise, when the turn ends the host is asked to
    say when nothing is at work any more (``when_quiet``), and the run ends there: the
    host closes and keeps what waits for review.

    ``timeout`` is an idle timeout: seconds without any event from the host.
    ``max_time`` caps the whole run (0: no cap). When either stops the run, the
    host's input closes and it gets :data:`CLOSE_GRACE` seconds to keep the change
    it had applied (a ``kept`` event); the outcome is "review" with a note then.
    With ``project``, a change a crashed host left staged is kept the same way.
    """
    started = time.time()
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        limit=4_000_000,
    )
    outcome = Outcome()
    show = on_event or (lambda _e: None)
    why = ""
    try:
        async with asyncio.timeout(max_time or None):
            return await _follow(process, first, accept, show, outcome, timeout, go)
    except TimeoutError:
        why = (
            f"no progress for {timeout:g} s"
            if outcome.error == "idle"
            else f"it ran past --max-time {max_time:g} s"
        )
        await _stopped(process, outcome, show)
    except HostClosed:
        why = "the chat host stopped unexpectedly"
    finally:
        if process.stdin is not None:
            with contextlib.suppress(OSError):
                process.stdin.close()
        # The host closes on its own once its input ends; it keeps the review then.
        try:
            await asyncio.wait_for(process.wait(), CLOSE_GRACE)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            await process.wait()
    if outcome.state != "review" and project is not None:
        kept = keep_leftovers(project, started, why)
        if kept:
            outcome.transaction_id, outcome.state = kept[0], "review"
    outcome.error = f"Stopped: {why}."
    outcome.note = STOPPED_NOTE.format(
        why=why,
        kept="what it had applied waits for your review"
        if outcome.state == "review"
        else "it had applied nothing that could be kept",
    )
    if outcome.state != "review":
        outcome.state = "failed"
    return outcome


class HostClosed(RuntimeError):
    """The chat host's output ended before the request finished."""


def keep_leftovers(project: Path, since: float, why: str) -> list[str]:
    """Changes a host that died left staged since ``since``: kept for review with a
    note, past the same gates as ``ProjectGateway.keep_unfinished``."""
    from joinery.submission import keep_refusal
    from joinery.transactions import list_transactions

    kept: list[str] = []
    for tx in list_transactions(project):
        if tx.state != "staging" or tx.created < since or tx.held:
            continue
        try:
            changes = tx.diff()
            if not changes or keep_refusal(project, tx, changes):
                continue
            tx.review_notes = [
                *tx.review_notes,
                {
                    "severity": "warn",
                    "text": f"The run stopped ({why}) before this change was "
                    "finished; Joinery's checks did not run on it.",
                    "file": "",
                },
            ]
            tx.review()
            tx.hold("joinery run")
        except (OSError, ValueError):
            continue
        kept.append(tx.id)
    return kept


async def _stopped(
    process: asyncio.subprocess.Process,
    outcome: Outcome,
    on_event: Callable[[dict[str, Any]], None],
) -> None:
    """The run stopped early: close the host's input and read on while it closes, so
    the change it keeps (``kept``) is reported rather than lost."""
    if process.stdin is not None:
        with contextlib.suppress(OSError):
            process.stdin.close()
    if process.stdout is not None:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(CLOSE_GRACE):
                while line := await process.stdout.readline():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("kind") in EVENTS:
                        on_event(event)
                        _note_kept(outcome, event)


def _note_kept(outcome: Outcome, event: dict[str, Any]) -> None:
    """A ``kept`` event: an unfinished change the host kept for review."""
    if event.get("kind") != "kept" or event.get("task_id"):
        return
    outcome.transaction_id = str(event.get("transaction_id", ""))
    outcome.summary = str(event.get("summary", ""))
    outcome.note = str(event.get("note", ""))
    outcome.state = "review"
    outcome.kept = True


async def _follow(
    process: asyncio.subprocess.Process,
    first: list[dict[str, Any]],
    accept: bool,
    on_event: Callable[[dict[str, Any]], None],
    outcome: Outcome,
    idle: float = TIMEOUT,
    go: GoPolicy = "auto",
) -> Outcome:
    assert process.stdin is not None and process.stdout is not None
    stdin, stdout = process.stdin, process.stdout

    async def send(message: dict[str, Any]) -> None:
        stdin.write((json.dumps(message) + "\n").encode())
        await stdin.drain()

    sent = False
    reply: list[str] = []
    tasks: dict[str, dict[str, str]] = {}
    waiting: dict[str, Any] | None = None
    while True:
        try:
            line = await asyncio.wait_for(stdout.readline(), idle or None)
        except TimeoutError:
            outcome.error = "idle"
            raise
        if not line:
            break
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        if kind in EVENTS:
            on_event(event)
        if kind == "chat" and event.get("chat_id"):
            outcome.chat_id = str(event["chat_id"])
        elif kind == "kept":
            _note_kept(outcome, event)
        elif kind == "todo" and not event.get("task_id"):
            state = str(event.get("state", ""))
            waiting = event if state == "waiting" else None
        answer = _no_editor(event)
        if answer is not None:
            await send(answer)
            continue
        if kind == "connection" and event.get("state") == "error":
            raise RuntimeError(str(event.get("message", "The agent didn't connect.")))
        if kind == "connection" and event.get("state") == "connected" and not sent:
            sent = True
            for message in first:
                await send(message)
        elif kind == "message_delta" and not event.get("task_id"):
            if event.get("new_message"):
                reply = []
            reply.append(str(event.get("text", "")))
        elif kind == "message" and not event.get("task_id"):
            outcome.reply = str(event.get("text", "")).strip() or outcome.reply
        elif kind == "file_preview" and not event.get("task_id"):
            outcome.transaction_id = str(event.get("transaction_id", ""))
            outcome.summary = str(event.get("summary", ""))
            outcome.state = "review"
        elif kind == "task":
            _task(tasks, event)
        elif kind == "timing" and not event.get("task_id"):
            outcome.outcome = str(event.get("outcome", ""))
            outcome.reply = outcome.reply or "".join(reply).strip()
            if outcome.outcome not in {"done", ""} and outcome.state == "none":
                outcome.state = "failed"
            if waiting is not None and outcome.outcome == "done":
                todo_id = str(waiting.get("todo_id", ""))
                if _says_go(go, waiting):
                    # Nobody else can say Go in a run: the work starts now.
                    on_event({"kind": "notice", "text": "Going ahead with the to-do."})
                    waiting, reply, outcome.reply = None, [], ""
                    await send({"kind": "todo_go", "todo_id": todo_id})
                    continue
                outcome.todo_waiting = todo_id
                waiting = None
            if accept and outcome.transaction_id:
                await send(
                    {
                        "kind": "decision",
                        "transaction_id": outcome.transaction_id,
                        "accept": True,
                    }
                )
            elif accept:
                return outcome
            else:
                await send({"kind": "when_quiet"})
        elif kind == "file_decision" and accept:
            outcome.state = "accepted" if event.get("accepted") else "rejected"
            return outcome
        elif kind == "quiet":
            outcome.reply = outcome.reply or "".join(reply).strip()
            outcome.tasks = [t for t in tasks.values() if t["transaction_id"]]
            return outcome
    if outcome.kept:
        return outcome  # a crash after the host kept the change: nothing is lost
    raise HostClosed("The chat host closed before the request finished.")


def _says_go(go: GoPolicy, todo: dict[str, Any]) -> bool:
    if callable(go):
        return go(todo)
    return go == "auto"


def ask_go(todo: dict[str, Any]) -> bool:
    """Ask on the terminal whether the to-do may start."""
    steps = [str(s) for s in todo.get("steps") or []]
    print("The agent proposes this to-do:", file=sys.stderr)
    for number, step in enumerate(steps, 1):
        print(f"  {number}. {step}", file=sys.stderr)
    try:
        answer = input("Go? [Y/n] ")
    except EOFError:
        return True
    return answer.strip().lower() in {"", "y", "yes", "go"}


def go_policy(choice: str, interactive: bool | None = None) -> GoPolicy:
    """``--go``: ask on a terminal, go ahead without one (nobody could say Go)."""
    if choice in {"auto", "never"}:
        return choice
    if interactive is None:
        interactive = sys.stdin.isatty()
    if choice == "ask" or interactive:
        return ask_go if interactive else "never"
    return "auto"


def _task(tasks: dict[str, dict[str, str]], event: dict[str, Any]) -> None:
    """A parallel task's latest state; its change counts while it waits for review."""
    task_id = str(event.get("task_id", ""))
    waiting = str(event.get("state", "")) == "in_review"
    tasks[task_id] = {
        "task_id": task_id,
        "title": str(event.get("title", "")),
        "transaction_id": str(event.get("transaction_id", "")) if waiting else "",
    }


def prompt_message(request: str, pin: Pin, chat_id: str = "") -> dict[str, Any]:
    selection = {"mode": "auto", "model": pin.model, "effort": pin.effort}
    message: dict[str, Any] = {
        "kind": "prompt",
        "text": request,
        "selection": selection,
    }
    if chat_id:
        message["chat_id"] = chat_id
    return message


def chat_to_continue(project: Path, choice: str) -> str:
    """``--chat``: the chat id to continue ("last" for the newest), "" for a new one."""
    from joinery import chats

    choice = choice.strip()
    if not choice:
        return ""
    found = chats.listing(project)
    if choice == "last":
        if not found:
            raise ValueError("This project has no chat to continue yet.")
        return str(found[0]["id"])
    if not any(str(record.get("id", "")) == choice for record in found):
        raise ValueError(
            f"No chat {choice!r} in this project: see joinery chats list "
            f"--project {project}."
        )
    return choice


async def run_request(
    project: Path,
    request: str,
    pin: Pin,
    *,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    timeout: float = TIMEOUT,
    max_time: float = 0.0,
    command: Callable[[Path, Pin], list[str]] | None = None,
    chat_id: str = "",
    go: GoPolicy = "auto",
) -> Outcome:
    """One request, its change left waiting for review."""
    if not request.strip():
        raise ValueError("Write the request to run.")
    argv = (command or chat_command)(project, pin)
    return await drive(
        argv,
        [prompt_message(request, pin, chat_id)],
        on_event=on_event,
        timeout=timeout,
        max_time=max_time,
        go=go,
        project=project,
    )


async def run_idle(
    project: Path,
    pin: Pin,
    *,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    timeout: float = TIMEOUT,
    command: Callable[[Path, Pin], list[str]] | None = None,
) -> Outcome:
    """Start every run waiting for idle (ADR 0072) now and wait for them to finish;
    their changes stay waiting for review."""
    argv = (command or chat_command)(project, pin)
    return await drive(
        argv,
        [{"kind": "idle", "action": "now"}, {"kind": "when_quiet"}],
        on_event=on_event,
        timeout=timeout,
    )


def default_agent(project: Path) -> str:
    """The agent the project's chats use when none is pinned: the last one used."""
    from joinery import chats

    with contextlib.suppress(OSError, ValueError, KeyError, AttributeError):
        for record in chats.listing(project):
            agent = str(record.get("provider", ""))
            if agent:
                return agent
    return ""


# --- CLI ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "run",
        help="run one request without the editor; its change waits for your review",
    )
    add_arguments(parser)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--request", default="", help="what to do, in words")
    group.add_argument(
        "--idle", action="store_true", help="start the runs waiting for idle, now"
    )
    parser.add_argument(
        "--chat",
        default="",
        help="continue an earlier chat: its id (joinery chats list) or 'last'",
    )
    parser.add_argument(
        "--go",
        choices=["auto", "ask", "never"],
        default="",
        help="a to-do waiting for Go: ask, go ahead, or leave it waiting "
        "(default: ask on a terminal, go ahead otherwise)",
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The options ``run`` and ``command run`` share."""
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--agent", default="", help="claude, codex, gemini, opencode")
    parser.add_argument("--model", default="")
    parser.add_argument("--effort", default="")
    parser.add_argument(
        "--timeout",
        type=float,
        default=TIMEOUT,
        help="stop after this many seconds with no progress (default 900)",
    )
    parser.add_argument(
        "--max-time",
        type=float,
        default=0.0,
        help="stop after this many seconds in all (default: no cap)",
    )
    parser.add_argument("--json", action="store_true", help="stream JSON events")


def printer(as_json: bool) -> Callable[[dict[str, Any]], None]:
    """Each event as a JSON line, or a few plain lines for a person."""

    def show(event: dict[str, Any]) -> None:
        if as_json:
            print(json.dumps(event), flush=True)
            return
        line = describe(event)
        if line:
            print(line, flush=True)

    return show


def describe(event: dict[str, Any]) -> str:
    kind = event.get("kind")
    if kind == "turn_state":
        return f"· {event.get('state', '')}"
    if kind == "tool_activity" and event.get("allowed") is not False:
        return f"  {event.get('server', '')} {event.get('tool', '')}".rstrip()
    if kind == "check_problems":
        return f"  checks: {len(event.get('problems') or [])} to fix"
    if kind == "notice":
        return f"  {event.get('text', '')}"
    if kind == "file_preview":
        return f"Waiting for review: {event.get('summary', '')}"
    if kind == "kept":
        return f"Kept unfinished for review: {event.get('summary', '')}"
    if kind == "chat" and event.get("chat_id"):
        return f"Chat {event['chat_id']} ({event.get('mode', 'new')})"
    if kind == "tool_activity" and event.get("allowed") is False:
        return f"  refused: {event.get('tool', '')}: {event.get('reason', '')}"
    return ""


def pin_from(args: argparse.Namespace, project: Path) -> Pin:
    agent = args.agent or default_agent(project)
    if not agent:
        raise ValueError(
            "Choose the agent with --agent (claude, codex, gemini, opencode)."
        )
    return Pin(agent=agent, model=args.model, effort=args.effort)


def finish(outcome: Outcome, project: Path, as_json: bool) -> int:
    """Print the result; 0 when the run finished (with or without a change)."""
    from joinery import metrics

    data = outcome.as_dict()
    if outcome.transaction_id:
        data["cost"] = metrics.cost(project, outcome.transaction_id)
    if as_json:
        print(json.dumps(data), flush=True)
    else:
        if outcome.reply:
            print(outcome.reply)
        waiting = [outcome.transaction_id] if outcome.state == "review" else []
        waiting += [t["transaction_id"] for t in outcome.tasks]
        for identifier in waiting:
            print(
                "Waiting for your review in the Review tab, or: joinery transactions "
                f"accept --project {project} --id {identifier}"
            )
        if not waiting and outcome.state != "failed":
            print("Nothing changed.")
        if outcome.note:
            print(outcome.note)
        if outcome.todo_waiting:
            print(
                "The to-do waits for Go, which this run can't give: run again with "
                "--go auto, or set joinery todo-go --set never."
            )
        if outcome.chat_id:
            print(
                f"Chat {outcome.chat_id}: continue it with joinery run --project "
                f"{project} --chat {outcome.chat_id} --request ..."
            )
        if outcome.error:
            print(outcome.error, file=sys.stderr)
    return 1 if outcome.state == "failed" else 0


def run(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    if not (project / "project.godot").is_file():
        message = f"No Godot project at {project}."
        print(
            json.dumps({"kind": "run_result", "state": "failed", "error": message})
            if args.json
            else message
        )
        return 2
    try:
        pin = pin_from(args, project)
        show = printer(args.json)
        if args.idle:
            outcome = asyncio.run(
                run_idle(project, pin, on_event=show, timeout=args.timeout)
            )
        else:
            outcome = asyncio.run(
                run_request(
                    project,
                    args.request,
                    pin,
                    on_event=show,
                    timeout=args.timeout,
                    max_time=args.max_time,
                    chat_id=chat_to_continue(project, args.chat),
                    go=go_policy(args.go),
                )
            )
    except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
        outcome = Outcome(state="failed", error=str(exc) or type(exc).__name__)
    return finish(outcome, project, args.json)
