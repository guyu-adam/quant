"""Incremental Baostock daily bar ingestion."""

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
    for code in codes:
        started = time.monotonic()
        destination = daily_dir / f"{code}.parquet"
        try:
            existing = pd.read_parquet(destination) if destination.exists() else pd.DataFrame()
            existing = to_typed(existing) if not existing.empty else existing
            if not existing.empty and pd.to_datetime(existing["date"]).max().strftime("%Y-%m-%d") >= end:
                LOG.info("skip %s", code)
                continue
            tail_start = start
            if not existing.empty:
                next_date = pd.to_datetime(existing["date"]).max() + pd.Timedelta(days=1)
                tail_start = next_date.strftime("%Y-%m-%d")
            if tail_start <= end:
                with client_factory() as client:
                    fresh = to_typed(client.daily(code, tail_start, end))
            else:
                fresh = pd.DataFrame()
            merged = pd.concat([existing, fresh], ignore_index=True)
            if not merged.empty:
                merged = (
                    merged.drop_duplicates("date", keep="last")
                    .sort_values("date")
                    .reset_index(drop=True)
                )
                daily_dir.mkdir(parents=True, exist_ok=True)
                merged.to_parquet(destination, index=False)
            progress[code] = {
                "start": start,
                "end": end,
                "rows": len(merged),
                "fetched_at": datetime.now(UTC).isoformat(),
            }
            _atomic_json(progress_path, progress)
            LOG.info("%s %d %.3fs", code, len(fresh), time.monotonic() - started)
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
    """Fetch and persist one code's Baostock adjustment factor rows."""
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
    destination.parent.mkdir(parents=True, exist_ok=True)
    factors.to_parquet(destination, index=False)
    return destination
