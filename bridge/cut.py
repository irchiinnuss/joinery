"""/cut's Write, as a reviewed change without an agent.

The cut card sends ``cut_write``::

    {"kind": "cut_write", "cuts": ["3fa2c1…", …]}   # the proposed cuts the user kept ticked

The host gathers the findings again (``joinery.cut.plan``), refuses a cut that is no
longer proposed (a Keep feature is never one), and starts a scripted task of the crew
that writes ``design/polish.md`` and moves the chosen features to the brief's "Out of
scope" on the staged copy. It is one change in Review like any other: nothing in the
game is deleted, and Discard keeps the brief as it was.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bridge.crew import Crew, Task
from bridge.protocol import Prompt
from joinery import cut
from joinery.brief import BRIEF_PATH


def chosen(project: Path, data: dict[str, Any]) -> tuple[cut.Plan, list[cut.Cut]]:
    ids = data.get("cuts")
    if ids is None:
        ids = []
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
        raise TypeError("cuts is a list of proposed cut ids.")
    found = cut.plan(project)
    return found, cut.choose(found, ids)


async def start(
    crew: Crew, project: Path, data: dict[str, Any], prompt: Prompt
) -> Task:
    """Start the write as a scripted task of the crew; its change waits in Review."""
    found, picked = chosen(project, data)
    lines = [f"Write design/polish.md: {found.headline}."]
    lines += [
        f"Cut from the brief: {c.text} ({cut.REASON_WORDS[c.reason]})." for c in picked
    ]
    targets = [cut.POLISH_PATH.as_posix()] + ([BRIEF_PATH.as_posix()] if picked else [])
    return await crew.start_scripted(
        cut.summary(picked),
        "\n".join(lines),
        [],
        prompt=prompt,
        targets=targets,
        facts=cut.facts(found, picked),
        edit=lambda stage: cut.apply(stage, found, picked),
    )
