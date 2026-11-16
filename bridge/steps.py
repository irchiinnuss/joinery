"""Steps, then run: a to-do's independent steps as parallel tasks (ADR 0072).

With ``[agents] run_steps = "together"`` a to-do (ADR 0060) whose steps say, through
``after``, that two or more of them need nothing first has those steps made by
parallel tasks of the crew (ADR 0036), each its own change the user reviews on its
own. The lead agent takes no turn meanwhile: it hears back once, when every step is
done (in review, or decided when other steps depend on them) or as soon as one fails.
Each step is checked off on the to-do as its task moves on: started while it works, done
once its change waits for review (or nothing needed changing), back to do if it failed.

Nothing here knows which provider runs the tasks.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bridge.crew import FINAL, IN_REVIEW, QUEUED, WORKING, Crew, Task
from bridge.instructions import STEP_PROMPT
from bridge.protocol import Prompt
from joinery import done_when
from joinery.routing import routing_settings, step_model

Emit = Callable[[dict[str, Any]], None]
#: Seconds between looks at the running steps.
POLL = 0.25
#: States in which a step didn't make its change.
FAILED = frozenset({"failed", "stopped"})
#: A step's task state -> the step's progress on the to-do.
PROGRESS = {
    WORKING: "started",
    IN_REVIEW: "done",
    "accepted": "done",
    "done": "done",
}


def title(number: int, step: str) -> str:
    words = " ".join(step.split())
    head = f"Step {number} · "
    room = 48 - len(head)
    return head + (words if len(words) <= room else words[: room - 1].rstrip() + "…")


class StepRun:
    """Runs one to-do's independent steps as tasks; :meth:`run` returns their outcomes."""

    def __init__(
        self,
        crew: Crew,
        prompt: Callable[[], Prompt],
        emit: Emit,
        progress: Callable[[str], None] = lambda _text: None,
        *,
        poll: float = POLL,
    ) -> None:
        self.crew = crew
        self.prompt = prompt
        self.emit = emit
        self.progress = progress
        self.poll = poll

    def _for_step(self, base: Prompt, step: str) -> Prompt:
        """A routine step (a small edit, naming nothing to make) runs on the light
        model when the project names one (ADR 0090); the rest on the main one."""
        project = getattr(self.crew.gateway, "project", None)
        if not isinstance(project, Path):
            return base
        model = step_model(routing_settings(project), self.crew.agent, "step", step)
        if not model:
            return base
        return base.model_copy(
            update={"selection": base.selection.model_copy(update={"model": model})}
        )

    async def run(
        self, todo: done_when.Todo, together: list[int]
    ) -> list[done_when.StepOutcome]:
        """Start the steps ``together`` (0-based) and wait for them.

        Raises ValueError, before anything starts, when tasks are off for the project.
        Stopping the run (cancelling it) stops every step still working or queued.
        """
        if self.crew.limit <= 1:
            raise ValueError(
                "Parallel tasks are off for this project ([agents] parallel = 1 in "
                "joinery.toml), so the steps run one by one."
            )
        base = self.prompt()
        started: list[tuple[int, Task]] = []
        try:
            for index in together:
                step = todo.step_line(index)
                parts = STEP_PROMPT.fill(
                    number=index + 1,
                    count=len(todo.steps),
                    step=step,
                    request=todo.request,
                )
                task = self.crew.add(
                    title(index + 1, todo.steps[index]),
                    step,
                    origin="lead",
                    prompt=self._for_step(base, step),
                    text=parts["text"],
                    rules=parts["rules"],
                )
                started.append((index, task))
        except ValueError:
            await self._stop(started)
            raise
        numbers = [i + 1 for i in together]
        self.emit(
            {
                "kind": "todo",
                "todo_id": todo.id,
                "state": "steps",
                "together": numbers,
            }
        )
        self.progress(f"Making {done_when.step_words(numbers)} at once…")
        self.crew.pump()
        # Steps other steps need must be decided first: the agent builds on accepted ones.
        decided = len(together) < len(todo.steps)
        mark = self._marker(todo, started)
        self.crew.observers.append(mark)
        for _, task in started:
            mark(task)
        try:
            await self._wait(started, decided, numbers)
        except asyncio.CancelledError:
            await self._stop(started)
            raise
        finally:
            self.crew.observers.remove(mark)
        now = time.time()
        return [
            done_when.StepOutcome(
                number=index + 1,
                text=todo.steps[index],
                state=task.state,
                message=task.message,
                task_id=task.id,
                seconds=max(0.0, (task.ended or now) - task.started),
                started=task.started,
            )
            for index, task in started
        ]

    def _marker(
        self, todo: done_when.Todo, started: list[tuple[int, Task]]
    ) -> Callable[[Task], None]:
        """A crew observer that checks each step off on the to-do as its task moves."""
        steps = {task.id: index for index, task in started}

        def mark(task: Task) -> None:
            index = steps.get(task.id)
            if index is None:
                return
            state = PROGRESS.get(task.state, "")
            if task.state == QUEUED or todo.state(index) == state:
                return
            todo.mark(index, state)
            note = task.message if not state else ""
            self.emit(done_when.step_event(todo, index + 1, note))

        return mark

    async def _wait(
        self, started: list[tuple[int, Task]], decided: bool, numbers: list[int]
    ) -> None:
        tasks = [task for _, task in started]
        mine = {task.id for task in tasks}
        changed = asyncio.Event()
        asked = False

        def ask() -> None:
            # Said the moment the last step reaches review, not at the next look: the
            # user may decide them all before then, and the line would never show.
            nonlocal asked
            if (
                decided
                and not asked
                and not any(t.state in FAILED or t.state == "discarded" for t in tasks)
                and not all(t.state in FINAL for t in tasks)
                and not any(t.state in {WORKING, QUEUED} for t in tasks)
            ):
                asked = True
                self.progress(
                    f"Review {done_when.step_words(numbers)} to go on with the rest…"
                )

        def observe(task: Task) -> None:
            if task.id in mine:
                ask()
                changed.set()

        self.crew.observers.append(observe)
        try:
            while True:
                changed.clear()
                if any(t.state in FAILED for t in tasks) or (
                    decided and any(t.state == "discarded" for t in tasks)
                ):
                    # One failed: the steps not started yet stop, the ones at work finish.
                    for task in tasks:
                        if task.state == QUEUED:
                            with contextlib.suppress(ValueError):
                                await self.crew.stop(task.id)
                    if not any(t.state == WORKING for t in tasks):
                        return
                elif decided:
                    if all(t.state in FINAL for t in tasks):
                        return
                    ask()
                elif all(t.state == IN_REVIEW or t.state in FINAL for t in tasks):
                    return
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(changed.wait(), self.poll)
        finally:
            self.crew.observers.remove(observe)

    async def _stop(self, started: list[tuple[int, Task]]) -> None:
        for _, task in started:
            if task.state in {WORKING, QUEUED}:
                with contextlib.suppress(ValueError):
                    await self.crew.stop(task.id)


def made_note(outcomes: list[done_when.StepOutcome]) -> str:
    """Joinery's own line when every step was made by a task and nothing is left."""
    words = done_when.step_words([o.number for o in outcomes])
    return (
        f"Made {words} at the same time, each as its own change. "
        "Review each one in the Review tab."
    )


def unchecked(todo: done_when.Todo, count: int) -> list[done_when.Result]:
    """The done-when items when every step was its own change: no one change to check."""
    return [
        done_when.Result(
            c.id,
            c.text,
            "inconclusive",
            f"Made as {count} separate changes, each checked on its own. Play the game "
            "once you have accepted them.",
        )
        for c in todo.done_when
    ]
