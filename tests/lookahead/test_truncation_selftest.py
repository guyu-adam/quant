"""P1-14 验收：5 个带未来函数的样例全部检出，5 个干净样例全部通过。"""

import numpy as np
import pandas as pd
import pytest

from q6.core.pit import PITView
from q6.lint.truncation_test import (
    LookaheadError,
    assert_no_lookahead,
    check_lookahead,
    pit_runner,
)

from .samples import CLEAN, LAG, LEAKY, make_data

DATA = make_data()


@pytest.mark.parametrize("name", sorted(LEAKY))
def test_leaky_detected(name):
    rep = check_lookahead(LEAKY[name], DATA, name=name, lag=LAG.get(name, 0))
    print(rep)
    assert not rep.passed, f"{name} 带未来函数却没被检出"
    assert rep.failures


@pytest.mark.parametrize("name", sorted(CLEAN))
def test_clean_passes(name):
    rep = assert_no_lookahead(CLEAN[name], DATA, name=name, lag=LAG.get(name, 0))
    assert rep.passed


@pytest.mark.parametrize("seed", range(5))
def test_leaky_detected_any_seed(seed):
    """检出不依赖于碰巧选中的切点。"""
    for name, f in LEAKY.items():
        assert not check_lookahead(f, DATA, lag=LAG.get(name, 0), seed=seed, n_cuts=3).passed, name


def test_nondeterministic_flagged():
    rng = np.random.default_rng(0)
    rep = check_lookahead(lambda d: d["close"] + rng.normal(size=d["close"].shape), DATA)
    assert rep.nondeterministic and not rep.passed


def test_output_must_be_time_aligned():
    with pytest.raises(TypeError):
        check_lookahead(lambda d: d["close"].mean(), DATA)


def test_assert_raises():
    with pytest.raises(LookaheadError):
        assert_no_lookahead(LEAKY["leaky_shift_negative"], DATA)


def test_ndarray_and_series_inputs():
    arr = DATA["close"].to_numpy()
    assert_no_lookahead(lambda a: np.cumsum(np.nan_to_num(a), axis=0), arr)
    assert not check_lookahead(lambda a: np.cumsum(np.nan_to_num(a)[::-1], axis=0)[::-1], arr).passed
    s = DATA["close"]["S000"]
    assert_no_lookahead(lambda x: x.rolling(5).sum(), s)


# ---------------- PITView 策略经适配器接入截断测试 ----------------

def _pit_momentum():
    def decide(v: PITView):
        w = v.window("close", 21)
        if len(w) < 21:
            return np.full(len(v.symbols), np.nan)
        r = w[-1] / w[0] - 1
        return pd.Series(r).rank(pct=True).to_numpy()
    return decide


def _pit_stateful_ewma():
    state = {"m": None}

    def decide(v: PITView):
        x = v.latest("close")
        state["m"] = x if state["m"] is None else np.where(np.isnan(x), state["m"], 0.9 * state["m"] + 0.1 * x)
        return state["m"].copy()
    return decide


def _pit_cheater():
    """绕过公开接口偷看底层面板（PITView 的结构防线挡不住，截断测试必须挡住）。"""
    def decide(v: PITView):
        arr = v._panel._fields["close"]
        return arr[min(v.t + 1, len(arr) - 1)]
    return decide


def test_pit_strategies():
    assert_no_lookahead(pit_runner(_pit_momentum), DATA)
    assert_no_lookahead(pit_runner(_pit_stateful_ewma), DATA, nan_probe=False)
    assert not check_lookahead(pit_runner(_pit_cheater), DATA).passed


def test_pit_field_lag_respected():
    """volume 声明滞后 3 根：策略即使用 latest 也只能拿到 t-3 的值，lag=0 测试应能证明其输出只依赖 ≤t-3。"""
    def mk():
        return lambda v: v.latest("volume")
    run = pit_runner(mk, field_lag={"volume": 3})
    out = run(DATA)
    np.testing.assert_array_equal(out.to_numpy()[3:], DATA["volume"].to_numpy()[:-3])
    assert np.isnan(out.to_numpy()[:3]).all()
