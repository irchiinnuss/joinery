import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from joinery.editor_ops import (
    NodeOperation,
    node_name_problem,
    suggest_node_name,
    validate_edit,
)


@pytest.fixture
def game(tmp_path: Path) -> Path:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    return tmp_path


def test_plain_godot_box(game: Path) -> None:
    edit = validate_edit(
        game,
        {
            "summary": "Box",
            "operations": [{"operation": "create_box", "size": [1, 1, 1]}],
        },
        (),
    )
    assert edit.operations[0].size == (1, 1, 1)


@pytest.mark.parametrize(
    "op",
    [
        {"operation": "execute_script", "code": "whatever"},
        {"operation": "create_box", "size": [1, -1, 1]},
        {"operation": "create_box", "size": [1, 1, float("nan")]},
        {"operation": "create_box", "name": "../escape"},
        {"operation": "create_box", "size": [1, 1, 1], "script": "bad"},
        {"operation": "set_transform", "target": "@Door", "scale": [0, 1, 1]},
        {"operation": "set_transform", "target": "@Door"},
    ],
)
def test_invalid_operations_fail_closed(game: Path, op: dict) -> None:
    with pytest.raises(ValidationError):
        validate_edit(game, {"summary": "Bad", "operations": [op]}, ("@Door",))


def test_target_binding(game: Path) -> None:
    args = {
        "summary": "Move",
        "operations": [
            {"operation": "set_transform", "target": "@Door", "position": [1, 0, 0]}
        ],
    }
    with pytest.raises(ValueError, match="selected"):
        validate_edit(game, args, ("@Window",))
    assert validate_edit(game, args, ("@Door",))


def test_respects_shared_project_gate(
    game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from joinery.policy import Severity, Verdict

    seen = []

    def deny(server: str, tool: str, project: Path) -> Verdict:
        seen.append((server, tool, project))
        return Verdict(Severity.DENY, "Denied by the shared policy")

    monkeypatch.setattr("joinery.editor_ops.mcp_violation", deny)
    with pytest.raises(ValueError, match="shared policy"):
        validate_edit(
            game, {"summary": "Box", "operations": [{"operation": "create_box"}]}, ()
        )
    assert seen == [("godot", "scene_edit", game)]


MUG = {"operation": "instantiate_scene", "resource_path": "res://art/models/mug.glb"}


def test_on_stands_a_new_instance_on_a_node(game: Path) -> None:
    table = "res://kitchen.tscn::Table"
    edit = validate_edit(
        game,
        {
            "summary": "Mug on the table",
            "operations": [{**MUG, "on": table, "at": [0.2, -0.1], "as_child": True}],
        },
        (table,),
    )
    (op,) = edit.operations
    assert isinstance(op, NodeOperation)
    assert op.on == table and op.at == (0.2, -0.1) and op.as_child
    with pytest.raises(ValueError, match="current editor context"):
        validate_edit(
            game, {"summary": "Mug", "operations": [{**MUG, "on": table}]}, ()
        )


@pytest.mark.parametrize(
    "op",
    [
        {**MUG, "at": [0, 0]},
        {**MUG, "as_child": True},
        {**MUG, "on": "@Table", "replace": "@Table"},
        {"operation": "create_node", "on": "@Table"},
        {**MUG, "on": "@Table", "at": [0, 1e9]},
    ],
)
def test_on_is_only_for_placing_an_instance(game: Path, op: dict) -> None:
    with pytest.raises(ValidationError):
        validate_edit(game, {"summary": "Bad", "operations": [op]}, ("@Table",))


KITCHEN = "res://kitchen.tscn"


def kitchen() -> dict[str, str]:
    """The editor's context for a kitchen with a Table instanced from table.tscn
    (its Top a node inside the instance) and a plain Shelf of the scene's own."""
    rows = [
        {"id": f"{KITCHEN}::.", "name": "Kitchen", "type": "Node3D"},
        {"id": f"{KITCHEN}::Room", "name": "Room", "type": "Node3D"},
        {
            "id": f"{KITCHEN}::Room/Table",
            "name": "Table",
            "type": "Node3D",
            "source": "res://art/table.tscn",
        },
        {
            "id": f"{KITCHEN}::Room/Table/Top",
            "name": "Top",
            "type": "MeshInstance3D",
            "source": "res://art/table.tscn",
        },
        {"id": f"{KITCHEN}::Shelf", "name": "Shelf", "type": "MeshInstance3D"},
    ]
    return {"nodes": json.dumps(rows)}


def place_on(game: Path, on: str, **extra: object) -> NodeOperation:
    context = kitchen()
    known = tuple(row["id"] for row in json.loads(context["nodes"]))
    edit = validate_edit(
        game,
        {"summary": "Mug on it", "operations": [{**MUG, "on": on, **extra}]},
        known,
        context,
    )
    (op,) = edit.operations
    assert isinstance(op, NodeOperation)
    return op


@pytest.mark.parametrize("as_child", [False, True])
@pytest.mark.parametrize("on", ["Room/Table", "Room/Table/Top"])
def test_on_an_instanced_scene_goes_beside_the_instance(
    game: Path, on: str, as_child: bool
) -> None:
    op = place_on(game, f"{KITCHEN}::{on}", as_child=as_child, at=[0.1, 0])
    # Never a child inside the instance: a sibling under the instance's parent,
    # still standing on the node it was put on.
    assert op.parent == f"{KITCHEN}::Room"
    assert not op.as_child
    assert op.on == f"{KITCHEN}::{on}" and op.at == (0.1, 0)


def test_on_a_top_level_instance_goes_under_the_scene_root(game: Path) -> None:
    context = kitchen()
    rows = json.loads(context["nodes"])
    rows.append(
        {"id": f"{KITCHEN}::Stool", "name": "Stool", "type": "Node3D", "source": "x"}
    )
    context["nodes"] = json.dumps(rows)
    edit = validate_edit(
        game,
        {"summary": "Mug", "operations": [{**MUG, "on": f"{KITCHEN}::Stool"}]},
        tuple(row["id"] for row in rows),
        context,
    )
    op = edit.operations[0]
    assert isinstance(op, NodeOperation) and op.parent == f"{KITCHEN}::."


def test_on_a_plain_node_keeps_its_placement(game: Path) -> None:
    op = place_on(game, f"{KITCHEN}::Shelf", as_child=True)
    assert op.parent == "" and op.as_child
    assert place_on(game, f"{KITCHEN}::Shelf").parent == ""


def test_an_explicit_parent_is_kept_on_an_instance(game: Path) -> None:
    op = place_on(game, f"{KITCHEN}::Room/Table", parent=f"{KITCHEN}::Shelf")
    assert op.parent == f"{KITCHEN}::Shelf"


@pytest.mark.parametrize(
    ("operation", "name", "message"),
    [
        ({**MUG}, "Mug#2", "Node names can't contain '#'; try 'Mug2'."),
        ({"operation": "create_node"}, "Mug.v2", "can't contain '.'; try 'Mugv2'"),
        ({"operation": "create_box"}, "a:b@c", "can't contain ':', '@'; try 'abc'"),
        ({"operation": "create_node"}, 'Say"Hi"', "can't contain '\"'; try 'SayHi'"),
        ({"operation": "create_node"}, "50%", "can't contain '%'; try '_50'"),
        ({"operation": "create_node"}, "Shelf/Mug", "can't contain '/'"),
        ({"operation": "create_node"}, "Mug 2", "letters, digits and underscores"),
        ({"operation": "create_box"}, "", "Give the node a name"),
    ],
)
def test_node_names_godot_forbids_are_refused_with_a_name_to_try(
    game: Path, operation: dict, name: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        validate_edit(
            game,
            {"summary": "Name", "operations": [{**operation, "name": name}]},
            (),
        )


def test_rename_refuses_a_forbidden_name_and_keeps_good_ones(game: Path) -> None:
    rename = {"operation": "rename_node", "target": "@Mug"}
    with pytest.raises(ValidationError, match="can't contain '#'; try 'Mug2'"):
        validate_edit(
            game,
            {"summary": "R", "operations": [{**rename, "name": "Mug#2"}]},
            ("@Mug",),
        )
    edit = validate_edit(
        game, {"summary": "R", "operations": [{**rename, "name": "Mug_2"}]}, ("@Mug",)
    )
    op = edit.operations[0]
    assert isinstance(op, NodeOperation) and op.name == "Mug_2"
    # No name lets Godot name the new node after its scene.
    assert validate_edit(game, {"summary": "M", "operations": [MUG]}, ()).operations


def test_suggested_names_always_pass() -> None:
    for name in ["#", "9lives", "Mug#2", "x" * 80 + "#", "ÄÖ"]:
        suggestion = suggest_node_name(name)
        assert node_name_problem(suggestion) == "", (name, suggestion)
