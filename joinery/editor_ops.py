"""Typed, agent-neutral scene scaffolding; no scripts or arbitrary property writes."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    FiniteFloat,
    JsonValue,
    model_validator,
)

from joinery.hook import mcp_violation

Vector = tuple[FiniteFloat, FiniteFloat, FiniteFloat]

#: Characters Godot refuses in a node name (it swaps them for ``_`` or drops the
#: name), and ``#``, which node paths and scene identities can't round-trip.
GODOT_FORBIDDEN = ("#", ".", ":", "@", "/", '"', "%")
_NAME = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
_NAME_OK = re.compile(_NAME)


def suggest_node_name(name: str) -> str:
    """A name like ``name`` that passes: its letters, digits and underscores."""
    kept = re.sub(r"[^A-Za-z0-9_]", "", name)[:64]
    if not kept:
        return "Node"
    return ("_" + kept)[:64] if kept[0].isdigit() else kept


def node_name_problem(name: str) -> str:
    """Why ``name`` can't be a node's name, with a name that can; "" when it can.

    The one check every scene op's node name goes through. A bad name is refused
    with this message for the agent to act on, never renamed behind its back.
    """
    if _NAME_OK.match(name):
        return ""
    suggestion = suggest_node_name(name)
    bad = [c for c in GODOT_FORBIDDEN if c in name]
    if bad:
        listed = ", ".join(f"'{c}'" for c in bad)
        return f"Node names can't contain {listed}; try '{suggestion}'."
    if not name:
        return f"Give the node a name; try '{suggestion}'."
    if len(name) > 64:
        return f"Node names are at most 64 characters; try '{suggestion}'."
    return (
        "Node names use only letters, digits and underscores and don't start "
        f"with a digit; try '{suggestion}'."
    )


def _checked_name(value: object) -> object:
    if isinstance(value, str):
        problem = node_name_problem(value)
        if problem:
            raise ValueError(problem)
    return value


def _optional_name(value: object) -> object:
    return value if value == "" else _checked_name(value)


#: A node's name, checked by :func:`node_name_problem` before the schema pattern,
#: which stays so agents see the rule up front.
NodeName = Annotated[str, BeforeValidator(_checked_name)]
#: The same, where "" means "let Godot pick the name".
OptionalNodeName = Annotated[str, BeforeValidator(_optional_name)]


class Box(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["create_box"]
    name: NodeName = Field(default="Box", pattern=_NAME)
    size: Vector = (1, 1, 1)
    position: Vector | None = None

    @model_validator(mode="after")
    def bounds(self) -> Box:
        if any(not 0.001 <= v <= 10000 for v in self.size):
            raise ValueError("Box sizes must be between 0.001 and 10000 metres.")
        if self.position and any(abs(v) > 1_000_000 for v in self.position):
            raise ValueError("Position is outside the supported scene bounds.")
        return self


class Transform(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["set_transform"]
    target: str = Field(min_length=1, max_length=1024)
    position: Vector | None = None
    rotation_degrees: Vector | None = None
    scale: Vector | None = None
    relative: bool = False

    @model_validator(mode="after")
    def bounds(self) -> Transform:
        if (
            self.position is None
            and self.rotation_degrees is None
            and self.scale is None
        ):
            raise ValueError("Supply a position, rotation, or scale.")
        if self.scale and any(not 0.001 <= v <= 10000 for v in self.scale):
            raise ValueError("Scale must be positive and nonzero.")
        for vector in (self.position, self.rotation_degrees):
            if vector and any(abs(v) > 1_000_000 for v in vector):
                raise ValueError("Transform is outside the supported scene bounds.")
        return self


class NodeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal[
        "create_node",
        "instantiate_scene",
        "delete_node",
        "duplicate_node",
        "rename_node",
        "reparent_node",
        "set_node_properties",
        "attach_script",
        "connect_signal",
        "disconnect_signal",
    ]
    target: str = ""
    parent: str = ""
    node_type: str = Field(default="Node3D", pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    name: OptionalNodeName = Field(default="", pattern=r"^$|" + _NAME)
    properties: dict[str, JsonValue] = {}
    script_path: str = ""
    resource_path: str = ""
    signal_name: str = ""
    method_name: str = ""
    receiver: str = ""
    #: instantiate_scene only: the node the new instance replaces. The instance
    #: takes its parent, place in the tree and transform; the node is removed.
    replace: str = ""
    #: With replace: scale the instance evenly to the replaced node's size and
    #: stand it where that node stood (its bounds' bottom centre).
    fit_size: bool = False
    #: instantiate_scene only: the node the new instance stands on. Its bounds'
    #: bottom rests on that node's top, centred (or at ``at``), beside it under
    #: its parent (or under it with ``as_child``).
    on: str = ""
    #: With on: metres (x, z) from the top's centre, in world axes.
    at: tuple[FiniteFloat, FiniteFloat] | None = None
    #: With on: the instance becomes the node's child instead of its sibling.
    as_child: bool = False

    @model_validator(mode="after")
    def required_fields(self) -> NodeOperation:
        if (self.replace or self.fit_size) and self.operation != "instantiate_scene":
            raise ValueError("Only instantiate_scene replaces a node.")
        if self.fit_size and not self.replace:
            raise ValueError("fit_size needs replace: the node whose size to keep.")
        if self.on and self.operation != "instantiate_scene":
            raise ValueError("Only instantiate_scene places a node on another.")
        if self.on and self.replace:
            raise ValueError("Use replace or on, not both.")
        if (self.at is not None or self.as_child) and not self.on:
            raise ValueError("at and as_child need on: the node to stand it on.")
        if self.at is not None and any(abs(v) > 10000 for v in self.at):
            raise ValueError("at is outside the supported scene bounds.")
        if (
            self.operation not in {"create_node", "instantiate_scene"}
            and not self.target
        ):
            raise ValueError("This operation needs a target object.")
        if self.operation == "rename_node" and not self.name:
            raise ValueError("Provide the new node name.")
        if self.operation == "instantiate_scene" and (
            not self.resource_path.startswith("res://")
            or ".." in Path(self.resource_path.removeprefix("res://")).parts
        ):
            raise ValueError("Instantiate a scene resource within this project.")
        if self.operation == "set_node_properties" and not self.properties:
            raise ValueError("Provide the properties to change.")
        if any(key in {"script", "owner", "name"} for key in self.properties):
            raise ValueError(
                "Use source/script tools or rename operations for these properties."
            )
        if self.operation == "attach_script" and (
            not self.script_path.startswith("res://")
            or not self.script_path.endswith(".gd")
            or ".." in Path(self.script_path.removeprefix("res://")).parts
        ):
            raise ValueError(
                "Attach an existing validated GDScript within the project."
            )
        if self.operation in {"connect_signal", "disconnect_signal"} and not (
            self.signal_name and self.method_name and self.receiver
        ):
            raise ValueError(
                "Supply the signal, receiver identity and receiver method."
            )
        _finite_properties(self.properties)
        return self


def _finite_properties(value: JsonValue, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("Property resources are nested too deeply.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Scene properties must contain finite numbers.")
    if isinstance(value, dict):
        for child in value.values():
            _finite_properties(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _finite_properties(child, depth + 1)


Operation = Annotated[Box | Transform | NodeOperation, Field(discriminator="operation")]


class SceneEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=160)
    operations: list[Operation] = Field(min_length=1, max_length=32)


def validate_edit(
    project: Path, arguments: object, targets: tuple[str, ...]
) -> SceneEdit:
    edit = SceneEdit.model_validate(arguments)
    verdict = mcp_violation("godot", "scene_edit", project)
    if verdict:
        raise ValueError(verdict.reason)
    for operation in edit.operations:
        if (
            isinstance(operation, (Transform, NodeOperation))
            and operation.target
            and operation.target not in targets
        ):
            raise ValueError(
                "Transform edits must refer to an object selected for this prompt."
            )
        if isinstance(operation, NodeOperation):
            for identity in (
                operation.parent,
                operation.receiver,
                operation.replace,
                operation.on,
            ):
                if identity and identity not in targets:
                    raise ValueError(
                        "Referenced nodes must be in the current editor context."
                    )
    return edit


SCENE_TOOL = {
    "type": "function",
    "name": "joinery_edit_scene",
    "description": (
        "Preview a reversible edit in the currently open Godot scene. "
        "Create, delete, duplicate, rename, reparent or instantiate nodes; set properties/resources; attach an existing validated "
        "GDScript and connect/disconnect persistent signals. Targets use stable current editor identities. "
        "Vector properties use numeric arrays. Resources use {resource_type: ClassName, properties: {...}} or "
        "{resource_path: res://existing.tres}. Plain greyboxes are scene scaffolding; finished assets use the owning creative app. "
        "instantiate_scene places a model or scene by its res:// path, including one made earlier in this same change "
        "(it is reviewed together with the model). To swap a placeholder for it, pass replace with the placeholder's identity "
        "(it keeps the placeholder's parent and transform) and fit_size true to keep the placeholder's size. "
        "To put it on something (a mug on a table), pass on with that node's identity: it rests on the node's top, "
        "centred, beside it under the same parent; at [x, z] moves it that many metres from the centre and as_child "
        "true puts it under the node. "
        "Use this tool for requested live scene changes instead of manual steps. The user then accepts "
        "or rejects the preview in the Review panel below the viewport. Do not claim a preview is accepted or saved."
    ),
    "inputSchema": SceneEdit.model_json_schema(),
}
