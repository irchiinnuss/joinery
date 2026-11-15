"""Catch a saved-scene tool that drops what it could not load.

The Godot MCP edits a scene by loading it in a headless Godot and saving it back.
A script that does not compile there (it names the game's autoloads or classes,
which a bare ``--script`` run doesn't have) loads as nothing, and the save then
writes the node without its script. Instanced scenes go the same way. The node
is still there, so nothing looks wrong until the game runs.

:func:`dropped` compares the scene before and after such a call and names every
node that kept its place but lost its script or its instance.
"""

from __future__ import annotations

from joinery.scene_text import Scene

DROPPED = (
    "Godot's saved-scene tool could not load {what} in {scene}, so saving the scene "
    "dropped {it}. The scene was put back as it was. Make this edit as a text patch "
    "with write_file (patch), adding [node] sections and keeping the scene's "
    "ext_resources as they are."
)


def _links(text: str) -> dict[str, tuple[str, str]]:
    scene = Scene(text)
    return {node.path: (scene.script_of(node), node.instance) for node in scene.nodes()}


def dropped(before: str, after: str) -> list[str]:
    """Node paths (with what they lost) that kept their place but lost a link."""
    old, new = _links(before), _links(after)
    lost: list[str] = []
    for path, (script, instance) in old.items():
        if path not in new:
            continue
        now_script, now_instance = new[path]
        if script and not now_script:
            lost.append(f"{path} (script {script})")
        if instance and not now_instance:
            lost.append(f"{path} (instance of {instance})")
    return lost


def message(scene: str, lost: list[str]) -> str:
    shown = ", ".join(lost[:5]) + (
        f" and {len(lost) - 5} more" if len(lost) > 5 else ""
    )
    return DROPPED.format(
        what=shown,
        scene="res://" + scene.removeprefix("res://"),
        it="it" if len(lost) == 1 else "them",
    )
