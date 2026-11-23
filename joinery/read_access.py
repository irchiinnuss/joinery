"""Audited read operations shared by the gateway and specialist policy hook."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

GODOT_READS = {
    "get_godot_version",
    "get_project_info",
    "list_project_files",
    "read_file",
    "read_scene",
    "read_project_settings",
    "get_uid",
    "validate_script",
    "validate_scripts",
}
BLENDER_READS = {
    "get_blendfile_summary_datablocks",
    "get_objects_summary",
    "get_blendfile_summary_missing_files",
    "get_blendfile_summary_linked_libraries",
    "get_blendfile_summary_paths",
    "get_python_api_docs",
    "search_api_docs",
    "search_manual_docs",
}
FIRST_PARTY_READS = {
    "document_capabilities",
    "inspect_document",
    "validate_project",
    "transaction_status",
    "kit_list",
    "audit_animations",
    "terrain_info",
}


#: The registry's own reads that call_tool and read_tool reach. tool_schema is
#: answered by the gateway itself, for whichever server it names.
REGISTRY = "joinery-registry"
REGISTRY_READS = {
    "art_direction",
    "game_brief",
    "game_setup",
    "list_assets",
    "preview_asset",
    "resolve_source",
    "kit_list",
    "tool_schema",
}


def read_only(server: str, tool: str) -> bool:
    return (
        (server == "godot" and tool in GODOT_READS)
        or (server == "blender" and tool in BLENDER_READS)
        or (server == REGISTRY and tool in REGISTRY_READS)
        or (server == "claude-collider" and tool == "cc_status")
        or (
            server
            in {
                "joinery-blender-ops",
                "joinery-paint",
                "joinery-sound",
                "joinery-materialmaker",
                "joinery-verify",
            }
            and tool in FIRST_PARTY_READS
        )
    )


def not_a_read(server: str, tool: str) -> str:
    """Why read_tool refused a call: it reaches audited reads only."""
    return (
        f"{server}.{tool} is not a read-only tool; read_tool only reaches reads "
        "(validate_script, read_file, inspect_document, ...). Call it with call_tool."
    )


def call_target(values: Mapping[str, Any]) -> dict[str, Any]:
    """call_tool and read_tool arguments in their one spelling, the same for the
    policy hook and the gateway: ``name`` read as ``tool``; a registry read with no
    server is the registry's; ``tool_schema`` called through them is the registry's,
    for the server they named (``tool_schema(server, tool)``)."""
    out = dict(values)
    tool = str(out.get("tool") or out.get("name") or "")
    if "name" in out and not out.get("tool"):
        del out["name"]
    server = str(out.get("server") or "")
    if tool == "tool_schema" and server != REGISTRY:
        inner = out.get("arguments")
        if isinstance(inner, str):
            try:
                inner = json.loads(inner) if inner.strip() else {}
            except ValueError:
                inner = None
        if isinstance(inner, dict) and server and not inner.get("server"):
            out["arguments"] = {**inner, "server": server}
        server = REGISTRY
    if not server and tool in REGISTRY_READS:
        server = REGISTRY
    if tool:
        out["tool"] = tool
    out["server"] = server
    return out


def missing_target(values: Mapping[str, Any]) -> str:
    """What call_tool or read_tool lacks, in words the agent can act on ("" when
    nothing)."""
    if not values.get("tool"):
        return (
            "Name the tool: call_tool(server='godot', tool='write_file', "
            "arguments={...}); discover_tools lists each tool's server and name."
        )
    if not values.get("server"):
        return (
            f"Name the server of {values['tool']}: discover_tools lists each tool's "
            "server."
        )
    return ""
