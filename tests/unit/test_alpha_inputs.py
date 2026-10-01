import pandas as pd

from q6.alpha.inputs import build_inputs


def test_build_inputs_adjustments_and_masking():
    rows = []
    for code in "ABC":
        for day in range(6):
            close = 10 + day
            rows.append(
                dict(
                    date=f"2020-01-{day + 1:02}",
                    code=code,
                    open_hfq=close - 0.5,
                    high_hfq=close + 1,
                    low_hfq=close - 1,
                    close_hfq=close,
                    ret=0.1,
                    amount=1000.0,
                    turn=2.0,
                    volume=100.0,
                    adj_factor=2.0 if day >= 3 else 1.0,
                    tradestatus=0 if code == "A" and day == 2 else 1,
                    bad=bool(code == "B" and day == 4),
                )
            )
    d = build_inputs(pd.DataFrame(rows))
    assert d["volume"].loc["2020-01-04", "A"] == 50
    assert d["amount"].loc["2020-01-04", "A"] == 1000
    assert d["vwap"].loc["2020-01-04", "A"] == 20  # 1000 / 后复权股数 50；与 close_hfq 同口径
    assert d["float_cap"].loc["2020-01-04", "A"] == 50000
    for field in d:
        assert pd.isna(d[field].loc["2020-01-03", "A"])
        assert pd.isna(d[field].loc["2020-01-05", "B"])
