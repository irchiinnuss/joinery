"""The import cache: stale when a model or a script class arrived since the last import,
and brought up to date before playing and after an accepted change."""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

from joinery import import_cache, playtest

ROOT = Path(__file__).resolve().parents[1]


def _png(path: Path) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"\x00\xff\x00\x00\xff"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    return project


def _imported(project: Path, rel: str) -> None:
    """``rel`` as Godot leaves it after an import: a .import naming its copy."""
    dest = f"res://.godot/imported/{Path(rel).name}-0123.ctex"
    (project / dest.removeprefix("res://")).parent.mkdir(parents=True, exist_ok=True)
    (project / dest.removeprefix("res://")).write_bytes(b"x")
    record = project / (rel + ".import")
    record.write_text(f'[remap]\npath="{dest}"\n[deps]\ndest_files=["{dest}"]\n')
    later = (project / rel).stat().st_mtime + 5
    os.utime(record, (later, later))


def test_no_cache_is_stale(tmp_path: Path) -> None:
    assert import_cache.stale(_project(tmp_path)) == [".godot"]


def test_an_asset_is_stale_until_its_import_is_current(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / ".godot").mkdir()
    _png(project / "art/rock.png")
    assert import_cache.stale(project) == ["art/rock.png"]
    _imported(project, "art/rock.png")
    assert import_cache.stale(project) == []
    # The accepted change brought a newer rock.
    newer = (project / "art/rock.png.import").stat().st_mtime + 5
    os.utime(project / "art/rock.png", (newer, newer))
    assert import_cache.stale(project) == ["art/rock.png"]
    _imported(project, "art/rock.png")
    for copy in (project / ".godot/imported").iterdir():
        copy.unlink()
    assert import_cache.stale(project) == ["art/rock.png"]


def test_a_script_class_the_class_table_lacks_is_stale(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / ".godot").mkdir()
    script = project / "weapons/hitscan.gd"
    script.parent.mkdir()
    script.write_text("class_name HitscanWeapon\nextends Node3D\n")
    assert import_cache.stale(project) == ["weapons/hitscan.gd"], "no .uid yet"
    (project / "weapons/hitscan.gd.uid").write_text("uid://abc\n")
    (project / import_cache.CLASS_TABLE).write_text('list=[{"class": &"Other"}]\n')
    os.utime(project / import_cache.CLASS_TABLE, (1, 1))
    assert import_cache.stale(project) == ["weapons/hitscan.gd"]
    (project / import_cache.CLASS_TABLE).write_text('list=[{"class": &"HitscanWeapon"}]\n')
    assert import_cache.stale(project) == []


def test_ignored_folders_are_not_looked_at(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / ".godot").mkdir()
    _png(project / "raw/rock.png")
    (project / "raw/.gdignore").write_text("")
    _png(project / ".joinery/playtests/1/shot.png")
    assert import_cache.stale(project) == []


def test_ensure_imports_only_when_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    runs: list[Path] = []

    def fake(godot: Path, where: Path, env: Any = None) -> None:
        runs.append(where)
        (where / ".godot").mkdir(exist_ok=True)

    monkeypatch.setattr(import_cache, "run_import", fake)
    godot = tmp_path / "godot"
    assert import_cache.ensure(godot, project)
    assert not import_cache.ensure(godot, project)
    _png(project / "art/new.png")
    playtest._ensure_imported(godot, project, {})
    assert runs == [project, project]


def test_an_accepted_change_refreshes_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    runs: list[Path] = []
    monkeypatch.setattr(
        import_cache, "run_import", lambda godot, where, env=None: runs.append(where)
    )
    godot = tmp_path / "godot"
    godot.write_text("")
    changed = [{"path": "art/rock.png", "change": "added"}]
    # Without a cache the next run makes one.
    assert import_cache.after_accept(project, changed, godot=godot, wait=True) is None
    assert runs == []
    (project / ".godot").mkdir()
    _png(project / "art/rock.png")
    import_cache.after_accept(
        project, [{"path": "design/brief.md"}], godot=godot, wait=True
    )
    assert runs == [], "a change with nothing to import"
    import_cache.after_accept(project, changed, godot=godot, wait=True)
    assert runs == [project]
    thread = import_cache.after_accept(project, changed, godot=godot)
    assert thread is not None
    thread.join(10)
    assert runs == [project, project]


def _godot() -> str:
    for name in ("JOINERY_TEST_GODOT", "JOINERY_GODOT_BIN"):
        value = os.environ.get(name, "")
        if value and Path(value).is_file():
            return value
    stock = ROOT / ".tools/Godot.app/Contents/MacOS/Godot"
    return str(stock) if stock.is_file() else ""


@pytest.mark.skipif(not _godot(), reason="needs a Godot 4.7 binary")
def test_a_real_import_leaves_nothing_stale_and_a_new_class_is_seen(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _png(project / "art/rock.png")
    (project / "old.gd").write_text("class_name OldThing\nextends Node\n")
    godot = Path(_godot())
    env = {**os.environ, "JOINERY_BACKGROUND": "1"}
    assert import_cache.ensure(godot, project, env)
    assert import_cache.stale(project) == []
    (project / "hitscan.gd").write_text("class_name HitscanWeapon\nextends Node3D\n")
    assert import_cache.stale(project) == ["hitscan.gd"]
    assert import_cache.ensure(godot, project, env)
    assert import_cache.stale(project) == []
