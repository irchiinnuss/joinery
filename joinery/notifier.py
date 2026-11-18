"""JoineryNotifier.app: the macOS helper that shows Joinery's desktop notifications.

``osascript display notification`` shows them as Script Editor, with its icon. This
small Swift app (``release/notifier/JoineryNotifier.swift``) posts them through the
user-notification framework under its own bundle id and the Joinery icon. The
release puts it in ``Joinery.app/Contents/Helpers``; a source checkout builds it into
``~/Library/Caches/Joinery`` with ``joinery notifier build`` (ad-hoc signed). The
editor (``desktop_notify.gd``) uses whichever it finds and falls back to osascript.
"""

from __future__ import annotations

import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from joinery.catalog import ROOT
from joinery.distribution import bundle_version

NAME = "JoineryNotifier"
BUNDLE_ID = "org.joinery.notifier"
SOURCE = ROOT / "release/notifier/JoineryNotifier.swift"
ICON = ROOT / "release/icon/dist/joinery.icns"
# The editor looks here too (desktop_notify.gd); the launcher's cache folder.
CACHE = Path.home() / "Library/Caches/Joinery"
# Where the release puts it, inside the app.
HELPERS = "Joinery.app/Contents/Helpers"
# Joinery.app's own LSMinimumSystemVersion.
MINIMUM_MACOS = "13.0"


def info_plist() -> bytes:
    return plistlib.dumps(
        {
            "CFBundleName": NAME,
            "CFBundleDisplayName": "Joinery",
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleExecutable": NAME,
            "CFBundleIconFile": "Joinery",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": bundle_version(),
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": MINIMUM_MACOS,
            # No Dock icon and no menu bar: it only posts and leaves.
            "LSUIElement": True,
            "NSPrincipalClass": "NSApplication",
        }
    )


def compile_command(output: Path, *, arch: str = "") -> list[str]:
    arch = arch or ("arm64" if platform.machine() == "arm64" else "x86_64")
    if arch not in {"arm64", "x86_64"}:
        raise ValueError(f"Unknown macOS architecture {arch!r}; use arm64 or x86_64.")
    return [
        "xcrun", "swiftc", "-O", "-target", f"{arch}-apple-macos{MINIMUM_MACOS}",
        "-framework", "AppKit", "-framework", "UserNotifications",
        "-o", str(output), str(SOURCE),
    ]  # fmt: skip


def sign_command(app: Path, identity: str = "-") -> list[str]:
    command = ["codesign", "--force", "--sign", identity]
    if identity != "-":
        command += ["--timestamp", "--options", "runtime"]
    return [*command, str(app)]


def _run(argv: list[str]) -> None:
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise ValueError(
            f"{Path(argv[0]).name} failed: {(done.stderr or done.stdout).strip()}"
        )


def build(
    destination: Path = CACHE,
    *,
    identity: str = "-",
    arch: str = "",
) -> Path:
    """Compile, lay out and sign ``destination/JoineryNotifier.app``; return it.

    ``identity`` "-" signs ad hoc, which is what a source checkout needs; the release
    passes its Developer ID through ``release/platform_sign.py`` instead.
    """
    if sys.platform != "darwin":
        raise ValueError("JoineryNotifier is a macOS helper; build it on a Mac.")
    if shutil.which("xcrun") is None:
        raise ValueError("Needs the Xcode command line tools: xcode-select --install")
    destination.mkdir(parents=True, exist_ok=True)
    app = destination / f"{NAME}.app"
    # Built beside the old one and swapped in, so a notification never finds half an app.
    staging = Path(tempfile.mkdtemp(prefix=".notifier-", dir=destination))
    try:
        built = staging / f"{NAME}.app"
        (built / "Contents/MacOS").mkdir(parents=True)
        (built / "Contents/Resources").mkdir()
        _run(compile_command(built / "Contents/MacOS" / NAME, arch=arch))
        (built / "Contents/Info.plist").write_bytes(info_plist())
        shutil.copyfile(ICON, built / "Contents/Resources/Joinery.icns")
        _run(sign_command(built, identity))
        if app.exists():
            shutil.rmtree(app)
        built.rename(app)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return app


def bundle_files(app: Path) -> dict[str, Path]:
    """Every file of a built notifier, by its path inside ``Joinery.app``."""
    return {
        f"{HELPERS}/{app.name}/{path.relative_to(app).as_posix()}": path
        for path in sorted(app.rglob("*"))
        if path.is_file()
    }


def executable(app: Path) -> Path:
    return app / "Contents/MacOS" / NAME


def send(title: str, body: str, app: Path | None = None) -> int:
    """Show one notification through the built notifier; its exit status."""
    program = executable(app or CACHE / f"{NAME}.app")
    if not program.is_file():
        raise ValueError("No notifier yet; run joinery notifier build.")
    return subprocess.run([str(program), "--", title, body], check=False).returncode
