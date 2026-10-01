"""内容寻址的数据快照写入、加载与完整性校验。

快照 ID 由 Parquet 文件字节决定。升级 pyarrow 或 pandas 可能改变这些字节，
即使数据本身没有变化也会改变快照 ID；因此可复现 ID 的前提是由 uv.lock 锁定依赖版本。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


class SnapshotIntegrityError(ValueError):
    """快照文件、清单或内容地址校验失败。"""


_TABLE_PATHS = {
    "quarantine": "quarantine.parquet",
    "calendar": "calendar.parquet",
    "universe_monthly": "universe_monthly.parquet",
}
_BOOL_COLUMNS = {"bad", "is_new", "is_st", "tradable"}
_INT8_COLUMNS = {"tradestatus", "isST"}
_INT64_COLUMNS = {"volume"}


def _normalize(frame: pd.DataFrame, table: str) -> pd.DataFrame:
    result = frame.copy()
    for name in result.columns:
        if name in {"date", "month_end", "update_date"}:
            result[name] = pd.to_datetime(result[name]).astype("datetime64[ns]")
        elif name == "code":
            result[name] = result[name].astype(str)
        elif name in _BOOL_COLUMNS or pd.api.types.is_bool_dtype(result[name].dtype):
            result[name] = result[name].astype(bool)
        elif name in _INT8_COLUMNS:
            result[name] = pd.to_numeric(result[name]).astype("int8")
        elif name in _INT64_COLUMNS:
            result[name] = pd.to_numeric(result[name]).astype("int64")
        elif name == "listed_days":
            result[name] = pd.to_numeric(result[name]).astype("int32")
        elif name == "index":
            result[name] = result[name].astype(str)
        elif pd.api.types.is_numeric_dtype(result[name].dtype):
            result[name] = pd.to_numeric(result[name]).astype("float64")
    if table == "daily":
        return result.sort_values(["date", "code"], kind="mergesort").reset_index(drop=True)
    return result.sort_values(list(result.columns), kind="mergesort", na_position="first").reset_index(
        drop=True
    )


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot_id(schema: int, asof: str, files: list[dict]) -> str:
    payload = json.dumps(
        {"schema": schema, "asof": asof, "files": files}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(
        table, path, compression="zstd", compression_level=9, use_dictionary=True, write_statistics=True
    )


def write_snapshot(
    tables: dict[str, pd.DataFrame], root: Path, *, asof: str, sources: dict, code_version: str
) -> str:
    """规范化表格并原子写入一个由文件内容决定 ID 的快照。"""
    if "daily" not in tables:
        raise ValueError("tables 必须包含 daily")
    unknown = set(tables) - {"daily", *_TABLE_PATHS}
    if unknown:
        raise ValueError(f"未知表：{sorted(unknown)}")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=root))
    try:
        files: list[dict] = []
        for name in sorted(tables):
            frame = _normalize(tables[name], name)
            if name == "daily":
                if "date" not in frame:
                    raise ValueError("daily 缺少 date 列")
                for year, group in frame.groupby(frame["date"].dt.year, sort=True):
                    rel = f"daily/year={int(year)}.parquet"
                    target = temp / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _write_parquet(group.reset_index(drop=True), target)
                    files.append(
                        {
                            "path": rel,
                            "sha256": _hash(target),
                            "rows": len(group),
                            "bytes": target.stat().st_size,
                        }
                    )
            else:
                rel = _TABLE_PATHS[name]
                target = temp / rel
                _write_parquet(frame, target)
                files.append(
                    {"path": rel, "sha256": _hash(target), "rows": len(frame), "bytes": target.stat().st_size}
                )
        files.sort(key=lambda item: item["path"])
        sid = _snapshot_id(1, asof, files)
        manifest = {
            "schema": 1,
            "asof": asof,
            "sources": sources,
            "code_version": code_version,
            "files": files,
            "snapshot_id": sid,
            "created_at": datetime.now(UTC).isoformat(),
        }
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        destination = root / sid
        if destination.exists():
            verify_snapshot(root, sid)
            shutil.rmtree(temp)
            return sid
        os.replace(temp, destination)
        return sid
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def _read_manifest(root: Path, snapshot_id: str) -> tuple[Path, dict, list[dict]]:
    directory = Path(root) / snapshot_id
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        files = manifest["files"]
        calculated = _snapshot_id(manifest["schema"], manifest["asof"], files)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SnapshotIntegrityError(f"无法读取快照清单 {directory / 'manifest.json'}: {exc}") from exc
    if calculated != snapshot_id or manifest.get("snapshot_id") != snapshot_id:
        raise SnapshotIntegrityError(
            f"快照 ID 不一致：目录={snapshot_id}，清单={manifest.get('snapshot_id')}，计算={calculated}"
        )
    return directory, manifest, files


def _validate_extras(directory: Path, files: list[dict]) -> None:
    expected = {item["path"] for item in files}
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob("*.parquet")}
    extras = sorted(actual - expected)
    if extras:
        raise SnapshotIntegrityError(f"清单外的 parquet 文件：{', '.join(extras)}")


def _validate_file(directory: Path, entry: dict) -> Path:
    path = directory / entry["path"]
    try:
        actual = _hash(path)
        size = path.stat().st_size
        rows = pq.ParquetFile(path).metadata.num_rows
    except (OSError, pa.ArrowException) as exc:
        raise SnapshotIntegrityError(f"文件 {entry['path']} 无法读取：{exc}") from exc
    if actual != entry["sha256"]:
        raise SnapshotIntegrityError(
            f"文件 {entry['path']} SHA256 不匹配：期望 {entry['sha256']}，实际 {actual}"
        )
    if size != entry["bytes"] or rows != entry["rows"]:
        raise SnapshotIntegrityError(
            f"文件 {entry['path']} 元数据不匹配：行数期望 {entry['rows']} 实际 {rows}，"
            f"字节期望 {entry['bytes']} 实际 {size}"
        )
    return path


def verify_snapshot(root: Path, snapshot_id: str) -> None:
    """校验清单地址、全部文件哈希及行数/字节数。"""
    directory, _, files = _read_manifest(root, snapshot_id)
    _validate_extras(directory, files)
    for entry in files:
        _validate_file(directory, entry)


def load_snapshot(
    root: Path,
    snapshot_id: str,
    tables: tuple[str, ...] = ("daily",),
    years: tuple[int, int] | None = None,
) -> dict[str, pd.DataFrame]:
    """校验并读取指定表；daily 可按闭区间年份筛选。"""
    directory, _, files = _read_manifest(root, snapshot_id)
    _validate_extras(directory, files)
    unknown = set(tables) - {"daily", *_TABLE_PATHS}
    if unknown:
        raise ValueError(f"未知表：{sorted(unknown)}")
    result: dict[str, pd.DataFrame] = {}
    for name in tables:
        if name == "daily":
            entries = [item for item in files if item["path"].startswith("daily/")]
            if years is not None:
                entries = [
                    item
                    for item in entries
                    if years[0] <= int(Path(item["path"]).stem.split("=")[1]) <= years[1]
                ]
            pieces = []
            for entry in entries:
                path = _validate_file(directory, entry)
                pieces.append(pq.read_table(path).to_pandas())
            result[name] = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
            if not result[name].empty:
                result[name] = (
                    result[name].sort_values(["date", "code"], kind="mergesort").reset_index(drop=True)
                )
        else:
            rel = _TABLE_PATHS[name]
            entry = next((item for item in files if item["path"] == rel), None)
            if entry is None:
                raise KeyError(f"快照清单缺少请求的表 {name!r}（文件 {rel}）")
            result[name] = pq.read_table(_validate_file(directory, entry)).to_pandas()
    return result
