"""Real local TCP/pipe/lease behavior plus native Windows adapter checks."""

import asyncio
import base64
import io
import json
import os
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bridge.provider_process import readonly_command
from bridge.stdio import ThreadInput
from joinery import platform_support
from joinery.connection import status
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC, rpc
from joinery.mcp_runtime import host_environment
from joinery.platform_support import (
    command_is_editor,
    command_targets_project,
    hook_command,
    launch_argv,
)
from joinery.server_install import _run
from joinery.transactions import ProjectLease


def test_tcp_gateway_authenticates_and_retains_no_approval_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    monkeypatch.setattr("joinery.gateway.configured", lambda _: ([], []))

    async def run() -> None:
        gateway = ProjectGateway(tmp_path)
        gateway.discover = AsyncMock(return_value={"servers": [], "tools": []})  # type: ignore[method-assign]
        server = GatewayRPC(gateway, transport="tcp")
        await server.start()
        try:
            endpoint = json.loads(server.path.read_text())
            reader, writer = await asyncio.open_connection(
                endpoint["host"], endpoint["port"]
            )
            writer.write(b'{"method":"inventory","params":{}}\n')
            await writer.drain()
            denied = json.loads(await reader.readline())
            assert denied == {"ok": False, "error": "Gateway authentication failed."}
            gateway.discover.assert_not_awaited()
            writer.close()
            await writer.wait_closed()
            assert (await rpc(server.path, "inventory", {}))["tools"] == []
            gateway.discover.assert_awaited_once()
            with pytest.raises(RuntimeError, match="no approval"):
                await rpc(server.path, "decision", {"accept": True})
            if os.name != "nt":
                assert stat.S_IMODE(server.directory.stat().st_mode) == 0o700
                assert stat.S_IMODE(server.path.stat().st_mode) == 0o600
        finally:
            await server.close()
        assert not server.directory.exists()

    asyncio.run(run())


def test_tcp_endpoint_cannot_redirect_connection_off_host(tmp_path: Path) -> None:
    endpoint = tmp_path / "gateway.endpoint.json"
    endpoint.write_text(
        json.dumps(
            {"version": 1, "host": "example.org", "port": 443, "token": "x" * 43}
        )
    )
    with pytest.raises(ValueError, match="Invalid local gateway"):
        asyncio.run(rpc(endpoint, "inventory", {}))


def test_threaded_stdin_delivers_frames_unicode_and_eof() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        transport = ThreadInput(
            io.BytesIO('{"text":"世界"}\n{"kind":"cancel"}\n'.encode()), reader
        )
        try:
            assert (
                json.loads(await asyncio.wait_for(reader.readline(), 2))["text"]
                == "世界"
            )
            assert (
                json.loads(await asyncio.wait_for(reader.readline(), 2))["kind"]
                == "cancel"
            )
            assert await asyncio.wait_for(reader.readline(), 2) == b""
            assert transport.thread.daemon
        finally:
            transport.close()

    asyncio.run(run())


def test_threaded_stdin_refuses_oversized_messages() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        transport = ThreadInput(io.BytesIO(b"x" * 1_000_001), reader)
        try:
            with pytest.raises(ValueError, match="message size limit"):
                await asyncio.wait_for(reader.readline(), 2)
        finally:
            transport.close()

    asyncio.run(run())


def test_project_lease_survives_contending_process_and_releases_on_death(
    tmp_path: Path,
) -> None:
    lease = ProjectLease(tmp_path)
    code = "from pathlib import Path; from joinery.transactions import ProjectLease; import sys\ntry: lease=ProjectLease(Path(sys.argv[1]), wait=0.3)\nexcept ValueError: raise SystemExit(23)\nraise SystemExit(0)"
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)],
            capture_output=True,
            check=False,
        )
        assert result.returncode == 23
    finally:
        lease.close()
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from joinery.transactions import ProjectLease; import sys; lease=ProjectLease(Path(sys.argv[1])); print('ready',flush=True); sys.stdin.buffer.read()",
            str(tmp_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert (
            child.stdout is not None
            and child.stdout.readline().rstrip(b"\r\n") == b"ready"
        )
        child.kill()
        child.wait(timeout=5)
        next_lease = ProjectLease(tmp_path)
        next_lease.close()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        if child.stdin:
            child.stdin.close()
        if child.stdout:
            child.stdout.close()
        if child.stderr:
            child.stderr.close()


def test_editor_project_matching_handles_spaces_without_prefix_collisions(
    tmp_path: Path,
) -> None:
    project = tmp_path / "My Project & Test"
    assert command_targets_project(
        'godot --editor --path "' + str(project) + '"', project
    )
    assert command_targets_project("godot --editor --path " + str(project), project)
    assert not command_targets_project(
        "godot --path " + str(project) + "-other", project
    )
    assert command_targets_project(
        'godot "' + str(project / "project.godot") + '"', project
    )


def test_only_an_editor_on_the_project_counts_not_a_game_it_left_running(
    tmp_path: Path,
) -> None:
    project = tmp_path / "yard day"
    path = str(project)
    assert command_is_editor("godot --editor --path " + path, project)
    assert command_is_editor("godot --path " + path + " -e", project)
    assert command_is_editor('godot "' + str(project / "project.godot") + '"', project)
    assert command_is_editor("godot --path " + path + " --project-manager", project)
    # A game run from the editor that outlived it: --path, but no --editor.
    game = (
        "godot --path " + path + " --remote-debug tcp://127.0.0.1:6007"
        " --editor-pid 14314 --scene res://core/main.tscn"
    )
    assert command_targets_project(game, project)
    assert not command_is_editor(game, project)
    assert not command_is_editor("godot --editor --path " + path + "-other", project)


def test_a_leftover_game_run_does_not_hold_back_an_addon_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX process listing")
    project = tmp_path / "yard day"
    rows = {
        "comm=": "14500 /Applications/Joinery.app/Contents/MacOS/godot.macos.editor.arm64\n"
        "14501 /usr/bin/python3\n",
        "args=": f"14500 godot --path {project} --remote-debug tcp://127.0.0.1:6007"
        " --editor-pid 14314 --scene res://core/main.tscn\n"
        f"14501 python3 -m joinery chat --project {project}\n",
    }

    def ps(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        field = argv[-1].split(",")[-1]
        return subprocess.CompletedProcess(argv, 0, rows[field], "")

    monkeypatch.setattr(platform_support.subprocess, "run", ps)
    assert not platform_support.editor_running(project)
    rows["args="] += f"14502 godot --editor --path {project}\n"
    rows["comm="] += "14502 godot\n"
    assert platform_support.editor_running(project)


def test_a_process_watch_sees_its_process_end_and_never_touches_it() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    watch = platform_support.ProcessWatch(child.pid)
    try:
        assert watch.alive()
        assert watch.alive(), "looking twice changes nothing"
        assert child.poll() is None
        child.kill()
        child.wait()
        deadline = time.monotonic() + 5
        while watch.alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not watch.alive()
    finally:
        watch.close()
        if child.poll() is None:
            child.kill()
            child.wait()
    assert not platform_support.ProcessWatch(0).alive()
    assert not platform_support.ProcessWatch(-1).alive()


def test_a_process_watch_counts_one_it_may_not_inspect_as_running() -> None:
    if os.name == "nt":
        pytest.skip("POSIX permissions")
    watch = platform_support.ProcessWatch(1)  # init/launchd: another account's.
    try:
        assert watch.alive()
    finally:
        watch.close()


def test_the_real_editor_pipe_ends_when_the_editor_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real stdin reader, with the pipe's write end still held open elsewhere."""
    from bridge import stdio

    monkeypatch.setattr(stdio, "EDITOR_POLL_SECONDS", 0.02)
    read_end, write_end = os.pipe()
    open_ends = [write_end]
    editor = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    async def go() -> bytes:
        with os.fdopen(read_end, "rb", buffering=0) as stream:
            if os.name == "nt":
                # input_reader's own reader, without swapping this process's stdin.
                reader = asyncio.StreamReader()
                transport: stdio.InputTransport = ThreadInput(stream, reader)
            else:
                reader, transport = await stdio.input_reader(stream)
            watcher = asyncio.create_task(
                stdio.end_with_editor(editor.pid, reader, transport)
            )
            os.write(write_end, b'{"kind": "ping"}\n')
            first = await asyncio.wait_for(reader.readline(), 5)
            editor.kill()
            editor.wait()
            assert await asyncio.wait_for(reader.readline(), 5) == b""
            await asyncio.wait_for(watcher, 5)
            transport.close()
            # Let a reader thread blocked on the pipe finish before its end closes.
            os.close(open_ends.pop())
            return first

    try:
        assert asyncio.run(go()) == b'{"kind": "ping"}\n'
    finally:
        for end in open_ends:
            os.close(end)
        if editor.poll() is None:
            editor.kill()
            editor.wait()


def test_a_windows_game_run_is_not_an_editor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "yard day"
    game = {
        "ExecutablePath": "C:/Joinery/godot.exe",
        "CommandLine": f'godot.exe --path "{project}" --editor-pid 14314'
        " --scene res://core/main.tscn",
    }
    listed = [game]

    def query(_: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, json.dumps(listed), "")

    monkeypatch.setattr(platform_support.os, "name", "nt")
    monkeypatch.setattr(platform_support.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(platform_support, "_windows_editor_query", query)
    assert not platform_support.editor_running(project)
    listed.append(
        {"ExecutablePath": "", "CommandLine": f'godot.exe --editor --path "{project}"'}
    )
    assert platform_support.editor_running(project)


def test_explicit_python_provider_never_passes_arguments_through_a_shell(
    tmp_path: Path,
) -> None:
    program = tmp_path / "adapter name & text.py"
    program.write_text("import json,sys; print(json.dumps(sys.argv[1:]))")
    arguments = ["$(never_execute)", "%PATH%", 'quote" value', "unicode-世界"]
    result = subprocess.run(
        launch_argv(str(program), arguments), capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == arguments
    assert launch_argv("provider.exe", arguments) == ["provider.exe", *arguments]


def test_powershell_hook_encoding_retains_literal_paths_and_gate_exit() -> None:
    command = hook_command(
        ["uv", "--project", "C:\\My Folder & $() %PATH%\\quote's", "joinery", "hook"]
    )
    assert command.startswith(
        "powershell.exe -NoProfile -NonInteractive -EncodedCommand "
    )
    script = base64.b64decode(command.split()[-1]).decode("utf-16-le")
    assert "'C:\\My Folder & $() %PATH%\\quote''s'" in script
    assert "exit $process.ExitCode" in script
    assert "My Folder" not in command


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell execution")
def test_native_windows_hook_preserves_argv_stdin_and_exit_code(tmp_path: Path) -> None:
    program = tmp_path / "literal path & script.py"
    program.write_text(
        "import json,sys; print(json.dumps({'argv':sys.argv[1:],'stdin':sys.stdin.read()})); raise SystemExit(7)"
    )
    arguments = [
        str(program),
        "quote's",
        'double"quote',
        "&never-execute",
        "%PATH%",
        "$(never_execute)",
        "世界",
    ]
    command = hook_command([sys.executable, *arguments])
    result = subprocess.run(
        shlex.split(command),
        input='{"fixture":"input 世界"}',
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        # Cold Windows PowerShell/AMSI startup on hosted runners can exceed
        # 15 seconds. Execute once and retain all argv/stdin/exit assertions.
        timeout=45,
    )
    assert result.returncode == 7
    returned = json.loads(result.stdout)
    assert returned["argv"] == arguments[1:]
    assert json.loads(returned["stdin"]) == {"fixture": "input 世界"}


def test_windows_external_provider_cannot_fall_back_to_unrestricted_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bridge.provider_process.sys", SimpleNamespace(platform="win32")
    )
    with pytest.raises(ValueError, match="read-only host boundary"):
        readonly_command(["provider.exe"], tmp_path)


def test_windows_npm_uses_direct_node_and_preserves_untrusted_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = tmp_path / "Node & runtime"
    installation.mkdir()
    shim = installation / "npm.cmd"
    shim.write_text("this shell shim must never execute")
    cli = installation / "node_modules/npm/bin/npm-cli.js"
    cli.parent.mkdir(parents=True)
    # The fixture interpreter stands in for node.exe, exercising real process
    # argv transport rather than executing either a shell or the .cmd shim.
    cli.write_text("import json,sys; print(json.dumps(sys.argv[1:]))")
    monkeypatch.setattr(
        platform_support.shutil,
        "which",
        lambda name: str(shim) if name in {"npm", "npm.cmd"} else sys.executable,
    )
    arguments = ["install", "&never-execute", 'double"quote', "%PATH%", "世界"]
    argv = platform_support.windows_npm_argv(["npm", *arguments])
    monkeypatch.setattr("joinery.server_install.npm_argv", lambda _: argv)
    assert json.loads(_run(["npm", *arguments], tmp_path)) == arguments


def test_windows_npm_refuses_unrecognised_shim_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim = tmp_path / "custom.cmd"
    shim.write_text("do not run")
    monkeypatch.setattr(platform_support.shutil, "which", lambda _: str(shim))
    with pytest.raises(ValueError, match="Unsupported Windows npm shim layout"):
        platform_support.windows_npm_argv(["npm", "install"])


def test_windows_codex_npm_layout_connects_without_executing_cmd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim = tmp_path / "Codex & CLI/codex.cmd"
    shim.parent.mkdir()
    shim.write_text("invalid shell shim must never execute")
    cli = shim.parent / "node_modules/@openai/codex/bin/codex.js"
    cli.parent.mkdir(parents=True)
    cli.write_text(
        "import sys; assert sys.argv[1:]==['login','status']; print('Logged in using ChatGPT')"
    )
    monkeypatch.setattr(
        platform_support,
        "sys",
        SimpleNamespace(platform="win32", executable=sys.executable),
    )
    monkeypatch.setattr(
        platform_support.shutil,
        "which",
        lambda name: str(shim) if name == "codex" else sys.executable,
    )
    result = status("codex")
    assert result["installed"] and result["connected"]
    assert result["method"] == "ChatGPT account"
    with pytest.raises(ValueError, match="Unsupported Windows provider shim"):
        launch_argv(str(shim.with_name("unknown.cmd")), ["&never-execute"])


def test_worker_host_environment_has_windows_paths_without_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed = {
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "SYSTEMROOT",
        "ProgramFiles",
    }
    forbidden = {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "JOINERY_MODEL_API_KEY",
        "GOOGLE_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "PYTHONPATH",
        "NODE_OPTIONS",
    }
    for key in allowed | forbidden:
        monkeypatch.setenv(key, "fixture-" + key)
    inherited = host_environment()
    assert all(inherited[key] == "fixture-" + key for key in allowed)
    assert not forbidden.intersection(inherited)


def test_native_server_inherits_selected_graphics_but_not_unrestricted_loaders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graphics = {
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "VK_DRIVER_FILES",
        "VK_ICD_FILENAMES",
        "LIBGL_ALWAYS_SOFTWARE",
        "GALLIUM_DRIVER",
        "MESA_LOADER_DRIVER_OVERRIDE",
    }
    excluded = {
        "OPENAI_API_KEY",
        "JOINERY_MODEL_API_KEY",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "VK_LAYER_PATH",
    }
    for key in graphics | excluded:
        monkeypatch.setenv(key, "fixture-" + key)
    inherited = host_environment()
    assert all(inherited[key] == "fixture-" + key for key in graphics)
    assert not excluded.intersection(inherited)


@pytest.mark.skipif(os.name != "nt", reason="native Windows private-storage DACL")
def test_native_windows_private_storage_has_only_owner_and_system_access(
    tmp_path: Path,
) -> None:
    import win32security

    directory = tmp_path / "private & endpoint"
    directory.mkdir()
    platform_support.private_directory(directory)
    secret = directory / "secret.txt"
    secret.touch()
    platform_support.private_file(secret)
    identity = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        check=True,
    )
    import re

    match = re.search(rb"\bS-1-\d+(?:-\d+)+\b", identity.stdout)
    assert match is not None
    owner = match.group().decode("ascii")
    for path in (directory, secret):
        # Query the native descriptor directly. Get-Acl can emit nonterminating
        # errors and serialize missing properties as null despite exit code 0.
        descriptor = win32security.GetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION,
        )
        control, _revision = descriptor.GetSecurityDescriptorControl()
        assert control & win32security.SE_DACL_PROTECTED
        acl = descriptor.GetSecurityDescriptorDacl()
        assert acl is not None
        rules = [acl.GetAce(index) for index in range(acl.GetAceCount())]
        assert {win32security.ConvertSidToStringSid(rule[2]) for rule in rules} == {
            owner,
            "S-1-5-18",
        }
        assert all(
            rule[0][0] == win32security.ACCESS_ALLOWED_ACE_TYPE for rule in rules
        )
        assert not any(rule[0][1] & win32security.INHERITED_ACE for rule in rules)


def test_a_new_project_skips_the_editor_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from joinery import bootstrap

    (tmp_path / "project.godot").write_text("config_version=5\n")

    def never(_: Path) -> bool:
        raise AssertionError("no addon yet, so no editor can be running it")

    monkeypatch.setattr(bootstrap, "editor_running", never)
    assert bootstrap.update_addon(tmp_path)["state"] == "updated"


def test_a_slow_windows_process_query_is_a_plain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from joinery import platform_support

    def slow(_: str) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("powershell", 45)

    monkeypatch.setattr(platform_support.os, "name", "nt")
    monkeypatch.setattr(platform_support.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(platform_support, "_windows_editor_query", slow)
    with pytest.raises(ValueError, match="took too long"):
        platform_support.editor_running(tmp_path)
