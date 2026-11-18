"""JoineryNotifier.app: built from source, signed, shipped in Joinery.app, and safe with text."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from joinery import cli, distribution, notifier
from joinery.catalog import ROOT
from release import platform_sign
from tests.test_distribution import executable, source  # noqa: F401  (fixture)

SWIFT = notifier.SOURCE.read_text(encoding="utf-8")
# The code without its comments.
CODE = "\n".join(
    line for line in SWIFT.splitlines() if not line.lstrip().startswith(("//", "///"))
)


def test_info_plist_names_joinery_with_its_icon_and_no_dock_icon() -> None:
    info = plistlib.loads(notifier.info_plist())
    assert info["CFBundleIdentifier"] == "org.joinery.notifier"
    assert info["CFBundleDisplayName"] == "Joinery"
    assert info["CFBundleExecutable"] == "JoineryNotifier"
    assert info["CFBundleIconFile"] == "Joinery"
    assert info["LSUIElement"] is True
    assert info["CFBundleShortVersionString"] == distribution.bundle_version()


def test_the_text_never_becomes_script_source() -> None:
    """The title and body are argv strings handed to the notification APIs, nothing more."""
    for runner in ("NSAppleScript", "OSAScript", "osascript", "Process(", "/bin/sh"):
        assert runner not in CODE, runner
    assert "CommandLine.arguments" in CODE
    assert "content.title = title" in CODE and "content.body = body" in CODE
    assert "UNUserNotificationCenter" in CODE and "NSUserNotification" in CODE
    assert "requestAuthorization" in CODE


def test_compile_and_sign_commands() -> None:
    command = notifier.compile_command(Path("/out/JoineryNotifier"), arch="arm64")
    assert command[:2] == ["xcrun", "swiftc"]
    assert command[command.index("-target") + 1] == "arm64-apple-macos13.0"
    assert command[-1] == str(notifier.SOURCE)
    with pytest.raises(ValueError, match="arm64 or x86_64"):
        notifier.compile_command(Path("/out/x"), arch="ppc")
    assert notifier.sign_command(Path("/a.app")) == [
        "codesign",
        "--force",
        "--sign",
        "-",
        "/a.app",
    ]
    signed = notifier.sign_command(Path("/a.app"), "Developer ID Application: X")
    assert "--timestamp" in signed and "runtime" in signed


def test_build_lays_out_and_replaces_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> None:
        calls.append(argv)
        if argv[1] == "swiftc":
            Path(argv[argv.index("-o") + 1]).write_bytes(b"binary")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(notifier, "_run", run)
    old = tmp_path / "JoineryNotifier.app/Contents"
    old.mkdir(parents=True)
    (old / "stale").write_text("old")
    app = notifier.build(tmp_path, arch="arm64")
    assert app == tmp_path / "JoineryNotifier.app"
    assert (app / "Contents/MacOS/JoineryNotifier").read_bytes() == b"binary"
    assert (app / "Contents/Resources/Joinery.icns").read_bytes()[:4] == b"icns"
    assert plistlib.loads((app / "Contents/Info.plist").read_bytes())["LSUIElement"]
    assert not (app / "Contents/stale").exists()
    assert [c[0] for c in calls] == ["xcrun", "codesign"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["JoineryNotifier.app"]


def test_build_refuses_off_a_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ValueError, match="macOS helper"):
        notifier.build(Path("/nowhere"))


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("xcrun") is None, reason="needs a Mac"
)
def test_real_build_is_signed_and_checks_its_arguments(tmp_path: Path) -> None:
    app = notifier.build(tmp_path)
    subprocess.run(["codesign", "--verify", "--strict", str(app)], check=True)
    program = str(notifier.executable(app))
    # Without arguments (a click on a notification) it leaves at once, showing nothing.
    assert subprocess.run([program], timeout=10, check=False).returncode == 0
    wrong = subprocess.run([program, "--", "only a title"], timeout=10, check=False)
    assert wrong.returncode == 1


def test_macos_bundle_carries_the_notifier_in_helpers(
    source: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    app = tmp_path / "built/JoineryNotifier.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Resources").mkdir()
    (app / "Contents/_CodeSignature").mkdir()
    (app / "Contents/Info.plist").write_bytes(notifier.info_plist())
    program = app / "Contents/MacOS/JoineryNotifier"
    executable(program, "macos-arm64")
    (app / "Contents/_CodeSignature/CodeResources").write_text("sealed")
    output = tmp_path / "Joinery.zip"
    distribution.package(
        output,
        desktop=True,
        binary=executable(source / "engine", "macos-arm64"),
        notifier=app,
    )
    helper = "Joinery.app/Contents/Helpers/JoineryNotifier.app/Contents/"
    with zipfile.ZipFile(output) as archive:
        assert (
            archive.getinfo(helper + "MacOS/JoineryNotifier").external_attr >> 16
            & 0o111
        )
        assert archive.read(helper + "_CodeSignature/CodeResources") == b"sealed"
        info = plistlib.loads(archive.read(helper + "Info.plist"))
        assert info["CFBundleIdentifier"] == "org.joinery.notifier"
        manifest = archive.read(
            "Joinery.app/Contents/Resources/joinery/manifest.json"
        ).decode()
        assert helper + "MacOS/JoineryNotifier" in manifest
    with pytest.raises(ValueError, match="only into a macOS desktop bundle"):
        distribution.package(
            tmp_path / "linux.tar.gz",
            desktop=True,
            binary=executable(source / "godot.linux.editor.x86_64", "linux-x86_64"),
            notifier=app,
        )


def test_cli_builds_into_the_cache_by_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, object] = {}

    def build(destination: Path, *, identity: str, arch: str) -> Path:
        seen.update(destination=destination, identity=identity, arch=arch)
        return destination / "JoineryNotifier.app"

    monkeypatch.setattr(notifier, "build", build)
    assert cli.main(["notifier", "build"]) == 0
    assert seen == {"destination": notifier.CACHE, "identity": "-", "arch": ""}
    assert "JoineryNotifier.app" in capsys.readouterr().out


def test_release_signing_signs_the_notifier_before_sealing_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_: object) -> None:
        calls.append(argv)
        if argv[:2] == ["ditto", "-x"]:
            work = Path(argv[-1])
            (work / "Joinery.app" / platform_sign.NOTIFIER).mkdir(parents=True)
        elif argv[:2] == ["ditto", "-c"]:
            Path(argv[-1]).write_bytes(b"zip")

    monkeypatch.setattr(platform_sign, "_run", run)
    archive = tmp_path / "Joinery-0.7.0-macos-arm64.zip"
    archive.write_bytes(b"zip")
    platform_sign.sign_macos(
        archive, identity="Developer ID Application: X", notarize=False
    )
    signed = [c[-1] for c in calls if c[0] == "codesign" and "--verify" not in c]
    assert [Path(p).name for p in signed] == [
        "Godot",
        "JoineryNotifier.app",
        "Joinery.app",
    ]
    helper = next(c for c in calls if c[-1].endswith("JoineryNotifier.app"))
    assert "--entitlements" not in helper
    assert "runtime" in helper and "--timestamp" in helper


def test_native_build_packages_the_notifier_on_macos() -> None:
    native = (ROOT / ".github/workflows/native.yml").read_text(encoding="utf-8")
    build = native.index("joinery notifier build --output out/notifier")
    package = native.index("uv run joinery package --desktop")
    assert build < package
    assert '--notifier "$JOINERY_NOTIFIER"' in native
