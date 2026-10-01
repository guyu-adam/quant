"""P1-10：清洗与复权。"""

import numpy as np
import pandas as pd
import pytest

from q6.data.adjust import (
    add_back_adjusted,
    back_factor,
    compare_vendor_factor,
    event_ratio,
    forward_adjusted,
)
from q6.data.clean import clean_daily
from q6.lint.truncation_test import assert_no_lookahead, check_lookahead


def _raw(code, dates, close, preclose, **kw):
    n = len(dates)
    close = np.asarray(close, float)
    d = dict(date=pd.to_datetime(dates), code=code, open=close, high=close * 1.01, low=close * 0.99,
             close=close, preclose=np.asarray(preclose, float), volume=np.full(n, 1000),
             amount=close * 1000, tradestatus=np.ones(n, int), isST=np.zeros(n, int))
    d.update(kw)
    return pd.DataFrame(d)


def test_ten_for_ten_bonus_continuous():
    """10 送 10：除权日 preclose 减半。后复权收盘价必须连续，收益 = 真实涨幅。"""
    df = _raw("A", ["2020-06-01", "2020-06-02", "2020-06-03", "2020-06-04"],
              close=[20.0, 20.0, 10.5, 10.5], preclose=[19.8, 20.0, 10.0, 10.5])
    out = add_back_adjusted(df)
    np.testing.assert_allclose(out["adj_factor"], [1, 1, 2, 2])
    np.testing.assert_allclose(out["close_hfq"], [20, 20, 21, 21])
    assert out["close_hfq"].pct_change().iloc[2] == pytest.approx(0.05)
    # 原始价格列不被改动（涨跌停用不复权价）
    np.testing.assert_array_equal(out["close"], df["close"])


def test_cash_dividend_and_rights():
    """现金分红 0.5 元：preclose = 10 - 0.5。复权收益 = 含息收益。"""
    df = _raw("A", ["2021-01-04", "2021-01-05"], close=[10.0, 9.5], preclose=[10.0, 9.5])
    out = add_back_adjusted(df)
    assert out["close_hfq"].pct_change().iloc[1] == pytest.approx(0.0)  # 除息日不涨不跌 = 持有收益 0
    r = clean_daily(df).data["ret"]
    assert r.iloc[1] == pytest.approx(0.0)


def test_event_during_suspension():
    """除权落在停牌期间：复牌日 preclose 是除权参考价，比例在复牌日体现。"""
    df = _raw("A", ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"],
              close=[10.0, 10.0, 10.0, 5.2], preclose=[10.0, 10.0, 10.0, 5.0],
              tradestatus=[1, 0, 0, 1], volume=[1000, 0, 0, 1000])
    res = clean_daily(df)
    d = res.data
    np.testing.assert_allclose(d["adj_factor"], [1, 1, 1, 2])
    assert d["ret"].iloc[3] == pytest.approx(0.04)
    assert d["tradable"].tolist() == [True, False, False, True]
    assert d["ret"].iloc[1:3].isna().all()  # 停牌日没有成交价
    assert res.quarantine.empty


def test_rounding_noise_not_accumulated():
    c = np.full(500, 10.0)
    pc = c * (1 + 1e-9)
    assert (event_ratio(c, pc) == 1.0).all()


def test_quarantine_flags_not_fixes():
    ten = np.full(10, 10.0)
    df = _raw("A", pd.bdate_range("2020-01-01", periods=10), close=ten, preclose=ten)
    df.loc[3, "high"] = 9.0  # high < low
    df.loc[7:, ["open", "close", "high", "low"]] = [15.0, 15.0, 15.15, 14.85]  # 第 7 行无复牌无上市的 +50%
    df.loc[8:, "preclose"] = 15.0
    res = clean_daily(df)
    q = res.quarantine
    assert len(q) == 2
    assert "high<low" in q.loc[q.date == df.date[3], "reason"].item()
    assert "涨跌幅" in q.loc[q.date == df.date[7], "reason"].item()
    d = res.data
    assert not d.loc[3, "tradable"] and d.loc[3, "bad"]
    assert d.loc[3, "high"] == 9.0  # 不静默修正
    assert d["tradable"].sum() == 8


def test_new_stock_and_st_flags():
    df = _raw("A", pd.bdate_range("2020-01-01", periods=70), close=np.full(70, 10.0),
              preclose=np.full(70, 10.0), isST=[0] * 69 + [1])
    d = clean_daily(df, new_stock_days=60).data
    assert d["is_new"].sum() == 60 and d["is_st"].iloc[-1]
    # 上市前 5 日豁免涨跌幅检查（注册制新股前 5 日不设限）
    df2 = _raw("B", pd.bdate_range("2021-01-01", periods=3),
               close=[30.0, 40.0, 41.0], preclose=[10.0, 30.0, 40.0])
    assert clean_daily(df2).quarantine.empty


def test_duplicates_rejected():
    df = _raw("A", ["2020-01-02", "2020-01-02"], close=[1, 1], preclose=[1, 1])
    with pytest.raises(ValueError, match="重复"):
        clean_daily(df)


def test_multi_code_unsorted_input():
    a = _raw("A", ["2020-01-02", "2020-01-03"], close=[10, 5], preclose=[10, 5])
    b = _raw("B", ["2020-01-02", "2020-01-03"], close=[7, 7], preclose=[7, 7])
    d = clean_daily(pd.concat([b, a]).sample(frac=1, random_state=0)).data
    assert d["code"].tolist() == ["A", "A", "B", "B"]
    np.testing.assert_allclose(d["adj_factor"], [1, 2, 1, 1])


def test_vendor_factor_comparison():
    df = add_back_adjusted(_raw("A", ["2020-06-01", "2020-06-02", "2020-06-03"],
                                close=[20, 10.5, 10.5], preclose=[20, 10, 10.5]))
    good = pd.DataFrame({"code": "A", "date": pd.to_datetime(["2020-05-01", "2020-06-02"]),
                         "vendor_factor": [3.0, 6.0]})
    assert compare_vendor_factor(df, good).empty
    bad = good.assign(vendor_factor=[3.0, 5.0])
    assert len(compare_vendor_factor(df, bad)) == 2


# ---------- 后复权 PIT 安全、前复权（锚定末日）不安全：用截断测试证明 ----------

def _wide():
    rng = np.random.default_rng(3)
    T, N = 200, 4
    idx = pd.bdate_range("2018-01-01", periods=T)
    close = 10 * np.exp(np.cumsum(rng.normal(0, 0.02, (T, N)), axis=0))
    pre = np.vstack([close[:1], close[:-1]])
    ev = rng.random((T, N)) < 0.02  # 随机除权：preclose 打折
    pre = np.where(ev, pre * rng.uniform(0.5, 0.95, (T, N)), pre)
    return {"close": pd.DataFrame(close, idx), "preclose": pd.DataFrame(pre, idx)}


def _hfq(d):
    c, p = d["close"].to_numpy(), d["preclose"].to_numpy()
    return np.column_stack([c[:, j] * back_factor(c[:, j], p[:, j]) for j in range(c.shape[1])])


def _qfq_anchor_last(d):
    f = _hfq(d) / d["close"].to_numpy()
    return d["close"].to_numpy() * f / f[-1]


def test_back_adjust_is_pit_safe():
    assert_no_lookahead(_hfq, _wide(), nan_probe=False)


def test_forward_adjust_anchored_at_end_leaks():
    assert not check_lookahead(_qfq_anchor_last, _wide(), nan_probe=False).passed


def test_forward_adjusted_anchor_explicit():
    df = add_back_adjusted(_raw("A", ["2020-06-01", "2020-06-02", "2020-06-03"],
                                close=[20, 10.5, 10.5], preclose=[20, 10, 10.5]))
    q = forward_adjusted(df, "2020-06-03")
    np.testing.assert_allclose(q["close_qfq"], [10, 10.5, 10.5])
