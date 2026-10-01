"""Append-only registry for experiment runs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from q6.config import config_hash
from q6.data.lockbox import REPO_ROOT, is_unlocked

if TYPE_CHECKING:
    from q6.config import Config

RUNS_NAME = "runs.jsonl"
LOCK_TIMEOUT_S = 10.0
LOCK_RETRY_S = 0.01
ZERO_HASH = "0" * 64
KINDS = {"backtest", "sensitivity", "walkforward", "lockbox", "other"}


class RegistryTamperedError(RuntimeError):
    """Raised when the append-only run registry fails verification."""


def _json_value(value: Any) -> Any:
    """Convert values to JSON-safe primitives while representing non-finite floats."""
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    # Numpy scalar types expose item(); avoid importing numpy in this stdlib-only module.
    if type(value).__module__.startswith("numpy") and hasattr(value, "item"):
        return _json_value(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "nan"
        return "inf" if value > 0 else "-inf"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _canonical(record: dict) -> str:
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _git_metadata() -> tuple[str | None, bool | None]:
    try:
        sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            # registry/ 本身入库：不排除的话第一条记录之后永远是 dirty，这个字段就没意义了
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--", ".", ":!registry"],
            check=True, capture_output=True, text=True,
        ).stdout.strip())
        if len(sha) != 40:
            return None, None
        return sha, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, None


def _last_hash(path: Path) -> str:
    if not path.exists() or path.stat().st_size == 0:
        return ZERO_HASH
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        end = stream.tell()
        pos = end - 1
        while pos >= 0:
            stream.seek(pos)
            if stream.read(1) == b"\n" and pos != end - 1:
                break
            pos -= 1
        start = pos + 1
        stream.seek(start)
        line = stream.readline()
    try:
        return json.loads(line.decode("utf-8"))["hash"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RegistryTamperedError("cannot read final registry hash") from exc


def record_run(*, kind: str, strategy: str, cfg: Config | None, snapshot_id: str | None,
               params: dict, metrics: dict, notes: str = "",
               registry_dir: str | Path | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"invalid run kind: {kind}")
    directory = Path(registry_dir) if registry_dir is not None else REPO_ROOT / "registry"
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / f"{RUNS_NAME}.lock"
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            break
        except FileExistsError as exc:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out acquiring {lock_path}") from exc
            time.sleep(LOCK_RETRY_S)

    try:
        path = directory / RUNS_NAME
        git_sha, git_dirty = _git_metadata()
        record = {
            "run_id": uuid.uuid4().hex,
            "ts": datetime.now(UTC).isoformat(),
            "kind": kind,
            "strategy": str(strategy),
            "git_sha": git_sha,
            "git_dirty": git_dirty,
            "snapshot_id": snapshot_id,
            "config_hash": config_hash(cfg) if cfg is not None else None,
            "params": _json_value(params),
            "metrics": _json_value(metrics),
            "lockbox_unlocked": is_unlocked(),
            "host": platform.node(),
            "pid": os.getpid(),
            "notes": notes,
            "prev_hash": _last_hash(path),
        }
        record["hash"] = hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()
        payload = (_canonical(record) + "\n").encode("utf-8")
        with path.open("ab", buffering=0) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return record
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _read_and_verify(directory: Path) -> list[dict]:
    path = directory / RUNS_NAME
    if not path.exists():
        return []
    records = []
    previous = ZERO_HASH
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.endswith("\n"):
                    raise RegistryTamperedError(f"line {line_number} is incomplete")
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise RegistryTamperedError(f"line {line_number} is not an object")
                supplied_hash = record.get("hash")
                if record.get("prev_hash") != previous:
                    raise RegistryTamperedError(f"broken hash link at line {line_number}")
                unhashed = {key: value for key, value in record.items() if key != "hash"}
                expected = hashlib.sha256(_canonical(unhashed).encode("utf-8")).hexdigest()
                if supplied_hash != expected:
                    raise RegistryTamperedError(f"invalid hash at line {line_number}")
                records.append(record)
                previous = supplied_hash
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryTamperedError("invalid JSONL registry") from exc
    return records


def read_runs(registry_dir: str | Path | None = None) -> list[dict]:
    directory = Path(registry_dir) if registry_dir is not None else REPO_ROOT / "registry"
    return _read_and_verify(directory)


def verify_chain(registry_dir: str | Path | None = None) -> int:
    return len(read_runs(registry_dir))
