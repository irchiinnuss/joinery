"""The real chat host with a scripted provider behind it, for the sidebar's UI scenarios.

    python tests/godot/scripted_host.py --project DIR --script turns.json [--agent codex]

Everything between the sidebar and the provider is Joinery's own code: the framed pipes,
``bridge.stdio.serve`` with its turn controller, timing, chat recorder, cancel and policy
stop. Only three things are stand-ins:

- the provider (``stdio.Bridge``): it plays the turn the script names for the request's
  text, so a run costs nothing and is the same every time;
- the project gateway and its RPC endpoint (the test_chats.py stand-ins, kept permissive):
  no transactions, sockets or tools;
- the user's decisions on a change: a ``decision`` line is answered here with a
  ``file_decision``, as the gateway would after an accept or a discard.

The script is JSON: ``{"turns": {"<request text>": [step, ...], "*": [...]}, "models":
[...], "decision_ms": 50, "connect_ms": 0}``. A request with no turn of its own plays
``"*"``; a message from another chat (``chat_task``) plays its turn as that chat's own task
events. Steps, in order:

- ``{"wait": ms}``: nothing for a while (thinking, a slow provider);
- ``{"execution": {...}}``: the model at work (``bridge.protocol.Execution``);
- ``{"delta": text, "new": bool}``: one piece of a streamed reply;
- ``{"stream": text, "chunk": n, "every_ms": ms}``: the text streamed n characters at a time;
- ``{"message": text, "level": "info"|"warn"}``: a whole reply or a warning;
- ``{"trail": {...}}``: a work-trail line (``bridge.protocol.Trail``);
- ``{"activity": {...}}``: a tool call and the gate's verdict (``ToolActivity``);
- ``{"usage": {...}}``: token use;
- ``{"emit": {...}}``: any event, straight to the sidebar (cards, change previews…);
- ``{"raw": text}``: text written to the sidebar as is (a line that is not JSON, half a line);
- ``{"raw_split": {...}}``: an event written in two pieces a moment apart, split inside its
  first multi-byte character, as a pipe may deliver it;
- ``{"raise": text}``, ``{"raise_os": text}``, ``{"timeout": text}``: the provider fails;
- ``{"policy_stop": reason}``: the project policy stops the turn (a Rule Zero stop);
- ``{"exit": code}``: the host process dies (the connection drops);
- ``{"hang": true}``: never finish (only Stop ends it);
- ``{"done": bool}``: the turn ends (``True`` when the steps run out).

Each request is also printed to stderr as ``SCRIPTED-PROMPT <text>`` for the test's log.
"""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import json
import os
import signal
import sys
import tempfile
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge import stdio
from bridge.models import ModelChoice
from bridge.protocol import (
    Done,
    Execution,
    Message,
    MessageDelta,
    ToolActivity,
    Trail,
    Usage,
)

Step = dict[str, Any]
#: The policy stop the host handed its RPC endpoint (``{"policy_stop": reason}``).
_stop: list[Callable[[str], Coroutine[Any, Any, None]]] = []
#: Tasks started here: asyncio keeps only a weak reference to a task, and one collected
#: mid-await (the input pump) would leave the host waiting on its input for good.
_tasks: set[asyncio.Task[Any]] = set()


def _keep(task: asyncio.Task[Any]) -> asyncio.Task[Any]:
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


class ScriptedBridge:
    """A provider that plays the script's turn for each request."""

    script: ClassVar[dict[str, Any]] = {}

    def __init__(self, _agent: str, role: str = "") -> None:
        self.role = role
        self.model_choices = [
            ModelChoice.model_validate(model) for model in self.script.get("models", [])
        ]
        self.thread = "scripted-thread"
        self._cancelled = asyncio.Event()

    def bind_gateway(self, _path: Path) -> None:
        pass

    async def connect(self, _project: Path, _profile: str = "") -> None:
        await asyncio.sleep(float(self.script.get("connect_ms", 0)) / 1000)

    async def refresh_models(self) -> None:
        pass

    def editor_result(self, _request_id: str, _result: dict[str, Any]) -> None:
        pass

    @property
    def handle(self) -> str:
        return self.thread

    async def resume(self, handle: str) -> bool:
        self.thread = handle
        return True

    async def new_session(self) -> None:
        self.thread = "scripted-thread-new"

    async def send(self, prompt: Any) -> AsyncIterator[Any]:
        self._cancelled.clear()
        text = str(prompt.text)
        print("SCRIPTED-PROMPT " + text.replace("\n", " ")[:200], file=sys.stderr)
        turns: dict[str, list[Step]] = self.script.get("turns", {})
        steps = turns.get(text, turns.get("*", []))
        ended = False
        for step in steps:
            async for event in self._play(step):
                if isinstance(event, Done):
                    ended = True
                yield event
            if ended:
                return
        yield Done(ok=True)

    async def _play(self, step: Step) -> AsyncIterator[Any]:
        if "wait" in step:
            await asyncio.sleep(float(step["wait"]) / 1000)
        elif "execution" in step:
            yield Execution.model_validate(
                {"model": "scripted", "name": "Scripted", "mode": "fast"}
                | step["execution"]
            )
        elif "delta" in step:
            yield MessageDelta(
                text=str(step["delta"]), new_message=bool(step.get("new"))
            )
        elif "stream" in step:
            text = str(step["stream"])
            size = max(1, int(step.get("chunk", 8)))
            pause = float(step.get("every_ms", 20)) / 1000
            for start in range(0, len(text), size):
                yield MessageDelta(
                    text=text[start : start + size],
                    new_message=start == 0 and bool(step.get("new", True)),
                )
                await asyncio.sleep(pause)
        elif "message" in step:
            yield Message(text=str(step["message"]), level=step.get("level", "info"))
        elif "trail" in step:
            yield Trail.model_validate(step["trail"])
        elif "activity" in step:
            yield ToolActivity.model_validate(step["activity"])
        elif "usage" in step:
            yield Usage.model_validate(step["usage"])
        elif "emit" in step:
            stdio.emit(dict(step["emit"]))
        elif "raw" in step:
            sys.stdout.write(str(step["raw"]))
            sys.stdout.flush()
        elif "raw_split" in step:
            data = (json.dumps(step["raw_split"], ensure_ascii=False) + "\n").encode()
            middle = next(i for i, byte in enumerate(data) if byte >= 0x80) + 1
            sys.stdout.buffer.write(data[:middle])
            sys.stdout.flush()
            await asyncio.sleep(0.2)
            sys.stdout.buffer.write(data[middle:])
            sys.stdout.flush()
        elif "raise" in step:
            raise RuntimeError(str(step["raise"]))
        elif "raise_os" in step:
            raise OSError(str(step["raise_os"]))
        elif "timeout" in step:
            raise TimeoutError(str(step["timeout"]))
        elif "policy_stop" in step:
            if _stop:
                # The stop cancels this very turn; let it.
                _keep(
                    asyncio.get_running_loop().create_task(
                        _stop[0](str(step["policy_stop"]))
                    )
                )
                await asyncio.sleep(30)
        elif "exit" in step:
            sys.stdout.flush()
            os._exit(int(step["exit"]))
        elif "hang" in step:
            await self._cancelled.wait()
        elif "done" in step:
            yield Done(ok=bool(step["done"]))

    async def cancel(self) -> None:
        self._cancelled.set()

    async def close(self) -> None:
        # A provider's session ends with it: a hanging turn ends too.
        self._cancelled.set()


class ScriptedGateway(MagicMock):
    """The project gateway, minus transactions: what the host reads is there."""

    def __init__(self, project: Path) -> None:
        super().__init__()
        self.project = project
        self.transaction = None
        self.art_gate = None
        self.timing = None
        self.editor_context: dict[str, str] = {}
        self.target_context = ()
        self.request_summary = ""
        self.keep_reviews = False
        self.keep_unfinished = False
        self.keeps_unfinished = False
        self.task_id = ""
        self.cancel = AsyncMock()
        self.close = AsyncMock()
        self.staged_files = AsyncMock(return_value={})

    def _get_child_mock(self, **kw: Any) -> Any:
        # Children are plain mocks, not gateways (this class's own init takes a project).
        return MagicMock(**kw)


class ScriptedRPC:
    def __init__(self, _gateway: Any, stop: Any, role: str = "") -> None:
        self.path = Path("scripted-gateway.sock")
        self.stop_reason = ""
        self.delegate: Any = None
        self.playtest: Any = None
        if not role:
            _stop.append(stop)

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass


class ChatTasks:
    """Messages from another chat (``chat_task``, ADR 0036): each plays its turn's steps as
    that chat's own task events, the way ``Crew.start_chat`` reports them."""

    def __init__(self) -> None:
        self.count = 0
        self.running: dict[str, asyncio.Task[None]] = {}

    def start(self, data: dict[str, Any]) -> None:
        self.count += 1
        task_id = f"chat-task-{self.count}"
        self.running[task_id] = asyncio.get_running_loop().create_task(
            self._play(task_id, data)
        )

    async def stop(self, task_id: str) -> bool:
        job = self.running.pop(task_id, None)
        if job is None:
            return False
        job.cancel()
        stdio.emit(
            {"kind": "task", "task_id": task_id, "origin": "chat", "state": "stopped"}
        )
        return True

    async def _play(self, task_id: str, data: dict[str, Any]) -> None:
        text = str(data.get("text", ""))
        chat = str(data.get("chat_id", "")) or f"scripted-chat-{self.count}"
        base = {"task_id": task_id, "origin": "chat", "chat_id": chat}
        stdio.emit(
            {
                "kind": "task",
                **base,
                "ref": data.get("ref", ""),
                "state": "working",
                "title": text,
            }
        )
        turns: dict[str, list[Step]] = ScriptedBridge.script.get("turns", {})
        first = True
        for step in turns.get(text, turns.get("*", [])):
            if "wait" in step:
                await asyncio.sleep(float(step["wait"]) / 1000)
            elif "stream" in step:
                body = str(step["stream"])
                size = max(1, int(step.get("chunk", 8)))
                for start in range(0, len(body), size):
                    stdio.emit(
                        {
                            "kind": "message_delta",
                            **base,
                            "text": body[start : start + size],
                            "new_message": first,
                        }
                    )
                    first = False
                    await asyncio.sleep(float(step.get("every_ms", 20)) / 1000)
            elif "hang" in step:
                await asyncio.sleep(3600)
        self.running.pop(task_id, None)
        stdio.emit({"kind": "task", **base, "state": "done"})


def answer_decisions(
    original: Callable[[Any], Awaitable[tuple[asyncio.StreamReader, Any]]],
) -> Callable[[Any], Awaitable[tuple[asyncio.StreamReader, Any]]]:
    """Wrap the host's stdin reader: a decision on a change, and a message from another
    chat (with its Stop), are answered here."""
    chat_tasks = ChatTasks()

    async def reader(stream: Any) -> tuple[asyncio.StreamReader, Any]:
        source, transport = await original(stream)
        filtered = asyncio.StreamReader(limit=1_000_000)

        async def pump() -> None:
            try:
                await forward()
            except BrokenPipeError:
                # The sidebar went while a decision was answered: so does this host.
                os._exit(0)
            except BaseException:
                # Never die quietly: the host would wait on its input for good.
                with _trace_path("pump").open("w") as log:
                    traceback.print_exc(file=log)
                raise

        async def forward() -> None:
            while line := await source.readline():
                try:
                    data = json.loads(line)
                except ValueError:
                    data = None
                if isinstance(data, dict) and data.get("kind") == "chat_task":
                    chat_tasks.start(data)
                    continue
                if (
                    isinstance(data, dict)
                    and data.get("kind") == "cancel"
                    and await chat_tasks.stop(str(data.get("task_id", "")))
                ):
                    continue
                if isinstance(data, dict) and data.get("kind") == "decision":
                    accepted = bool(data.get("accept", False))
                    identifier = str(data.get("transaction_id", ""))
                    # A real accept applies files first; the script says how long.
                    delay = float(ScriptedBridge.script.get("decision_ms", 50)) / 1000
                    await asyncio.sleep(delay)
                    stdio.emit(
                        {
                            "kind": "file_decision",
                            "transaction_id": identifier,
                            "accepted": accepted,
                            "changes": [],
                            "message": "",
                        }
                    )
                    stdio.emit(
                        {"kind": "turn_state", "state": "idle", "reason": "decided"}
                    )
                    continue
                filtered.feed_data(line)
                if isinstance(data, dict) and data.get("kind") == "shutdown":
                    break
            filtered.feed_eof()
            # The sidebar is gone: a scripted turn still waiting (a hang, another chat's
            # task) must never keep this process alive after a short grace.
            asyncio.get_running_loop().call_later(3.0, os._exit, 0)

        _keep(asyncio.get_running_loop().create_task(pump()))
        return filtered, transport

    return reader


def _trace_path(kind: str) -> Path:
    """Where this host writes what it knows when stuck: JOINERY_SCRIPTED_TRACE_DIR or temp."""
    folder = os.environ.get("JOINERY_SCRIPTED_TRACE_DIR") or tempfile.gettempdir()
    return Path(folder) / f"joinery-scripted-host-{os.getpid()}-{kind}.trace"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--agent", default="codex")
    # The editor that started it (Joinery Remote hands that sidebar what it owns).
    parser.add_argument("--editor-pid", type=int, default=0)
    args = parser.parse_args()
    # A host that won't end can be asked where it is: SIGUSR1 writes every stack here.
    # Windows has neither the signal nor faulthandler.register; there the test just ends it.
    if hasattr(signal, "SIGUSR1") and hasattr(faulthandler, "register"):
        trace = _trace_path("stacks")
        faulthandler.register(signal.SIGUSR1, file=trace.open("w"), all_threads=True)
    ScriptedBridge.script = json.loads(args.script.read_text(encoding="utf-8"))
    stand_ins: dict[str, Any] = {
        "Bridge": ScriptedBridge,
        "ProjectGateway": ScriptedGateway,
        "GatewayRPC": ScriptedRPC,
        "Specialists": lambda *_a: AsyncMock(),
        "game_suggestions": lambda _p: {"kind": "suggestions", "items": []},
        "input_reader": answer_decisions(stdio.input_reader),
    }
    for name, value in stand_ins.items():
        setattr(stdio, name, value)
    # Stand-in workers: the real disk and Docker are never asked before a turn.
    os.environ.setdefault("JOINERY_MACHINE_CHECK", "off")
    return asyncio.run(
        stdio.serve(args.project.resolve(), args.agent, editor_pid=args.editor_pid)
    )


if __name__ == "__main__":
    raise SystemExit(main())
