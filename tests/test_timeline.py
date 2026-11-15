"""The Timeline: accepted changes become git commits, and only theirs."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path, PureWindowsPath

import pytest

from joinery import cli, timeline
from joinery.hook import violation
from joinery.policy import Severity
from joinery.transactions import FileTransaction

pytestmark = pytest.mark.skipif(
    timeline.git_executable() is None, reason="git is not installed"
)


@pytest.fixture(autouse=True)
def _isolated_git(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No user or system git config: identity and hooks come only from the test."""
    home = tmp_path_factory.mktemp("git-home")
    config = home / "gitconfig"
    config.write_text("[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))


def git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True, check=True
    ).stdout


def make_project(path: Path, name: str = "Harbour") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "project.godot").write_text(
        f'config_version=5\n[application]\nconfig/name="{name}"\n'
    )
    (path / "main.gd").write_text("extends Node\n")
    return path


def started(path: Path) -> Path:
    project = make_project(path)
    git(project, "init", "-q")
    git(project, "config", "user.name", "Ada Lovelace")
    git(project, "config", "user.email", "ada@example.com")
    timeline.start(project)
    return project


def accept(
    project: Path, summary: str, edits: dict[str, str | None]
) -> FileTransaction:
    tx = FileTransaction(project)
    tx.begin(summary)
    for path, text in edits.items():
        target = tx.stage / path
        if text is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
    tx.review()
    tx.accept()
    return tx


def files_of(project: Path, commit: str = "HEAD") -> set[str]:
    return set(
        git(project, "show", "--format=", "--name-only", "--no-renames", commit).split()
    )


# --- Starting a timeline ------------------------------------------------------------


def test_start_makes_one_entry_and_keeps_restore_data_out(tmp_path: Path) -> None:
    project = started(tmp_path / "game")
    assert git(project, "log", "--format=%s").splitlines() == [timeline.STARTED_MESSAGE]
    ignore = (project / ".gitignore").read_text()
    for rule in timeline.IGNORE_RULES:
        assert rule in ignore
    (project / ".joinery/transactions/x").mkdir(parents=True)
    (project / ".joinery/transactions/x/journal.json").write_text("{}")
    (project / ".joinery/game-manifest.json").write_text("{}")
    status = git(project, "status", "--porcelain", "--untracked-files=all")
    assert ".joinery/transactions" not in status
    assert ".joinery/game-manifest.json" in status
    with pytest.raises(timeline.TimelineError, match="already"):
        timeline.start(project)


def test_new_project_starts_with_its_name(tmp_path: Path) -> None:
    path = tmp_path / "Lighthouse"
    assert cli.main(["new", str(path), "--name", "Lighthouse", "--tools", "godot"]) == 0
    assert git(path, "log", "--format=%s").splitlines() == ["Start Lighthouse"]
    # Everything creation wrote is in that first entry; nothing is left over.
    assert git(path, "status", "--porcelain") == ""
    assert "project.godot" in files_of(path)
    # The author is the local fallback: this test has no git identity.
    assert git(path, "log", "-1", "--format=%an <%ae>").strip() == (
        f"{timeline.FALLBACK_NAME} <{timeline.FALLBACK_EMAIL}>"
    )


def test_creation_steps_fold_into_the_first_entry(tmp_path: Path) -> None:
    project = make_project(tmp_path / "game")
    assert timeline.begin_project(project, fresh=True)["started"] == "yes"
    (project / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    timeline.begin_project(project, fresh=False)
    assert git(project, "log", "--format=%s").splitlines() == ["Start Harbour"]
    assert "joinery.toml" in files_of(project)
    # The first accepted change ends the folding: later steps never rewrite it.
    accept(project, "Add a crate", {"crate.gd": "extends Node3D\n"})
    (project / "notes.txt").write_text("mine\n")
    timeline.begin_project(project, fresh=False)
    assert len(git(project, "log", "--format=%s").splitlines()) == 2
    assert "notes.txt" in git(project, "status", "--porcelain")


def test_existing_project_is_never_put_under_git_unasked(tmp_path: Path) -> None:
    project = make_project(tmp_path / "game")
    (project / "level.tscn").write_text("[gd_scene format=3]\n")
    assert not timeline.looks_new(project)
    assert (
        timeline.begin_project(project, fresh=timeline.looks_new(project))["state"]
        == "none"
    )
    accept(project, "Add a crate", {"crate.gd": "extends Node3D\n"})
    assert not (project / ".git").exists()


def test_a_project_inside_another_repository_is_left_alone(tmp_path: Path) -> None:
    outer = tmp_path / "monorepo"
    outer.mkdir()
    git(outer, "init", "-q")
    project = make_project(outer / "game")
    assert timeline.state(project)["state"] == "nested"
    tx = accept(project, "Add a crate", {"crate.gd": "extends Node3D\n"})
    assert tx.timeline["state"] == "nested"
    assert git(outer, "rev-list", "--all") == ""
    with pytest.raises(timeline.TimelineError, match="part of the git repository"):
        timeline.start(project)


def test_no_git_says_so_and_never_blocks_a_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path / "game")
    monkeypatch.setattr(timeline, "git_executable", lambda: None)
    assert timeline.state(project) == {"state": "no-git", "message": timeline.NO_GIT}
    tx = accept(project, "Add a crate", {"crate.gd": "extends Node3D\n"})
    assert tx.state == "accepted" and tx.timeline["state"] == "no-git"
    with pytest.raises(timeline.TimelineError, match="Git isn't installed"):
        timeline.start(project)


# --- Accepted and reverted changes ------------------------------------------------------


def test_accept_commits_only_the_files_of_that_change(tmp_path: Path) -> None:
    project = started(tmp_path / "game")
    (project / "door.gd").write_text("extends Node\n")
    (project / "keep.gd").write_text("extends Node\n")
    timeline.commit_paths(project, ["door.gd", "keep.gd"], "Add door and keep")
    # The user's own work in progress: an edit, a new file and a staged one.
    (project / "main.gd").write_text("extends Node\n# mine\n")
    (project / "sketch.txt").write_text("idea\n")
    (project / "staged.txt").write_text("staged\n")
    git(project, "add", "staged.txt")
    tx = accept(
        project,
        "Widen the door",
        {
            "door.gd": "extends Node\nvar width := 2.0\n",
            "hinge.gd": "extends Node\n",
            "keep.gd": None,
        },
    )
    assert tx.timeline["state"] == "committed"
    assert files_of(project) == {"door.gd", "hinge.gd", "keep.gd"}
    message = git(project, "log", "-1", "--format=%B").strip()
    assert message == "Widen the door\n\nAccepted in Joinery"
    status = git(project, "status", "--porcelain")
    assert (
        " M main.gd" in status
        and "?? sketch.txt" in status
        and "A  staged.txt" in status
    )


def test_revert_becomes_its_own_entry(tmp_path: Path) -> None:
    project = started(tmp_path / "game")
    tx = accept(project, "Add a crate", {"crate.gd": "extends Node3D\n"})
    tx.revert()
    assert tx.timeline["state"] == "committed"
    subjects = git(project, "log", "--format=%s").splitlines()
    assert subjects[:2] == ["Revert: Add a crate", "Add a crate"]
    assert git(project, "log", "-1", "--format=%b").strip() == timeline.REVERTED_LINE
    assert "crate.gd" in files_of(project)
    assert not (project / "crate.gd").exists()


def test_author_is_the_user_never_an_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = started(tmp_path / "game")
    # An agent's process may carry its own identity; it is never used.
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "Claude")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "noreply@anthropic.com")
    accept(project, "Add a crate", {"crate.gd": "extends Node3D\n"})
    who = git(project, "log", "-1", "--format=%an <%ae>|%cn <%ce>").strip()
    assert who == "Ada Lovelace <ada@example.com>|Ada Lovelace <ada@example.com>"
    git(project, "config", "--unset", "user.name")
    git(project, "config", "--unset", "user.email")
    accept(project, "Add a barrel", {"barrel.gd": "extends Node3D\n"})
    assert git(project, "log", "-1", "--format=%an").strip() == timeline.FALLBACK_NAME


def test_auto_commit_can_be_switched_off(tmp_path: Path) -> None:
    project = started(tmp_path / "game")
    (project / "joinery.toml").write_text('[tools]\nenabled = ["godot"]\n')
    timeline.set_auto_commit(project, False)
    text = (project / "joinery.toml").read_text()
    assert 'enabled = ["godot"]' in text and "auto_commit = false" in text
    tx = accept(project, "Add a crate", {"crate.gd": "extends Node3D\n"})
    assert tx.timeline == {"state": "off"}
    assert len(git(project, "log", "--format=%s").splitlines()) == 1
    timeline.set_auto_commit(project, True)
    assert timeline.auto_commit(project)
    assert (project / "joinery.toml").read_text().count("[timeline]") == 1


def test_a_failed_commit_never_fails_the_change(tmp_path: Path) -> None:
    project = started(tmp_path / "game")
    (project / ".git/index.lock").write_text("")
    tx = accept(project, "Add a crate", {"crate.gd": "extends Node3D\n"})
    assert tx.state == "accepted"
    assert tx.timeline["state"] == "failed"
    assert tx.timeline["message"].startswith("Couldn't add this to the Timeline.")


def test_a_failed_commit_is_owed_to_the_next_accept(tmp_path: Path) -> None:
    project = started(tmp_path / "game")
    (project / ".git/index.lock").write_text("")
    first = accept(project, "Add a crate", {"crate.gd": "extends Node3D\n"})
    assert first.timeline["state"] == "failed"
    assert "Not committed yet: crate.gd" in first.timeline["message"]
    assert timeline.uncommitted(project, ["crate.gd"]) == ["crate.gd"]
    (project / ".git/index.lock").unlink()
    second = accept(project, "Add a barrel", {"barrel.gd": "extends Node3D\n"})
    assert second.timeline["state"] == "committed"
    # The owed change keeps its own entry, ahead of the new one.
    assert git(project, "log", "--format=%s").splitlines()[:2] == [
        "Add a barrel",
        "Add a crate",
    ]
    assert files_of(project, "HEAD~1") == {"crate.gd"}
    assert timeline.uncommitted(project, ["crate.gd", "barrel.gd"]) == []
    assert not (project / timeline.OWED).exists()


def ignoring_core_files(project: Path) -> None:
    """Ignore rules that leave out core-files.json, which changes carry."""
    assert "!.joinery/core-files.json" not in (project / ".gitignore").read_text()


def test_an_ignored_file_the_change_touched_stays_out_of_the_commit(
    tmp_path: Path,
) -> None:
    project = started(tmp_path / "game")
    ignoring_core_files(project)
    tx = accept(
        project,
        "Add a blockout",
        {"level.gd": "extends Node3D\n", ".joinery/core-files.json": "{}\n"},
    )
    # The record is carried into the project, but git ignores it there.
    assert (project / ".joinery/core-files.json").read_text() == "{}\n"
    assert ".joinery/core-files.json" in tx._timeline_paths(tx.changes)
    assert tx.timeline["state"] == "committed"
    assert files_of(project) == {"level.gd"}
    assert git(project, "ls-files", ".joinery/core-files.json") == ""
    assert not (project / timeline.OWED).exists()


def test_owed_paths_with_an_ignored_file_clear_on_the_next_accept(
    tmp_path: Path,
) -> None:
    project = started(tmp_path / "game")
    ignoring_core_files(project)
    # Left by an accept that git refused for the ignored record beside it.
    (project / "level.gd").write_text("extends Node3D\n")
    (project / ".joinery").mkdir(exist_ok=True)
    (project / ".joinery/core-files.json").write_text("{}\n")
    owed = [{"paths": ["level.gd", ".joinery/core-files.json"], "message": "Blockout"}]
    (project / timeline.OWED).write_text(json.dumps(owed))
    tx = accept(project, "Add a crate", {"crate.gd": "extends Node3D\n"})
    assert tx.timeline["state"] == "committed"
    assert git(project, "log", "--format=%s").splitlines()[:2] == [
        "Add a crate",
        "Blockout",
    ]
    assert files_of(project, "HEAD~1") == {"level.gd"}
    assert timeline.uncommitted(project, ["level.gd", "crate.gd"]) == []
    assert not (project / timeline.OWED).exists()


def test_godot_files_beside_a_change_are_committed_with_it(tmp_path: Path) -> None:
    project = started(tmp_path / "game")
    # Godot wrote an id for a script already in the timeline (an open editor).
    (project / "main.gd.uid").write_text("uid://main\n")
    # The tools' import of the new texture, written beside it before the accept.
    tx = FileTransaction(project)
    tx.begin("Add a crate")
    (tx.stage / "crate.gd").write_text("extends Node3D\n")
    (tx.stage / "crate.png").write_bytes(b"png")
    tx.review()
    tx.accept()
    (project / "crate.png.import").write_text("[remap]\n")
    assert tx.timeline["state"] == "committed"
    # Godot imports after the commit: its .uid joins the same entry.
    (project / "crate.gd.uid").write_text("uid://crate\n")
    tx.commit_generated()
    assert tx.timeline["state"] == "committed"
    assert len(git(project, "log", "--format=%s").splitlines()) == 2
    assert files_of(project) == {
        "crate.gd",
        "crate.gd.uid",
        "crate.png",
        "crate.png.import",
        "main.gd.uid",
    }
    assert tx.timeline["commit"] == git(project, "rev-parse", "HEAD").strip()
    assert git(project, "status", "--porcelain") == ""


def test_godot_files_after_a_newer_entry_get_their_own(tmp_path: Path) -> None:
    project = started(tmp_path / "game")
    tx = accept(project, "Add a crate", {"crate.gd": "extends Node3D\n"})
    accept(project, "Add a barrel", {"barrel.gd": "extends Node3D\n"})
    (project / "crate.gd.uid").write_text("uid://crate\n")
    tx.commit_generated()
    assert git(project, "log", "--format=%s").splitlines()[:3] == [
        "Add a crate",
        "Add a barrel",
        "Add a crate",
    ]
    assert files_of(project) == {"crate.gd.uid"}


def test_an_entry_is_named_by_the_change_and_keeps_the_request(
    tmp_path: Path,
) -> None:
    from joinery.transactions import change_title

    project = started(tmp_path / "game")
    tx = FileTransaction(project)
    tx.request = "The tools are ready now. Go ahead and make the casting hall blockout"
    tx.begin(tx.request[:160])
    (tx.stage / "hall.toml").write_text("[rooms]\n")
    (tx.stage / "hall.tscn").write_text("[gd_scene format=3]\n")
    tx.summary = change_title(tx.diff())
    tx.review()
    assert FileTransaction.load(project, tx.id).request == tx.request
    tx.accept()
    message = git(project, "log", "-1", "--format=%B").strip()
    assert message == (
        "Add hall.toml and hall.tscn\n\nRequest: The tools are ready now. Go ahead "
        "and make the casting hall blockout\n\nAccepted in Joinery"
    )
    # A request the summary already says isn't repeated.
    assert timeline.message_for("Add a crate", request="Add a crate") == (
        "Add a crate\n\nAccepted in Joinery"
    )
    many = [
        {"path": f"art/{n}.png", "change": "modified", "before": "a", "after": "b"}
        for n in "abcde"
    ]
    assert change_title(many) == "Change a.png, b.png and 3 more files"
    uid = {"path": "a.gd.uid", "change": "created", "before": "", "after": "b"}
    assert change_title([many[0], uid]) == "Change a.png"


def test_a_resumed_chat_digest_lists_new_timeline_entries(tmp_path: Path) -> None:
    from joinery import chats

    project = started(tmp_path / "game")
    time.sleep(1.1)  # Git times are whole seconds.
    before = time.time()
    time.sleep(1.0)
    accept(project, "Add a crate", {"crate.gd": "extends Node3D\n"})
    (project / "door.gd").write_text("extends Node\n")
    git(project, "add", "door.gd")
    git(
        project,
        "-c",
        "user.name=Grace Hopper",
        "-c",
        "user.email=grace@example.com",
        "commit",
        "-q",
        "-m",
        "Add a door",
    )
    assert [e["subject"] for e in timeline.since(project, before)] == [
        "Add a door",
        "Add a crate",
    ]
    digest = chats.since_digest(project, before)
    assert "(Add a crate): accepted by the user." in digest
    assert '- "Add a door" by Grace Hopper, 1 files.' in digest
    # The accepted change isn't listed again as an entry, nor its files as edits.
    assert '"Add a crate"' not in digest and "Files changed" not in digest
    assert chats.since_digest(project, time.time() + 5) == ""


def test_messages_keep_a_short_subject() -> None:
    long = "Make " + "the lamp brighter " * 10
    message = timeline.message_for(long)
    subject, _, rest = message.partition("\n\n")
    assert len(subject) <= 100 and subject.endswith("…")
    assert long.strip() in rest
    assert message.endswith(timeline.ACCEPTED_LINE)


# --- Reading the timeline ----------------------------------------------------------------


def test_list_pages_fifty_at_a_time_and_filters(tmp_path: Path) -> None:
    project = started(tmp_path / "game")
    for index in range(60):
        name = f"prop_{index:02d}.gd"
        (project / name).write_text(f"# {index}\n")
        timeline.commit_paths(project, [name], f"Add prop {index}")
    first = timeline.entries(project)
    assert len(first["entries"]) == timeline.PAGE and first["more"] is True
    assert first["entries"][0]["subject"] == "Add prop 59"
    assert first["entries"][0]["files"] == 1
    assert first["entries"][0]["author"] == "Ada Lovelace"
    second = timeline.entries(project, skip=timeline.PAGE)
    assert len(second["entries"]) == 11 and second["more"] is False
    assert second["entries"][-1]["subject"] == timeline.STARTED_MESSAGE
    by_file = timeline.entries(project, text="PROP_07.gd")
    assert [entry["subject"] for entry in by_file["entries"]] == ["Add prop 7"]
    by_author = timeline.entries(project, text="lovelace")
    assert len(by_author["entries"]) == timeline.PAGE


def test_show_reports_text_image_and_binary_files(tmp_path: Path) -> None:
    project = started(tmp_path / "game")
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000000049454e44ae426082"
    )
    (project / "wood.png").write_bytes(png)
    (project / "door.glb").write_bytes(b"glTF\0\0\0\x02" + bytes(100))
    timeline.commit_paths(project, ["wood.png", "door.glb"], "Add the door")
    accept(
        project,
        "Darken the door",
        {"main.gd": "extends Node\nvar dark := true\n", "wood.png": None},
    )
    (project / "door.glb").write_bytes(b"glTF\0\0\0\x02" + bytes(300))
    timeline.commit_paths(project, ["door.glb"], "Make the door bigger")
    entry = timeline.entries(project)["entries"][1]
    shown = timeline.show(project, entry["commit"])
    kinds = {item["path"]: (item["change"], item["kind"]) for item in shown["files"]}
    assert kinds == {"main.gd": ("modified", "text"), "wood.png": ("deleted", "image")}
    assert shown["body"] == timeline.ACCEPTED_LINE
    text = timeline.file_detail(project, entry["commit"], "main.gd")
    assert "+var dark := true" in text["diff"] and text["truncated"] is False
    image = timeline.file_detail(project, entry["commit"], "wood.png")
    assert Path(image["before"]).read_bytes() == png and image["after"] == ""
    newest = timeline.entries(project)["entries"][0]["commit"]
    binary = timeline.file_detail(project, newest, "door.glb")
    assert binary["kind"] == "binary"
    assert (binary["before_size"], binary["after_size"]) == (108, 308)
    with pytest.raises(timeline.TimelineError):
        timeline.file_detail(project, newest, "../secret.txt")
    with pytest.raises(timeline.TimelineError):
        timeline.show(project, "--all")


def test_restore_is_a_reviewed_change_not_a_reset(tmp_path: Path) -> None:
    project = started(tmp_path / "game")
    head_before = git(project, "rev-parse", "HEAD").strip()
    first = timeline.entries(project)["entries"][0]["commit"]
    accept(project, "Make main busy", {"main.gd": "extends Node\nvar busy := true\n"})
    outcome = timeline.restore(project, first, "main.gd")
    # Waiting for review: nothing on disk changed and history was not rewritten.
    assert outcome["state"] == "review"
    assert "var busy" in (project / "main.gd").read_text()
    assert git(project, "rev-parse", "HEAD~1").strip() == head_before
    tx = FileTransaction.load(project, outcome["transaction_id"])
    tx.accept()
    assert (project / "main.gd").read_text() == "extends Node\n"
    assert git(project, "log", "-1", "--format=%s").startswith(
        "Restore main.gd as it was on"
    )
    with pytest.raises(timeline.TimelineError, match="already as it was"):
        timeline.restore(project, first, "main.gd")


def test_a_deleted_file_comes_back_as_it_was_before(tmp_path: Path) -> None:
    project = started(tmp_path / "game")
    tx = accept(project, "Remove main", {"main.gd": None})
    outcome = timeline.restore(project, tx.timeline["commit"], "main.gd")
    FileTransaction.load(project, outcome["transaction_id"]).accept()
    assert (project / "main.gd").read_text() == "extends Node\n"


# --- Command line, paths and the agent gate ---------------------------------------------


def test_cli_lists_shows_and_switches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = started(tmp_path / "game")
    assert cli.main(["timeline", "list", "--project", str(project), "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["state"] == "ready" and listed["auto_commit"] is True
    commit = listed["entries"][0]["commit"]
    assert (
        cli.main(
            [
                "timeline",
                "show",
                "--project",
                str(project),
                "--commit",
                commit,
                "--json",
            ]
        )
        == 0
    )
    assert "main.gd" in {
        item["path"] for item in json.loads(capsys.readouterr().out)["files"]
    }
    assert (
        cli.main(
            [
                "timeline",
                "settings",
                "--project",
                str(project),
                "--auto-commit",
                "off",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"auto_commit": False}
    plain = make_project(tmp_path / "plain")
    assert cli.main(["timeline", "status", "--project", str(plain), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "none"
    assert cli.main(["timeline", "start", "--project", str(plain), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ready"


def test_windows_paths_reach_git_with_forward_slashes(tmp_path: Path) -> None:
    assert (
        timeline.to_git_path("art\\props\\door.png", windows=True)
        == "art/props/door.png"
    )
    assert timeline.to_git_path(
        str(PureWindowsPath("audio", "sfx", "creak.wav")), windows=True
    ) == ("audio/sfx/creak.wav")
    # Elsewhere a backslash is part of a file name.
    assert timeline.to_git_path("odd\\name.gd", windows=False) == "odd\\name.gd"
    assert (
        timeline.from_git_path(tmp_path, "art/props/door.png")
        == tmp_path / "art" / "props" / "door.png"
    )


def test_agents_cannot_restore_or_start_a_timeline(tmp_path: Path) -> None:
    def bash(command: str) -> dict[str, object]:
        return {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(tmp_path),
        }

    for command in (
        "uv run joinery timeline restore --project . --commit abc --path main.gd",
        "cd game && joinery timeline start --project .",
    ):
        verdict = violation(bash(command))
        assert verdict is not None and verdict.severity == Severity.DENY
    assert violation(bash("uv run joinery timeline list --project . --json")) is None
