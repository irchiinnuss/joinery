"""Reproducible server/runtime setup and versioned addon delivery."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from joinery import (
    docker_cli,
    metrics,
    played,
    readiness,
    references,
    team,
    tool_refresh,
)
from joinery.catalog import ROOT, load_catalog, load_project_tools, selection_ids
from joinery.platform_support import bash_script_argv, editor_running
from joinery.server_install import install
from joinery.transactions import FileTransaction, atomic_json
from joinery.wiring import _args, _command_str, _opencode_plugin, runs_gate_for

#: Each worker built for a tool that has one, after the shared images.
TOOL_WORKERS = {"blender": "blender", "inkscape": "inkscape", "audacity": "audacity"}
RUNTIME = "joinery-sandbox-runtime"


def _labels(tag: str, revision: str) -> list[str]:
    """The image's own revision (what readiness compares), and the whole sandbox's."""
    return [
        "--label",
        f"{readiness.SETUP_LABEL}={revision}",
        "--label",
        f"{readiness.IMAGE_LABEL}={readiness.image_revision(tag)}",
    ]


def _current(tag: str, revision: str) -> bool:
    """True when the image is there and was built from today's sources.

    An image from before per-image revisions counts only when the whole
    sandbox is unchanged since it was built.
    """
    try:
        done = docker_cli.run(
            ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", tag],
            capture_output=True,
            text=True,
        )
    except docker_cli.DockerNotResponding as exc:
        raise ValueError(str(exc)) from None
    if done.returncode:
        return False
    try:
        labels = json.loads(str(getattr(done, "stdout", "")) or "null") or {}
    except ValueError:
        return False
    if labels.get(readiness.IMAGE_LABEL):
        return bool(labels[readiness.IMAGE_LABEL] == readiness.image_revision(tag))
    return bool(labels.get(readiness.SETUP_LABEL) == revision)


def _outdated(wanted: list[str], revision: str) -> list[str]:
    """The images to build, in order: changed, missing, or built on a rebuilt parent."""
    rebuilt: list[str] = []
    for tag in wanted:
        parent = readiness.WORKER_IMAGES[tag][1]
        if parent in rebuilt or not _current(tag, revision):
            rebuilt.append(tag)
    return rebuilt


def _docker_build(
    tag: str, dockerfile: str, revision: str, progress: tool_refresh.Progress
) -> None:
    """Build one worker image, stamped with the revisions it came from."""
    progress.begin(tag)
    tool_refresh.run(
        [
            "docker",
            "build",
            "--progress=plain",
            "--platform",
            "linux/amd64",
            *_labels(tag, revision),
            "-t",
            tag,
            "-f",
            "sandbox/" + dockerfile,
            "sandbox",
        ],
        progress,
        cwd=ROOT,
        timeout=3600,
        what=tool_refresh.IMAGE_WORDS.get(tag, tag),
    )


def _build_runtime(revision: str, progress: tool_refresh.Progress) -> None:
    """The shared base every worker starts from (sandbox/build.sh), then its label.

    The label is a one-line build on top of the image: no new layers, nothing
    downloaded, and the previous image is left as it is (nothing is pruned).
    """
    progress.begin(RUNTIME)
    words = tool_refresh.IMAGE_WORDS[RUNTIME]
    tool_refresh.run(
        bash_script_argv([str(ROOT / "sandbox/build.sh"), "--all", "--tag", RUNTIME]),
        progress,
        cwd=ROOT,
        timeout=3600,
        what=words,
    )
    tool_refresh.run(
        [
            "docker",
            "build",
            "--progress=plain",
            "--platform",
            "linux/amd64",
            *_labels(RUNTIME, revision),
            "-t",
            RUNTIME,
            "-",
        ],
        progress,
        cwd=ROOT,
        timeout=600,
        input_text=f"FROM {RUNTIME}\n",
        what=words,
    )


def setup(project: Path, emit: tool_refresh.Emit | None = None) -> dict[str, Any]:
    """Build the offline workers and install the pinned servers for a project.

    While it builds, `~/.cache/joinery/setup.json` names the image in progress,
    so every readiness screen says "Refreshing…" instead of "Not available",
    and `refresh.json` and `refresh.log` say how far it is (joinery.tool_refresh;
    `emit` gets each change). Only the images whose sources changed (or whose
    parent was rebuilt) are built again, each labelled with its own revision,
    and a readiness refresh starts once the build is done. No image or build
    cache is ever removed. A failure raises RefreshFailed with a readable reason.

    Only one refresh runs at a time: while another holds the slot this raises
    RefreshBusy, and the caller follows that one (`tool_refresh.join`).
    """
    if not tool_refresh.acquire():
        raise tool_refresh.RefreshBusy()
    try:
        return _setup(project, emit)
    finally:
        tool_refresh.release()


def _setup(project: Path, emit: tool_refresh.Emit | None) -> dict[str, Any]:
    selected = selection_ids(
        load_catalog(), load_project_tools(project / "joinery.toml")
    )
    if shutil.which("docker") is None:
        raise ValueError(
            "Joinery's apps run offline in Docker. Install Docker Desktop and "
            "start it, then try again."
        )
    try:
        result = docker_cli.run(["docker", "info"], capture_output=True)
    except docker_cli.DockerNotResponding as exc:
        raise ValueError(str(exc)) from None
    if result.returncode:
        raise ValueError("Docker isn't running. Start Docker Desktop, then try again.")
    revision = readiness.sandbox_revision()
    os.environ["JOINERY_SANDBOX_REV"] = revision
    try:
        image = docker_cli.run(
            ["docker", "image", "inspect", RUNTIME], capture_output=True
        )
    except docker_cli.DockerNotResponding as exc:
        raise ValueError(str(exc)) from None
    first = image.returncode != 0
    workers = [
        "joinery-" + TOOL_WORKERS[tool] + "-worker"
        for tool in selected
        if tool in TOOL_WORKERS and load_catalog().get(tool).mcp is not None
    ]
    shared = [RUNTIME, "joinery-mcp-worker", "joinery-creative-worker"]
    build = _outdated([*shared, *workers], revision)
    progress = tool_refresh.Progress(len(build), emit, first=first)
    ready = []
    unavailable = []
    try:
        if RUNTIME in build:
            readiness.mark_setup("build", RUNTIME)
            _build_runtime(revision, progress)
        for tag in shared[1:]:
            if tag in build:
                readiness.mark_setup("build", tag)
                _docker_build(tag, readiness.WORKER_IMAGES[tag][0], revision, progress)
        for tool in selected:
            entry = load_catalog().get(tool)
            if entry.mcp is None:
                continue
            try:
                install(tool)
                tag = "joinery-" + TOOL_WORKERS.get(tool, "") + "-worker"
                if tag in build:
                    readiness.mark_setup("build", tag)
                    dockerfile = readiness.WORKER_IMAGES[tag][0]
                    _docker_build(tag, dockerfile, revision, progress)
                ready.append(tool)
            except tool_refresh.RefreshFailed:
                raise
            except (
                OSError,
                ValueError,
                RuntimeError,
                subprocess.SubprocessError,
            ) as exc:
                unavailable.append({"tool": tool, "reason": str(exc)})
        if unavailable:
            # Every image may be built, but an app that couldn't be set up still
            # can't work: the refresh failed, and it says which app and why.
            raise tool_refresh.RefreshFailed(_failed_apps(unavailable))
    except tool_refresh.RefreshCancelled:
        progress.finish("cancelled")
        raise
    except BaseException as exc:
        progress.finish("failed", error=str(exc))
        raise
    finally:
        readiness.clear_setup()
        # The last answer describes the tools before this refresh: never show it again.
        readiness.expire_snapshot()
        # Check the images now, in the background, so screens update by themselves.
        refreshing = readiness.spawn_refresh(project, full=True, catalog=True)
    progress.finish("done")
    return {
        "installed": ready,
        "unavailable": unavailable,
        "revision": revision,
        "built": build,
        "refreshing": refreshing,
        "log": str(tool_refresh.log_path()),
        "note": "Run joinery mcp status --probe for actual connected operation readiness.",
    }


def _failed_apps(unavailable: list[dict[str, str]]) -> str:
    """ "Couldn't set up Godot and Krita: <the first reason>"."""
    catalog = load_catalog()
    apps = [catalog.get(item["tool"]).name for item in unavailable]
    reason = next((item["reason"] for item in unavailable if item["reason"]), "")
    words = f"Couldn't set up {readiness.names(apps)}"
    return f"{words}: {reason}" if reason else words + "."


#: Files Godot writes next to the add-on's own in a project; never part of its version.
_GENERATED = {".uid", ".import"}


def addon_version(source: Path | None = None) -> str:
    source = source or ROOT / "godot_addon/joinery"
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if (
            path.is_file()
            and path.suffix not in _GENERATED
            and path.name != ".DS_Store"
        ):
            digest.update(
                path.relative_to(source).as_posix().encode() + b"\0" + path.read_bytes()
            )
    return digest.hexdigest()


def update_addon(project: Path) -> dict[str, str]:
    """Call with the project editor closed; stage first, keep a local backup."""
    if not (project / "project.godot").is_file():
        raise ValueError("Expected a Godot project.")
    target = project / "addons/joinery"
    # Without the addon no editor can be running it (a brand-new project), so skip
    # the process check, which is slow on Windows.
    if target.is_dir() and editor_running(project):
        raise ValueError(
            "Close this project's editor before updating its addon; this preserves unsaved work and avoids script hot-reload during a live session."
        )
    version = addon_version()
    uids_present = all(
        (target / uid.name).is_file()
        for uid in (ROOT / "godot_addon/joinery").glob("*.gd.uid")
    )
    if target.is_dir() and addon_version(target) == version and uids_present:
        before = _gate_texts(project)
        refresh_managed_gates(project)
        plugin = enable_plugin(project)
        current = {"version": version, "state": "current"}
        current.update(_commit_addon(project, before, plugin))
        return current
    backups = project / ".joinery/addon-backups"
    backups.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=".addon-", dir=target.parent if target.parent.is_dir() else project
        )
    )
    backup = backups / (
        str(time.time_ns())
        + "-"
        + (addon_version(target)[:12] if target.is_dir() else "new")
    )
    try:
        shutil.copytree(
            ROOT / "godot_addon/joinery",
            staging,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".DS_Store"),
        )
        target.parent.mkdir(exist_ok=True)
        if target.exists():
            target.rename(backup)
        try:
            staging.rename(target)
        except BaseException:
            if backup.exists():
                backup.rename(target)
            raise
        (project / ".joinery/addon-version.json").write_text(
            json.dumps({"version": version}) + "\n"
        )
        before = _gate_texts(project)
        refresh_managed_gates(project)
        plugin = enable_plugin(project)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    done = {"version": version, "state": "updated", "backup": str(backup)}
    done.update(_commit_addon(project, before, plugin))
    if backup.exists():
        # An existing game whose add-on was just refreshed: its Joinery-owned
        # game files get the same update, as one change the user reviews.
        done.update(_offer_core(project))
    return done


#: The agents' settings files whose Joinery gate an add-on update refreshes.
_GATE_FILES = (".claude/settings.json", ".codex/hooks.json", ".gemini/settings.json")
#: The Timeline entry an add-on update makes.
ADDON_COMMIT = "Update Joinery add-on to {version}"


def _gate_texts(project: Path) -> dict[str, bytes]:
    return {
        name: (project / name).read_bytes()
        for name in _GATE_FILES
        if (project / name).is_file()
    }


def _commit_addon(
    project: Path, gates_before: dict[str, bytes], plugin_enabled: bool
) -> dict[str, str]:
    """Put the add-on update in the Timeline as one entry, so the game's repository
    is never left with loose add-on files. Only the add-on's own files, the gates
    it refreshed and project.godot when it turned the plug-in on: anything else the
    user changed stays as it was. Nothing when the project keeps no timeline or
    switched automatic entries off, or when nothing changed."""
    from joinery import __version__, timeline

    try:
        if timeline.state(project)["state"] != "ready" or not timeline.auto_commit(
            project
        ):
            return {}
        target = project / "addons/joinery"
        paths = {
            path.relative_to(project).as_posix()
            for path in target.rglob("*")
            if path.is_file()
        }
        # Files an older add-on had that this one doesn't: their deletion too.
        paths.update(timeline.tracked_under(project, "addons/joinery"))
        paths.update(
            name
            for name in _GATE_FILES
            if (project / name).is_file()
            and gates_before.get(name) != (project / name).read_bytes()
        )
        if plugin_enabled:
            paths.add("project.godot")
        commit = timeline.commit_paths(
            project, sorted(paths), ADDON_COMMIT.format(version=__version__)
        )
    except (OSError, ValueError) as error:
        return {"timeline_error": str(error) or type(error).__name__}
    return {"commit": commit} if commit else {}


def _offer_core(project: Path) -> dict[str, str]:
    """`core_update.offer` in the add-on update's string-valued outcome."""
    from joinery import core_update

    offered = core_update.offer(project)
    out = {"core": str(offered["state"])}
    if offered.get("transaction_id"):
        out["core_transaction"] = str(offered["transaction_id"])
    if offered.get("edited"):
        out["core_kept"] = ", ".join(offered["edited"])
    if offered.get("message"):
        out["core_error"] = str(offered["message"])
    return out


PLUGIN = "res://addons/joinery/plugin.cfg"
_ENABLED = re.compile(r"^enabled\s*=\s*PackedStringArray\((.*?)\)\s*$", re.MULTILINE)


def enable_plugin(project: Path) -> bool:
    """Turn the Joinery addon on in project.godot, keeping other plugins.

    The Project Manager does this when it creates a project; projects created
    from the command line would otherwise open without the sidebar. Returns
    True when the file changed.
    """
    path = project / "project.godot"
    text = path.read_text()
    start = text.find("[editor_plugins]")
    if start == -1:
        path.write_text(
            text.rstrip("\n")
            + f'\n\n[editor_plugins]\n\nenabled=PackedStringArray("{PLUGIN}")\n'
        )
        return True
    end = text.find("\n[", start + 1)
    end = len(text) if end == -1 else end
    section = text[start:end]
    match = _ENABLED.search(section)
    if match is None:
        section = section.rstrip("\n") + f'\nenabled=PackedStringArray("{PLUGIN}")\n'
    else:
        entries = [e.strip() for e in match.group(1).split(",") if e.strip()]
        if f'"{PLUGIN}"' in entries:
            return False
        joined = ", ".join([*entries, f'"{PLUGIN}"'])
        section = (
            section[: match.start()]
            + f"enabled=PackedStringArray({joined})"
            + section[match.end() :]
        )
    path.write_text(text[:start] + section + text[end:])
    return True


def refresh_managed_gates(project: Path) -> None:
    """Migrate only Joinery-owned commands, preserving unrelated user settings."""
    for agent, filename, event in [
        ("claude", ".claude/settings.json", "PreToolUse"),
        ("codex", ".codex/hooks.json", "PreToolUse"),
        ("gemini", ".gemini/settings.json", "BeforeTool"),
    ]:
        path = project / filename
        if not path.is_file():
            continue
        data = json.loads(path.read_text())
        changed = False
        for group in data.get("hooks", {}).get(event, []):
            for hook in group.get("hooks", []):
                command = str(hook.get("command", ""))
                arguments = hook.get("args", [])
                legacy = command == "uv" and arguments == _args(str(ROOT), agent)
                owned = runs_gate_for(command, agent)
                if legacy or owned:
                    expected = _command_str(str(ROOT), agent)
                    if command != expected or "args" in hook or "onFailure" in hook:
                        hook["command"] = expected
                        hook.pop("args", None)
                        hook.pop("onFailure", None)
                        changed = True
        if changed:
            backup = (
                project
                / ".joinery/config-backups"
                / (str(time.time_ns()) + "-" + agent + ".json")
            )
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            atomic_json(path, data)
    plugin = project / ".opencode/plugin/joinery-gate.js"
    if plugin.is_file() and plugin.read_text().startswith("// Generated by Joinery."):
        plugin.write_text(_opencode_plugin(str(ROOT)))


#: Changed paths listed per transaction; the count says how many there are in all.
LISTED_FILES = 20


def transactions(
    project: Path, action: str, identifier: str = ""
) -> list[dict[str, Any]]:
    paths = (
        [project / ".joinery/transactions" / identifier / "journal.json"]
        if identifier
        else sorted((project / ".joinery/transactions").glob("*/journal.json"))
    )
    result: list[dict[str, Any]] = []
    waiting = set(played.summary(project)["ids"])
    for path in paths:
        tx = FileTransaction.load(project, path.parent.name)
        warning = ""
        if action == "recover":
            tx.recover()
            if tx.state in {"staging", "review"}:
                tx.reject()
        elif action == "revert":
            tx.revert()
            warning = references.orphan_notice(
                references.users_of(project, references.created(tx.changes)),
                references.REVERTED,
            )
        # A pruned change keeps its journal but can no longer be reverted.
        result.append(
            {
                "id": tx.id,
                "state": tx.state,
                "summary": tx.summary,
                "revertible": tx.revertible,
                # For the editor's Review tab: when, which apps, and what changed.
                "created": tx.created,
                "decided": tx.decided,
                "tools": tx.tools,
                # The reviewer agent's notes and how its review went.
                "review_notes": tx.review_notes,
                "reviewer": tx.reviewer,
                # Kept without the user's Accept (joinery.auto_accept).
                "auto_accepted": tx.auto_accepted,
                # What it said beside "Accepted automatically" (auto_accept.shown_notes).
                "auto_notes": tx.auto_notes,
                # What the agent guessed and where it departed from the request.
                "guessed": tx.guessed,
                "not_as_asked": tx.not_as_asked,
                # Accepted, changes the game, and nobody has played it since.
                "unplayed": tx.state == "accepted" and tx.id in waiting,
                # What the change cost to make, when it was measured (joinery.metrics).
                "cost": metrics.cost(project, tx.id),
                "file_count": len(tx.changes),
                "files": [
                    {"path": change["path"], "change": _change_kind(change)}
                    for change in tx.changes[:LISTED_FILES]
                ],
                **({"warning": warning} if warning else {}),
                **team.conflict_info(tx),
            }
        )
    return result


def transaction_lines(entry: dict[str, Any]) -> list[str]:
    """One transaction from :func:`transactions`, as plain lines for a person."""
    lines = [f"{entry['summary'] or 'Project changes'}  ({entry['state']})"]
    lines += [f"  Guessed: {line}" for line in entry.get("guessed") or []]
    lines += [f"  Not as asked: {line}" for line in entry.get("not_as_asked") or []]
    if entry.get("unplayed"):
        lines.append(f"  {played.CHANGE_LINE}")
    if entry.get("auto_accepted"):
        lines.append("  Accepted automatically")
    lines += [
        f"  {item['change']:<9} {item['path']}" for item in entry.get("files") or []
    ]
    more = int(entry.get("file_count", 0)) - len(entry.get("files") or [])
    if more > 0:
        lines.append(f"  and {more} more")
    return lines


def _change_kind(change: dict[str, str]) -> str:
    if not change.get("before"):
        return "created"
    if not change.get("after"):
        return "deleted"
    return "modified"
