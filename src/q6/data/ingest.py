"""Baostock 日线增量抓取与落地。"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from q6.data.sources.baostock import BaostockClient, to_typed

LOG = logging.getLogger(__name__)


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    """将 parquet 写入同目录临时文件并原子替换目标文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        frame.to_parquet(temp_name, index=False)
        with open(temp_name, "rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def fetch_daily_many(
    codes: list[str], start: str, end: str, root: str | Path,
    client_factory: Callable[[], BaostockClient] = BaostockClient,
) -> dict[str, Exception]:
    root = Path(root)
    daily_dir = root / "daily"
    progress_path = root / "_progress.json"
    failures_path = root / "_failures.json"
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
    failures: dict[str, str] = {}
    with client_factory() as client:
        for code in codes:
            started = time.monotonic()
            destination = daily_dir / f"{code}.parquet"
            try:
                existing = pd.read_parquet(destination) if destination.exists() else pd.DataFrame()
                existing = to_typed(existing) if not existing.empty else existing
                record = progress.get(code, {})
                if (
                    destination.exists()
                    and record.get("end", "") >= end
                    and len(existing) == record.get("rows")
                ):
                    LOG.info("skip %s", code)
                    continue
                chunks = [existing] if not existing.empty else []
                fetched_count = 0
                if not existing.empty:
                    dates = pd.to_datetime(existing["date"])
                    first = dates.min().strftime("%Y-%m-%d")  # lookahead: ok 抓取区间判断，非信号
                    if start < first:
                        head_end = (pd.Timestamp(first) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                        head = to_typed(client.daily(code, start, head_end))
                        chunks.append(head)
                        fetched_count += len(head)
                    last = dates.max().strftime("%Y-%m-%d")  # lookahead: ok 抓取区间判断，非信号
                    if last < end:
                        tail_start = (pd.Timestamp(last) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                        tail = to_typed(client.daily(code, tail_start, end))
                        chunks.append(tail)
                        fetched_count += len(tail)
                else:
                    fresh = to_typed(client.daily(code, start, end))
                    chunks.append(fresh)
                    fetched_count += len(fresh)
                merged = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
                if not merged.empty:
                    merged = (
                        merged.drop_duplicates("date", keep="last")
                        .sort_values("date")
                        .reset_index(drop=True)
                    )
                _atomic_parquet(merged, destination)
                progress[code] = {
                    "start": start,
                    "end": end,
                    "rows": len(merged),
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
                _atomic_json(progress_path, progress)
                LOG.info("%s %d %.3fs", code, fetched_count, time.monotonic() - started)
            except Exception as exc:
                failures[code] = f"{type(exc).__name__}: {exc}"
                LOG.exception("%s failed", code)
                _atomic_json(failures_path, failures)
    if failures:
        LOG.error("failed %d/%d codes: %s", len(failures), len(codes), ", ".join(failures))
    elif failures_path.exists():
        failures_path.unlink()
    return {code: RuntimeError(message) for code, message in failures.items()}


def fetch_adjust_factor(
    code: str,
    start: str,
    end: str,
    root: str | Path,
    client_factory: Callable[[], BaostockClient] = BaostockClient,
) -> Path:
    """抓取并保存单只股票的 Baostock 复权因子。"""
    destination = Path(root) / "adjfactor" / f"{code}.parquet"
    with client_factory() as client:
        factors = client.adjust_factor(code, start, end)
    if "dividOperateDate" in factors:
        factors["dividOperateDate"] = pd.to_datetime(factors["dividOperateDate"], errors="coerce")
    for column in ("foreAdjustFactor", "backAdjustFactor", "adjustFactor"):
        if column in factors:
            factors[column] = pd.to_numeric(
                factors[column].replace("", pd.NA), errors="coerce"
            ).astype("float64")
    _atomic_parquet(factors, destination)
    return destination
