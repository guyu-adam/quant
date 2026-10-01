"""P1-10 真实数据回归：贵州茅台 2015-01 ~ 2024-06（baostock 录制，tests/fixtures/baostock/）。

自推复权因子（只用 close / preclose）必须与 baostock 的因子表、后复权价一致。
"""

from pathlib import Path

import pandas as pd

from q6.data.adjust import compare_vendor_factor
from q6.data.clean import clean_daily

FX = Path(__file__).resolve().parents[1] / "fixtures" / "baostock"


def test_own_factor_matches_baostock_moutai():
    raw = pd.read_csv(FX / "sh.600519_daily_raw.csv", dtype={"code": str})
    res = clean_daily(raw)
    d = res.data
    assert res.quarantine.empty
    af = pd.read_csv(FX / "sh.600519_adjust_factor.csv")
    v = pd.DataFrame({"code": af["code"], "date": pd.to_datetime(af["dividOperateDate"]),
                      "vendor_factor": af["backAdjustFactor"].astype(float)})
    assert len(v) >= 10  # 期间每年分红一次
    assert compare_vendor_factor(d[["code", "date", "adj_factor"]], v, rtol=1e-4).empty

    hfq = pd.read_csv(FX / "sh.600519_close_hfq.csv", parse_dates=["date"]).set_index("date")["close"]
    diff = (d.set_index("date")["close_hfq"].pct_change() - hfq.pct_change()).abs().dropna()
    assert len(diff) > 2000
    assert diff.max() < 1e-6

    # 2020-06-24 除息（每股 17.025 元）：不复权价跌、复权收益为正 0.17%
    row = d.loc[d["date"] == "2020-06-24"].iloc[0]
    assert row["close"] < 1474.5 and abs(row["ret"] - (1460.01 / 1457.48 - 1)) < 1e-12
