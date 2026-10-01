"""P1-13 验收：任意游标下，PITView 返回数据的最大时间戳 ≤ 游标；改动游标之后的数据不影响任何返回值。"""

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from q6.core.pit import Panel, PITView, iter_views


def _panel(T, N, seed, lag=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2010-01-04", periods=T)
    f = {"close": rng.normal(size=(T, N)), "eps": rng.normal(size=(T, N))}
    return Panel(idx, [f"S{i}" for i in range(N)], f, field_lag={"eps": lag})


@settings(max_examples=200, deadline=None)
@given(T=st.integers(1, 60), N=st.integers(1, 6), seed=st.integers(0, 10_000),
       lag=st.integers(0, 5), lookback=st.one_of(st.none(), st.integers(1, 80)), data=st.data())
def test_never_returns_future(T, N, seed, lag, lookback, data):
    p = _panel(T, N, seed, lag)
    t = data.draw(st.integers(0, T - 1))
    v = p.view(t)
    for name, k in (("close", 0), ("eps", lag)):
        d = v.dates(lookback, name)
        w = v.window(name, lookback)
        assert len(d) == len(w)
        if len(d):
            assert d.max() <= v.now
            assert d.max() == p.date_at(t - k)  # 恰好到 t-lag，不多不少
        else:
            assert t - k < 0
        np.testing.assert_array_equal(w, p._fields[name][max(0, t + 1 - k - (lookback or 10**9)): max(0, t + 1 - k)])
        lt = v.latest(name)
        if t - k >= 0:
            np.testing.assert_array_equal(lt, p._fields[name][t - k])
        else:
            assert np.isnan(lt).all()


@settings(max_examples=100, deadline=None)
@given(T=st.integers(2, 50), N=st.integers(1, 5), seed=st.integers(0, 10_000), data=st.data())
def test_future_mutation_invisible(T, N, seed, data):
    """构造两个只在 t 之后不同的面板，视图在 t 处的一切返回值逐位相同。"""
    t = data.draw(st.integers(0, T - 2))
    p1 = _panel(T, N, seed)
    f2 = {k: p1._fields[k].copy() for k in p1.field_names}
    for a in f2.values():
        a[t + 1:] = 1e9
    p2 = Panel(pd.DatetimeIndex(p1._dates), p1.symbols, f2)
    v1, v2 = p1.view(t), p2.view(t)
    for name in p1.field_names:
        np.testing.assert_array_equal(v1.window(name), v2.window(name))
        np.testing.assert_array_equal(v1.latest(name), v2.latest(name))
        pd.testing.assert_frame_equal(v1.frame(name, 7), v2.frame(name, 7))


def test_returns_are_copies_not_views():
    p = _panel(20, 3, 0)
    v = p.view(5)
    w = v.window("close", 3)
    assert w.base is None or w.base.shape[0] <= 3  # 拿不到整块底层数组
    w[:] = 0
    assert not np.all(p.view(5).window("close", 3) == 0)
    with pytest.raises(ValueError):
        p._fields["close"][0, 0] = 1.0  # 底层只读


def test_panel_validation():
    idx = pd.bdate_range("2020-01-01", periods=3)
    with pytest.raises(ValueError):
        Panel(idx[::-1], ["A"], {"x": np.zeros((3, 1))})
    with pytest.raises(ValueError):
        Panel(idx, ["A", "A"], {"x": np.zeros((3, 2))})
    with pytest.raises(ValueError):
        Panel(idx, ["A"], {"x": np.zeros((2, 1))})
    with pytest.raises(KeyError):
        Panel(idx, ["A"], {"x": np.zeros((3, 1))}, field_lag={"y": 1})
    with pytest.raises(IndexError):
        Panel(idx, ["A"], {"x": np.zeros((3, 1))}).view(3)


def test_source_array_mutation_isolated():
    idx = pd.bdate_range("2020-01-01", periods=3)
    src = np.zeros((3, 1))
    p = Panel(idx, ["A"], {"x": src})
    src[:] = 99
    assert p.view(2).window("x").sum() == 0


def test_iter_views_and_index_of():
    p = _panel(10, 2, 1)
    ts = [v.t for v in iter_views(p, 3)]
    assert ts == list(range(3, 10))
    assert p.index_of(p.date_at(4)) == 4
    assert p.index_of(pd.Timestamp(p.date_at(4)) + pd.Timedelta(hours=5)) == 4
    with pytest.raises(KeyError):
        p.index_of("1999-01-01")
