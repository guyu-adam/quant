"""Keep simulation saves within their disk budget and restore archived runs."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa

from q6.sim import layout


def dir_size(path: Path) -> int:
    """Return logical bytes below path, ignoring files that disappear or are locked."""
    total = 0
    try:
        entries = path.rglob("*") if path.is_dir() else (path,)
        for entry in entries:
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def active_runs(saves: Path) -> set[str]:
    """Find runs named by the supervisor or whose database is not safely done."""
    active: set[str] = set()
    state = layout.read_json(saves / "supervisor.json", {}) or {}
    for worker in state.get("workers", []):
        run_id = worker.get("run_id")
        if run_id:
            active.add(str(run_id))

    runs = layout.runs_dir(saves)
    try:
        databases = runs.glob("*.sqlite")
    except OSError:
        return active
    for db in databases:
        run_id = db.stem
        uri = db.resolve().as_uri() + "?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=0.1)) as conn:
                row = conn.execute("SELECT value FROM meta WHERE key='status'").fetchone()
            if row is None or row[0] != layout.STATUS_DONE:
                active.add(run_id)
        except (sqlite3.Error, OSError, ValueError):
            active.add(run_id)
    return active


def _record_error(errors: list[dict[str, str]], path: Path, exc: OSError) -> None:
    errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})


def _remove(path: Path, errors: list[dict[str, str]]) -> bool:
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        _record_error(errors, path, exc)
        return False


def _archive(db: Path, destination: Path, errors: list[dict[str, str]]) -> bool:
    part = destination.with_name(destination.name + ".part")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with db.open("rb") as source, pa.CompressedOutputStream(str(part), "zstd") as sink:
            while chunk := source.read(1024 * 1024):
                sink.write(chunk)
        os.replace(part, destination)
    except (OSError, pa.ArrowException) as exc:
        _record_error(errors, db, exc if isinstance(exc, OSError) else OSError(str(exc)))
        _remove(part, errors)
        return False

    removed = True
    for source in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        if source.exists() and not _remove(source, errors):
            removed = False
    return removed


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return float("inf")


def run_once(
    saves: Path,
    budget: int = layout.SAVES_BUDGET,
    archive_at: int = layout.SAVES_ARCHIVE_AT,
    now: datetime | None = None,
) -> dict:
    """Archive old completed runs and remove old archives/backups to honor budget."""
    saves = Path(saves)
    saves.mkdir(parents=True, exist_ok=True)
    runs = layout.runs_dir(saves)
    archive = layout.archive_dir(saves)
    logs = layout.logs_dir(saves)
    for directory in (runs, archive, logs):
        directory.mkdir(parents=True, exist_ok=True)

    errors: list[dict[str, str]] = []
    before = dir_size(saves)
    archived: list[str] = []
    deleted: list[str] = []
    active = active_runs(saves)

    if before > archive_at:
        try:
            candidates = sorted(runs.glob("*.sqlite"), key=_mtime)
        except OSError:
            candidates = []
        for db in candidates:
            if db.stem in active:
                continue
            if dir_size(saves) <= archive_at:
                break
            target = archive / f"{db.stem}.sqlite.zst"
            if target.exists():  # 已归档完（.zst 写完才 rename）但原库上次没删掉
                for leftover in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
                    if leftover.exists():
                        _remove(leftover, errors)
                continue
            if _archive(db, target, errors):
                archived.append(db.stem)

    if dir_size(saves) > budget:
        try:
            archives = sorted(archive.glob("*.zst"), key=_mtime)
        except OSError:
            archives = []
        for path in archives:
            if dir_size(saves) <= budget:
                break
            if _remove(path, errors):
                deleted.append(str(path))

    if dir_size(saves) > budget:
        try:
            backups = [p for p in logs.glob("*.log.*") if p.is_file() and p.name.rsplit(".", 1)[-1].isdigit()]
            backups.sort(key=_mtime)
        except OSError:
            backups = []
        for path in backups:
            if dir_size(saves) <= budget:
                break
            if _remove(path, errors):
                deleted.append(str(path))

    after = dir_size(saves)
    return {
        "before": before,
        "after": after,
        "archived": archived,
        "deleted": deleted,
        "over_budget": after > budget,
        "errors": errors,
        "ts": (now or datetime.now(UTC)).isoformat(),
    }


def restore(saves: Path, run_id: str) -> Path:
    """Restore a compressed run database without changing its archive."""
    saves = Path(saves)
    source = layout.archive_dir(saves) / f"{run_id}.sqlite.zst"
    destination = layout.run_db(saves, run_id)
    part = destination.with_name(destination.name + ".part")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pa.CompressedInputStream(str(source), "zstd") as reader, part.open("wb") as output:
            while chunk := reader.read(1024 * 1024):
                output.write(chunk)
        os.replace(part, destination)
    except BaseException:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saves", type=Path, required=True)
    parser.add_argument("--once", action="store_true", help="run one cleanup pass (default behavior)")
    args = parser.parse_args()
    print(json.dumps(run_once(args.saves), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
