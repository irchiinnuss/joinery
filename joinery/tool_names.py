"""Joinery's own tools by the name each agent calls them.

Prompts, guides and refusals name joinery-registry tools plainly (``todo_step``,
``call_tool``), but no agent can call a bare name: each one prefixes an MCP
server's tools its own way. Under Claude Code ``todo_step`` is
``mcp__joinery-registry__todo_step`` and a bare call fails with "No such tool
available". :func:`callable_names` writes every registry tool named in a text in
the agent's own form, so the same text holds for Claude Code, Codex, Gemini CLI and
opencode alike (ADR 0006).
"""

from __future__ import annotations

import re
from functools import cache

from joinery.agents import get
from joinery.roles import REGISTRY_TOOLS

REGISTRY = "joinery-registry"


def registry_prefix(agent_id: str) -> str:
    """What ``agent_id`` puts before a joinery-registry tool's name when it calls one.

    Claude Code: ``mcp__joinery-registry__``. Codex keeps only letters, digits and
    ``_`` in a server's name: ``mcp__joinery_registry__``. Gemini CLI:
    ``mcp_joinery-registry_``. opencode: ``joinery-registry_``. An agent Joinery
    doesn't know gets no prefix."""
    try:
        naming = get(agent_id).mcp_naming
    except KeyError:
        return ""
    if naming == "dunder":
        server = REGISTRY.replace("-", "_") if agent_id == "codex" else REGISTRY
        return f"mcp__{server}__"
    if naming == "single":
        return f"mcp_{REGISTRY}_"
    return f"{REGISTRY}_"


def callable_name(agent_id: str, tool: str) -> str:
    """``tool`` (a joinery-registry tool) as ``agent_id`` calls it."""
    return registry_prefix(agent_id) + tool


#: Registry tools whose names can't be mistaken for words ("todo_step", not
#: "precheck" or "playtest", which prose uses too): written in an agent's form
#: wherever a text names them.
NAMED = tuple(sorted((t for t in REGISTRY_TOOLS if "_" in t), key=len, reverse=True))


@cache
def _named() -> re.Pattern[str]:
    # Not part of a longer name, a path (res://a/call_tool.gd) or a prefixed name.
    return re.compile(
        r"(?<![\w./:-])(" + "|".join(map(re.escape, NAMED)) + r")(?![\w-]|\.\w)"
    )


def callable_names(text: str, agent_id: str) -> str:
    """``text`` with each registry tool it names written as ``agent_id`` calls it."""
    prefix = registry_prefix(agent_id)
    if not prefix or not text:
        return text
    return _named().sub(lambda found: prefix + found.group(1), text)


def naming_note(agent_id: str) -> str:
    """One line on how Joinery's tools are called under ``agent_id`` ("" when its
    tools take no prefix): the guides name the short ones (precheck, playtest) bare."""
    prefix = registry_prefix(agent_id)
    if not prefix:
        return ""
    return (
        f"Joinery's own tools are on the {REGISTRY} MCP server: call each by its full "
        f"name, {prefix}<name> (precheck is {prefix}precheck, todo_step is "
        f"{prefix}todo_step). Project tools that discover_tools lists (read_file, "
        "write_file, inspect_document, game_brief…) are not called directly: pass "
        "their server and name to read_tool or call_tool. "
    )


def for_agent(text: str, agent_id: str) -> str:
    """Joinery's instructions for ``agent_id``: how its tools are called, then the
    text with every registry tool in the agent's form."""
    return callable_names(naming_note(agent_id) + text, agent_id)
