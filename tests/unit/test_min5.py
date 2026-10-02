from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from q6.data.lockbox import LockboxError
from q6.data.min5 import load_min5


def test_load_min5_rejects_lockbox_day(tmp_path: Path) -> None:
    with pytest.raises(LockboxError):
        load_min5(tmp_path, "2024-07-01", ["sh.600000"])


def test_load_min5_filters_day_and_codes(tmp_path: Path) -> None:
    path = tmp_path / "2024" / "06.parquet"
    path.parent.mkdir(parents=True)
    frame = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-06-27").date()] * 3 + [pd.Timestamp("2024-06-28").date()],
            "time": ["093500", "094000", "093500", "093500"],
            "code": ["sh.600000", "sh.600000", "sz.000001", "sh.600000"],
            "open": [1.0, 2.0, 3.0, 4.0],
            "high": [1.1, 2.1, 3.1, 4.1],
            "low": [0.9, 1.9, 2.9, 3.9],
            "close": [1.0, 2.0, 3.0, 4.0],
            "volume": np.array([10, 20, 30, 40], dtype="int64"),
            "amount": [100.0, 200.0, 300.0, 400.0],
        }
    )
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), path)
    result = load_min5(tmp_path, "2024-06-27", ["sh.600000"])
    assert list(result) == ["sh.600000"]
    assert result["sh.600000"]["time"].tolist() == ["093500", "094000"]
    assert result["sh.600000"]["volume"].dtype == np.int64
    assert result["sh.600000"]["volume"].sum() == 30


def test_real_min5_volumes_match_snapshot_daily_when_available() -> None:
    repo = Path(__file__).resolve().parents[2]
    root = repo / "data/min5"
    files = sorted(root.glob("*/*.parquet"))
    if not files:
        pytest.skip("real minute data has not been fetched")
    minute = pd.concat([pd.read_parquet(p) for p in files], ignore_index=True)
    minute["date"] = pd.to_datetime(minute["date"])
    candidates = minute.groupby(["code", "date"], sort=True)["volume"].sum().reset_index()
    daily_path = Path("/Users/guyu/Desktop/guyu-adam/quant/data/snapshots/6252e931a86bda15")
    from q6.data.snapshot import load_snapshot

    daily = load_snapshot(daily_path.parent, "6252e931a86bda15", tables=("daily",))["daily"]
    daily["date"] = pd.to_datetime(daily["date"])
    joined = candidates.merge(
        daily[["code", "date", "volume"]], on=["code", "date"], suffixes=("_min5", "_daily")
    )
    assert not joined.empty
    selected = []
    sample_days = pd.to_datetime(["2023-01-05", "2023-01-06", "2023-01-09"])
    for code in sorted(joined["code"].unique())[:5]:
        stock_days = joined[joined["code"] == code]
        selected.append(stock_days[stock_days["date"].isin(sample_days)])
    sample = pd.concat(selected, ignore_index=True)
    assert len(sample) == 15, f"expected 5 codes × 3 days; found {len(sample)} rows"
    relative = (sample["volume_min5"] - sample["volume_daily"]).abs() / sample["volume_daily"].clip(lower=1)
    # 10-code × 5-day probe measured a 2.28290753569286e-8 maximum relative difference.
    measured_max = 2.28290753569286e-8
    assert float(relative.max()) <= measured_max, f"maximum relative volume difference={relative.max():.14g}"
