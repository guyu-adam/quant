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
    """真数据：5 只 × 3 天，5 分钟量之和 = 快照日线量。只读 2023-01 那一个月（全量读入会超 512MB）。

    快照按 Q6_SNAPSHOT / Q6_SNAPSHOT_ROOT 找（同 tests/lookahead）；
    5 分钟数据在 Q6_MIN5_ROOT（默认 data/min5）。
    验收模式下缺数据不许 skip。"""
    import os

    from q6.data.snapshot import load_snapshot

    snap = os.environ.get("Q6_SNAPSHOT")
    root = Path(os.environ.get("Q6_MIN5_ROOT", "data/min5"))
    missing = [] if snap else ["Q6_SNAPSHOT 未设置"]
    if not (root / "2023" / "01.parquet").is_file():
        missing.append(f"{root}/2023/01.parquet 不存在")
    if missing:
        if os.environ.get("Q6_REQUIRE_SNAPSHOT") == "1":
            pytest.fail("验收模式不允许跳过：" + "；".join(missing))
        pytest.skip("；".join(missing))
    days = ["2023-01-05", "2023-01-06", "2023-01-09"]
    codes = sorted(set(pd.read_parquet(root / "2023" / "01.parquet", columns=["code"])["code"]))[:5]
    daily = load_snapshot(Path(os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots")), snap, ("daily",),
                          date_range=(days[0], days[-1]), columns=("volume",), codes=codes)["daily"]
    daily = daily.set_index([daily["code"], pd.to_datetime(daily["date"]).dt.strftime("%Y-%m-%d")])["volume"]
    rel = []
    for day in days:
        bars = load_min5(root, day, codes)
        for code in codes:
            d = float(daily[(code, day)])
            rel.append(abs(float(bars[code]["volume"].sum()) - d) / max(d, 1.0))
    assert len(rel) == 15
    # 10 只 × 5 天探查实测最大相对差 2.28290753569286e-8（P3-12 报告），不放宽
    assert max(rel) <= 2.28290753569286e-8, f"maximum relative volume difference={max(rel):.14g}"
