"""内容寻址的数据快照写入、加载与完整性校验。

快照 ID 由 Parquet 文件字节决定。升级 pyarrow 或 pandas 可能改变这些字节，
即使数据本身没有变化也会改变快照 ID；因此可复现 ID 的前提是由 uv.lock 锁定依赖版本。
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from q6.data import lockbox


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
            if isinstance(result[name].dtype, pd.CategoricalDtype):
                categories = sorted(result[name].cat.categories.astype(str))
                result[name] = result[name].cat.set_categories(categories)
            else:
                result[name] = result[name].astype(str)
        elif name in _BOOL_COLUMNS or pd.api.types.is_bool_dtype(result[name].dtype):
            result[name] = result[name].astype(bool)
        elif name in _INT8_COLUMNS:
            result[name] = pd.to_numeric(result[name]).astype("int8")
        elif name in _INT64_COLUMNS:
            volume = pd.to_numeric(result[name])
            result[name] = volume.astype("Int64" if volume.isna().any() else "int64")
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
    tables: dict[str, pd.DataFrame | Iterable[tuple[int, pd.DataFrame | str | Path]]],
    root: Path,
    *,
    asof: str,
    sources: dict,
    code_version: str,
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
            if name == "daily":
                daily_table = tables[name]
                if isinstance(daily_table, pd.DataFrame):
                    frame = _normalize(daily_table, name)
                    if "date" not in frame:
                        raise ValueError("daily 缺少 date 列")
                    yearly: Iterator[tuple[int, pd.DataFrame]] = iter(
                        (int(year), group) for year, group in frame.groupby(frame["date"].dt.year, sort=True)
                    )
                else:
                    yearly = iter(daily_table)
                previous_year: int | None = None
                for year, part in yearly:
                    if previous_year is not None and year <= previous_year:
                        raise ValueError("daily 年分区必须按年份严格升序")
                    previous_year = year
                    rel = f"daily/year={int(year)}.parquet"
                    target = temp / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if isinstance(part, (str, Path)):
                        source = Path(part)
                        metadata = pq.ParquetFile(source).metadata
                        if "date" not in pq.ParquetFile(source).schema_arrow.names:
                            raise ValueError(f"daily 年分区 {year} 缺少 date 列")
                        shutil.copyfile(source, target)
                        row_count = metadata.num_rows
                    else:
                        group = _normalize(part, name)
                        if "date" not in group or (
                            not group.empty and not group["date"].dt.year.eq(year).all()
                        ):
                            raise ValueError(f"daily 年分区 {year} 日期缺失或年份不匹配")
                        _write_parquet(group.reset_index(drop=True), target)
                        row_count = len(group)
                    files.append(
                        {
                            "path": rel,
                            "sha256": _hash(target),
                            "rows": row_count,
                            "bytes": target.stat().st_size,
                        }
                    )
                    if not isinstance(part, (str, Path)):
                        del group
                    del part
                    gc.collect()
                    pa.default_memory_pool().release_unused()
            else:
                frame = _normalize(tables[name], name)
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


def _resolve(root: Path, snapshot: str | Path) -> tuple[Path, str]:
    """snapshot 可以是裸 ID（到 root 下找），也可以是快照目录路径（绝对，或相对当前目录）。

    ID 一律取目录名，再与清单、重算值比对。"""
    spec = Path(snapshot)
    directory = spec if spec.is_absolute() or len(spec.parts) > 1 else Path(root) / spec
    return directory, directory.name


def _read_manifest(root: Path, snapshot: str | Path) -> tuple[Path, dict, list[dict]]:
    directory, snapshot_id = _resolve(root, snapshot)
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        files = manifest["files"]
        calculated = _snapshot_id(manifest["schema"], manifest["asof"], files)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SnapshotIntegrityError(f"无法读取快照清单 {directory / 'manifest.json'}: {exc}") from exc
    if calculated != snapshot_id or manifest.get("snapshot_id") != snapshot_id:
        raise SnapshotIntegrityError(
            f"快照 ID 不一致：目录={snapshot_id}（{directory}），"
            f"清单={manifest.get('snapshot_id')}，计算={calculated}"
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


def verify_snapshot(root: Path, snapshot_id: str | Path) -> None:
    """校验清单地址、全部文件哈希及行数/字节数。snapshot_id 也可传快照目录路径。"""
    directory, _, files = _read_manifest(root, snapshot_id)
    _validate_extras(directory, files)
    for entry in files:
        _validate_file(directory, entry)


# 各表用来判定锁箱期的日期列（任意一列 ≥ 锁箱起点就整行剔除）
_LOCKBOX_COLUMNS = {
    "daily": ("date",),
    "calendar": ("date",),
    "quarantine": ("date",),
    "universe_monthly": ("month_end", "update_date"),
}


def _drop_lockbox(table: pa.Table, name: str) -> pa.Table:
    """锁定状态下在 Arrow 层剔除锁箱期行，这些行不会被转成 pandas 交给调用方。"""
    if lockbox.is_unlocked():
        return table
    keep = None
    for column in _LOCKBOX_COLUMNS[name]:
        if column not in table.column_names:
            continue
        values = table[column]
        cutoff = pa.scalar(lockbox.LOCKBOX_TS.to_pydatetime(), type=pa.timestamp("ns"))
        before = pc.fill_null(pc.less(pc.cast(values, pa.timestamp("ns")), cutoff), True)
        keep = before if keep is None else pc.and_(keep, before)
    if keep is None:
        raise lockbox.LockboxError(f"表 {name} 缺少日期列 {_LOCKBOX_COLUMNS[name]}，无法执行锁箱期检查")
    return table.filter(keep)


def load_snapshot(
    root: Path,
    snapshot_id: str | Path,
    tables: tuple[str, ...] = ("daily",),
    years: tuple[int, int] | None = None,
    columns: tuple[str, ...] | None = None,
) -> dict[str, pd.DataFrame]:
    """校验并读取指定表；daily 可按闭区间年份筛选、按列裁剪（date / code 总会读入）。

    锁箱期（data/lockbox.py）未解锁时：
    - years 显式超出锁箱起点所在年 → LockboxError；
    - 起点之后整年的 daily 分区不打开、不校验、不读取；
    - 其余所有表都在 Arrow 层剔除日期 ≥ 锁箱起点的行（universe_monthly 同时看 month_end 和 update_date）。
    """
    lockbox.check_year_range(years, "load_snapshot")
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
            if not lockbox.is_unlocked():
                entries = [
                    item
                    for item in entries
                    if int(Path(item["path"]).stem.split("=")[1]) <= lockbox.LOCKBOX_START.year
                ]
            cols = None if columns is None else list(dict.fromkeys(("date", "code", *columns)))
            pieces = []
            for entry in entries:
                path = _validate_file(directory, entry)
                pieces.append(_drop_lockbox(pq.read_table(path, columns=cols), name).to_pandas())
            result[name] = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
            if not result[name].empty:
                if isinstance(result[name]["code"].dtype, pd.CategoricalDtype):
                    result[name]["code"] = result[name]["code"].astype(str)
                result[name] = (
                    result[name].sort_values(["date", "code"], kind="mergesort").reset_index(drop=True)
                )
        else:
            rel = _TABLE_PATHS[name]
            entry = next((item for item in files if item["path"] == rel), None)
            if entry is None:
                raise KeyError(f"快照清单缺少请求的表 {name!r}（文件 {rel}）")
            result[name] = _drop_lockbox(pq.read_table(_validate_file(directory, entry)), name).to_pandas()
    return result
