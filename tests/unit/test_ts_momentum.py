import numpy as np
import pandas as pd
import pytest

from q6.core.pit import Panel
from q6.engine.event import EngineConfig, EventEngine
from q6.engine.feed import ENGINE_FIELDS, Segment
from q6.engine.matching import MatchConfig
from q6.strategy.base import BarContext
from q6.strategy.ts_momentum import TSMomentum

A, B, C = "sz.000001", "sh.600000", "sz.000002"


def context(prices, *, universe=(A, B, C), trade=None, st=None, new=None, symbols=(A, B, C)):
    dates = pd.bdate_range("2020-01-01", periods=len(prices))
    close_hfq = pd.DataFrame(np.asarray(prices, dtype=float), index=dates, columns=symbols)
    fields = {
        "close_hfq": close_hfq,
        "close": close_hfq,
        "open": close_hfq,
        "high": close_hfq * 1.001,
        "low": close_hfq * 0.999,
        "preclose": close_hfq.shift(1).fillna(close_hfq),
        "volume": close_hfq * 0 + 1e7,
        "amount": close_hfq * 1e7,
        "tradestatus": close_hfq * 0 + 1,
        "is_st": close_hfq * 0,
        "is_new": close_hfq * 0,
        "adj_factor": close_hfq * 0 + 1,
        "ret": close_hfq.pct_change().fillna(0),
        "bad": close_hfq * 0,
    }
    if trade is not None:
        fields["tradestatus"].iloc[-1] = trade
    if st is not None:
        fields["is_st"].iloc[-1] = st
    if new is not None:
        fields["is_new"].iloc[-1] = new
    view = Panel.from_frames(fields).view(len(dates) - 1)
    return BarContext(view, None, tuple(universe))


def test_positive_ts_momentum_weights_inverse_volatility_and_cap():
    # Last 4 bars provide a positive prior return and distinct, finite volatilities.
    prices = [[10 + i * 0.1, 20 + i * 0.2 + (i % 2) * 0.1, 30 - i * 0.1] for i in range(6)]
    s = TSMomentum(lookback=4, skip=1, vol_window=4, every=1, cap=0.4, gross=0.8, vol_target=99)
    got = s.on_bar(context(prices))
    assert set(got) == {A, B}
    px = np.asarray(prices, dtype=float)[-5:, :2]
    returns = px[1:] / px[:-1] - 1
    inv = 1 / returns.std(axis=0, ddof=1)
    expected = np.minimum(inv / inv.sum() * 0.8, 0.4)
    assert got[A] == pytest.approx(expected[0])
    assert got[B] == pytest.approx(expected[1])
    assert sum(got.values()) <= 0.8


def test_cadence_first_call_then_none():
    s = TSMomentum(lookback=4, skip=1, vol_window=4, every=2, cap=0.5, gross=0.8)
    ctx = context([[10+i, 20+i, 30-i] for i in range(6)])
    assert s.on_bar(ctx) is not None
    assert s.on_bar(ctx) is None
    assert s.on_bar(ctx) is not None


@pytest.mark.parametrize("kwargs", [{"trade": 0}, {"st": 1}, {"new": 1}, {"universe": ()}])
def test_all_ineligible_returns_empty(kwargs):
    s = TSMomentum(lookback=4, skip=1, vol_window=4, every=1, cap=0.5, gross=0.8)
    result = s.on_bar(context([[10+i, 20+i, 30+i] for i in range(6)], **kwargs))
    assert result == {}


def test_nonpositive_momentum_and_missing_window_are_excluded():
    px = np.asarray([[10+i, 20+i, 30+i] for i in range(6)], dtype=float)
    px[-2, 0] = 1  # negative momentum at t-skip
    px[-2, 1] = np.nan
    px[-2, 2] = 1  # negative momentum at t-skip  # incomplete history
    result = TSMomentum(lookback=4, skip=1, vol_window=4, every=1, cap=0.5, gross=0.8).on_bar(context(px))
    assert result == {}


def test_max_names_keeps_largest_positive_momentum():
    noise = [1, 1.01, 0.99, 1.02, 0.98, 1.03]
    prices = [[10 * 1.01**i * noise[i], 20 * 1.04**i * noise[i],
               30 * 1.02**i * noise[i]] for i in range(6)]
    result = TSMomentum(lookback=4, skip=1, vol_window=4, every=1, cap=0.5,
                        gross=0.8, vol_target=99, max_names=2).on_bar(context(prices))
    assert set(result) == {B, C}


def test_max_names_ties_use_symbol_ascending():
    # C、B 动量完全相同，输入列顺序相反于要求的并列顺序。
    noise = [1, 0.95, 0.98, 1.01, 1.10, 1.12]
    tied = [10 * 1.01**i * noise[i] for i in range(6)]
    prices = [[tied[i], tied[i], 30 * 1.005**i * noise[i]] for i in range(6)]
    ctx = context(prices, symbols=(C, B, A))
    result = TSMomentum(lookback=4, skip=1, vol_window=4, every=1, cap=0.5,
                        gross=0.8, vol_target=99, max_names=2).on_bar(ctx)
    assert set(result) == {B, C}


@pytest.mark.parametrize("max_names", [0, -1, 1.5, True])
def test_max_names_requires_positive_integer(max_names):
    with pytest.raises(ValueError):
        TSMomentum(max_names=max_names)


class Feed:
    fields = (*ENGINE_FIELDS, "close_hfq")

    def __init__(self, frames):
        self.panel = Panel.from_frames(frames)

    def segments(self, start, end, warmup, keep=None):
        yield Segment(self.panel, warmup, np.ones((len(self.panel), 3), dtype=bool))


def test_runs_through_event_engine_with_fills_and_finite_equity():
    dates = pd.bdate_range("2021-01-01", periods=90)
    rng = np.random.default_rng(4)
    prices = np.round(20 * np.exp(np.cumsum(rng.normal(0.001, 0.006, (90, 3)), axis=0)), 2)
    close = pd.DataFrame(prices, index=dates, columns=[A, B, C])
    pre = close.shift().fillna(close)
    frames = {
        "open": close, "high": close * 1.002, "low": close * 0.998, "close": close, "preclose": pre,
        "volume": close * 0 + 1e7, "amount": close * 1e7, "tradestatus": close * 0 + 1,
        "is_st": close * 0, "is_new": close * 0, "adj_factor": close * 0 + 1,
        "ret": close / pre - 1, "bad": close * 0,
        "close_hfq": close,
    }
    strat = TSMomentum(lookback=10, skip=2, vol_window=8, every=5, cap=0.5, gross=0.8, vol_target=9)
    res = EventEngine(EngineConfig(match=MatchConfig(slippage_bp=0, impact_coef=0, commission_min=0))).run(
        strat, Feed(frames), dates[10], dates[-1]
    )
    assert res.fills
    assert np.isfinite(res.daily["equity"]).all()
