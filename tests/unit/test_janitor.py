"""Janitor safety and compression behavior, including sparse logical-size accounting."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from q6.sim import janitor, layout


def _db(saves: Path, run_id: str, status: str = "done", payload: bytes = b"small") -> Path:
    path = layout.run_db(saves, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(layout.SCHEMA)
        conn.execute("INSERT INTO meta(key, value) VALUES ('status', ?)", (status,))
        conn.execute(
            "INSERT INTO checkpoint(id, day, n_days, blob, written_at) "
            "VALUES (1, '2020-01-01', 1, ?, 'now')",
            (payload,),
        )
        conn.commit()
    return path


def test_sparse_2_5gb_runs_under_budget(tmp_path: Path) -> None:
    # truncate creates sparse files; dir_size intentionally counts their logical size.
    saves = layout.ensure(tmp_path / "saves")
    old = _db(saves, "old")
    with old.open("ab") as stream:
        stream.truncate(2_500_000_000)
    result = janitor.run_once(saves, budget=100_000_000, archive_at=90_000_000)
    assert result["before"] == 2_500_000_000
    assert result["after"] <= 100_000_000, result
    assert not result["over_budget"]
    assert "old" in result["archived"]


def test_real_compression_and_restore_are_byte_exact(tmp_path: Path) -> None:
    saves = layout.ensure(tmp_path / "saves")
    # A deterministic, incompressible 30 MB payload exercises the real zstd stream.
    payload = os.urandom(30 * 1024 * 1024)
    db = _db(saves, "real", payload=payload)
    original = db.read_bytes()
    result = janitor.run_once(saves, budget=40 * 1024 * 1024, archive_at=20 * 1024 * 1024)
    assert result["archived"] == ["real"]
    assert not db.exists()
    restored = janitor.restore(saves, "real")
    assert restored.read_bytes() == original
    assert (layout.archive_dir(saves) / "real.sqlite.zst").exists()


@pytest.mark.parametrize("active_source", ["supervisor", "status"])
def test_active_databases_are_never_archived_or_deleted(tmp_path: Path, active_source: str) -> None:
    saves = layout.ensure(tmp_path / "saves")
    db = _db(saves, "busy", status="running" if active_source == "status" else "done")
    with db.open("ab") as stream:
        stream.truncate(1024 * 1024)
    if active_source == "supervisor":
        layout.write_json_atomic(saves / "supervisor.json", {"workers": [{"run_id": "busy"}]})
    result = janitor.run_once(saves, budget=1, archive_at=1)
    assert db.exists()
    assert "busy" not in result["archived"]
    assert result["over_budget"]


def test_archive_order_and_old_archive_deletion(tmp_path: Path) -> None:
    saves = layout.ensure(tmp_path / "saves")
    old = _db(saves, "older")
    newer = _db(saves, "newer")
    os.utime(old, (1, 1))
    os.utime(newer, (2, 2))
    archive = layout.archive_dir(saves)
    first, second = archive / "first.sqlite.zst", archive / "second.sqlite.zst"
    first.write_bytes(b"1" * 100)
    second.write_bytes(b"2" * 100)
    os.utime(first, (3, 3))
    os.utime(second, (4, 4))
    budget = janitor.dir_size(saves) - 100
    result = janitor.run_once(saves, budget=budget, archive_at=10**9)
    assert result["archived"] == []
    # Oldest existing archive is removed first until the logical-size budget is met.
    assert str(first) in result["deleted"]
    assert not first.exists()
    assert second.exists()


def test_only_rotated_logs_are_deletable(tmp_path: Path) -> None:
    saves = layout.ensure(tmp_path / "saves")
    current = layout.logs_dir(saves) / "x.log"
    current.write_bytes(b"current")
    backups = [layout.logs_dir(saves) / f"x.log.{n}" for n in range(1, 6)]
    for index, path in enumerate(backups):
        path.write_bytes(bytes([index]) * 100)
    result = janitor.run_once(saves, budget=7, archive_at=0)
    assert current.exists()
    assert all(not path.exists() for path in backups)
    assert len(result["deleted"]) == 5


def test_failed_file_removal_is_recorded_and_does_not_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saves = layout.ensure(tmp_path / "saves")
    first = layout.archive_dir(saves) / "first.zst"
    second = layout.archive_dir(saves) / "second.zst"
    first.write_bytes(b"a" * 100)
    second.write_bytes(b"b" * 100)
    os.utime(first, (1, 1))
    os.utime(second, (2, 2))
    original_remove = os.remove

    def fail_first(path, *args, **kwargs):
        if Path(path) == first:
            raise PermissionError("locked for test")
        return original_remove(path, *args, **kwargs)

    monkeypatch.setattr(os, "remove", fail_first)
    result = janitor.run_once(saves, budget=50, archive_at=0)
    assert first.exists()
    assert not second.exists()
    assert result["errors"] == [{"path": str(first), "error": "PermissionError: locked for test"}]


def test_leftover_db_removed_when_archive_already_complete(tmp_path):
    """上次压完但原库删不掉（Win 上被面板读着）：下次运行要把原库补删，而不是一直占双份空间。"""
    saves = tmp_path / "saves"
    runs = layout.runs_dir(saves)
    runs.mkdir(parents=True)
    db = runs / "old.sqlite"
    # 只用 `with connect()` 不会关连接，Win 上测试自己占着文件、janitor 删不掉
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(layout.SCHEMA)
        conn.execute("INSERT INTO meta VALUES ('status', 'done')")
        conn.commit()
    layout.archive_dir(saves).mkdir(parents=True)
    (layout.archive_dir(saves) / "old.sqlite.zst").write_bytes(b"complete archive")
    result = janitor.run_once(saves, budget=10**9, archive_at=0)
    assert not db.exists() and result["errors"] == []
