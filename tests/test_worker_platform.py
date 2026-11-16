"""Workers run as the platform of the image actually present, and the Godot check
worker is built for the host: native on Apple Silicon, amd64 elsewhere. The
checks use it when it is there and the import needs no Blender."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from joinery import bootstrap, readiness, submission, worker_platform
from joinery.worker_platform import AMD64, ARM64, GODOT_WORKER


@pytest.fixture(autouse=True)
def _fresh() -> None:
    worker_platform.forget()


def _host(monkeypatch: pytest.MonkeyPatch, machine: str) -> None:
    monkeypatch.setattr("joinery.worker_platform.platform.machine", lambda: machine)


def _images(monkeypatch: pytest.MonkeyPatch, found: dict[str, str]) -> None:
    monkeypatch.setattr(worker_platform, "present", found.get)


def test_only_the_godot_worker_is_built_for_an_arm64_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, "arm64")
    assert worker_platform.build_platform(GODOT_WORKER) == ARM64
    assert worker_platform.build_platform(GODOT_WORKER + ":latest") == ARM64
    # Blender, Material Maker and Surge XT have no Linux arm64 build.
    for image in ("joinery-sandbox-runtime", "joinery-creative-worker"):
        assert worker_platform.build_platform(image) == AMD64
    _host(monkeypatch, "x86_64")
    assert worker_platform.build_platform(GODOT_WORKER) == AMD64


def test_a_run_uses_the_platform_of_the_image_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, "arm64")
    # An amd64 Godot worker built elsewhere still runs as what it is.
    _images(monkeypatch, {GODOT_WORKER: AMD64, "joinery-mcp-worker": AMD64})
    assert worker_platform.platform_for(GODOT_WORKER) == AMD64
    assert worker_platform.emulated(GODOT_WORKER)
    _images(monkeypatch, {GODOT_WORKER: ARM64})
    assert not worker_platform.emulated(GODOT_WORKER)
    # Absent: what it would be built as.
    assert worker_platform.platform_for("joinery-creative-worker") == AMD64


def test_the_checks_use_the_godot_worker_when_it_is_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    _images(monkeypatch, {})
    assert submission.check_image(tmp_path) == "joinery-creative-worker"
    _images(monkeypatch, {GODOT_WORKER: ARM64})
    assert submission.check_image(tmp_path) == GODOT_WORKER


def test_a_project_whose_import_reads_a_blend_keeps_the_creative_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n")
    _images(monkeypatch, {GODOT_WORKER: ARM64})
    # Native sources under a .gdignore are never imported.
    (tmp_path / "art/sources").mkdir(parents=True)
    (tmp_path / "art/sources/.gdignore").write_text("")
    (tmp_path / "art/sources/mower.blend").write_bytes(b"BLENDER")
    (tmp_path / ".joinery").mkdir()
    (tmp_path / ".joinery/old.blend").write_bytes(b"BLENDER")
    assert not submission.imports_blend(tmp_path)
    assert submission.check_image(tmp_path) == GODOT_WORKER
    (tmp_path / "art/props").mkdir()
    (tmp_path / "art/props/crate.blend").write_bytes(b"BLENDER")
    assert submission.imports_blend(tmp_path)
    assert submission.check_image(tmp_path) == "joinery-creative-worker"
    # Unless the project turned Godot's Blender import off.
    (tmp_path / "project.godot").write_text(
        "config_version=5\n[filesystem]\nimport/blender/enabled=false\n"
    )
    assert submission.check_image(tmp_path) == GODOT_WORKER


def test_the_limits_scale_only_for_an_emulated_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host(monkeypatch, "arm64")
    _images(monkeypatch, {GODOT_WORKER: ARM64, "joinery-creative-worker": AMD64})
    native = submission.scene_test_seconds(tmp_path, GODOT_WORKER)
    assert native == submission.SCENE_TEST_SECONDS
    assert submission.scene_test_seconds(tmp_path, "joinery-creative-worker") == (
        native * submission.EMULATED_FACTOR
    )
    assert submission.import_seconds(tmp_path, GODOT_WORKER) == (
        submission.IMPORT_SECONDS
    )


def test_setup_builds_the_godot_worker_for_the_host_with_its_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, "arm64")
    args = bootstrap._build_args(GODOT_WORKER)
    assert f"JOINERY_PLATFORM={ARM64}" in args
    pins = bootstrap.sandbox_versions()
    assert f"GODOT_VERSION={pins['GODOT_VERSION']}" in args
    assert bootstrap._build_args("joinery-mcp-worker") == []
    assert readiness.WORKER_IMAGES[GODOT_WORKER] == ("godot.Dockerfile", "")
    sources = {p.name for p in readiness.image_sources(GODOT_WORKER)}
    assert {"godot.Dockerfile", "checksums-native.txt", "versions.env"} <= sources


def test_an_image_built_for_another_platform_is_rebuilt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, "arm64")
    _images(monkeypatch, {GODOT_WORKER: AMD64})
    assert not bootstrap._current(GODOT_WORKER, "rev")


def test_the_arm64_godot_pin_is_kept_apart_from_the_tool_images() -> None:
    """Pinning the worker's arm64 Godot must not change what the shared tool
    images are built from (that would rebuild every one of them)."""
    from joinery.catalog import ROOT

    native = (ROOT / "sandbox/checksums-native.txt").read_text()
    assert "fetch-checksums.sh" in native
    assert "godotarm64 " in native
    assert "godotarm64" not in (ROOT / "sandbox/checksums.txt").read_text()
    runtime = {p.name for p in readiness.image_sources("joinery-sandbox-runtime")}
    assert "checksums-native.txt" not in runtime


class _Docker:
    """docker_cli.run for setup: image IDs before and after a build, a worker
    that answers or not, and the removals asked for."""

    def __init__(self, works: bool = True) -> None:
        self.id = "sha256:old"
        self.works = works
        self.removed: list[str] = []

    def __call__(self, argv: list[str], **_: object) -> SimpleNamespace:
        done = SimpleNamespace(returncode=0, stdout="")
        if argv[:3] == ["docker", "image", "inspect"]:
            done.stdout = self.id
        elif argv[:2] == ["docker", "run"]:
            done.stdout = "4.7.2.stable.official" if self.works else ""
            done.returncode = 0 if self.works else 1
        elif argv[:3] == ["docker", "image", "rm"]:
            self.removed.append(argv[3])
        return done


class _Progress:
    def begin(self, _tag: str) -> None:
        pass


def _build(monkeypatch: pytest.MonkeyPatch, docker: _Docker, tag: str) -> None:
    def build(argv: list[str], *_a: object, **_k: object) -> None:
        docker.id = "sha256:new"

    monkeypatch.setattr(bootstrap.docker_cli, "run", docker)
    monkeypatch.setattr(bootstrap.tool_refresh, "run", build)
    bootstrap._docker_build(tag, "godot.Dockerfile", "rev", _Progress())  # type: ignore[arg-type]


def test_an_amd64_godot_worker_is_removed_once_its_arm64_one_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, "arm64")
    _images(monkeypatch, {GODOT_WORKER: AMD64})
    docker = _Docker()
    _build(monkeypatch, docker, GODOT_WORKER)
    assert docker.removed == ["sha256:old"]


def test_nothing_is_removed_when_the_new_worker_does_not_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, "arm64")
    _images(monkeypatch, {GODOT_WORKER: AMD64})
    docker = _Docker(works=False)
    _build(monkeypatch, docker, GODOT_WORKER)
    assert docker.removed == []


def test_a_same_platform_rebuild_and_the_tool_images_remove_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, "arm64")
    _images(monkeypatch, {GODOT_WORKER: ARM64, "joinery-creative-worker": AMD64})
    docker = _Docker()
    _build(monkeypatch, docker, GODOT_WORKER)
    _build(monkeypatch, docker, "joinery-creative-worker")
    assert docker.removed == []
