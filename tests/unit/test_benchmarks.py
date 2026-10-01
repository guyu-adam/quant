from __future__ import annotations

import pandas as pd
import pytest

from q6.research.benchmark import buy_and_hold, index_returns


def test_index_returns_uses_preclose() -> None:
    frame = pd.DataFrame(
        {"code": ["sh.000300"] * 3, "date": pd.date_range("2020-01-01", periods=3),
         "close": [10.0, 12.0, 9.0], "preclose": [9.0, 10.0, 12.0]}
    )
    result = index_returns(frame, "sh.000300")
    assert result.tolist() == pytest.approx([1 / 9, 0.2, -0.25])


def test_buy_and_hold_three_days_two_assets() -> None:
    close = pd.DataFrame({"A": [100.0, 110.0, 121.0], "B": [100.0, 90.0, 99.0]})
    result = buy_and_hold(close, pd.Series({"A": 0.5, "B": 0.5}))
    assert result.tolist() == pytest.approx([0.0, 0.0, 0.1])


def test_load_benchmarks_drops_lockbox_rows() -> None:
    import pyarrow.parquet as pq

    from q6.data.benchmarks import load_benchmarks
    from q6.data.lockbox import LOCKBOX_TS, REPO_ROOT

    raw = pq.read_table(REPO_ROOT / "data/benchmarks/index_daily.parquet").to_pandas()
    assert raw["date"].max() >= LOCKBOX_TS  # 入库文件本身含锁箱期，守卫必须在加载层起作用
    d = load_benchmarks()
    assert len(d) and d["date"].max() < LOCKBOX_TS
    assert set(d["code"]) == {"sh.000001", "sh.000300", "sh.000905", "sh.000906"}


def test_load_benchmarks_rejects_tampered_file(tmp_path) -> None:
    import shutil

    from q6.data.benchmarks import load_benchmarks
    from q6.data.lockbox import REPO_ROOT
    from q6.data.snapshot import SnapshotIntegrityError

    src = REPO_ROOT / "data/benchmarks"
    for name in ("manifest.json", "index_daily.parquet"):
        shutil.copy(src / name, tmp_path / name)
    blob = bytearray((tmp_path / "index_daily.parquet").read_bytes())
    blob[len(blob) // 2] ^= 0xFF
    (tmp_path / "index_daily.parquet").write_bytes(bytes(blob))
    with pytest.raises(SnapshotIntegrityError):
        load_benchmarks(tmp_path)
