from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from q6.core.pit import Panel
from q6.engine.event import EngineConfig, EventEngine
from q6.engine.feed import ENGINE_FIELDS, Segment
from q6.engine.matching import MatchConfig
from q6.strategy.base import BarContext, StrategyBase
from q6.strategy.benchmarks import BuyAndHold, UniverseEqualWeight

A, B, C, D = "sh.600000", "sz.000001", "sh.600001", "sz.000002"


def context(date="2021-03-01"):
    dates = pd.DatetimeIndex([pd.Timestamp(date)])
    symbols = (A, B, C, D)
    fields = {
        "close": np.array([[10.0, 20.0, np.nan, 40.0]]),
        "tradestatus": np.array([[1.0, 0.0, 1.0, 1.0]]),
        "is_st": np.array([[0.0, 0.0, 0.0, 1.0]]),
        "is_new": np.array([[0.0, 0.0, 1.0, 0.0]]),
    }
    view = Panel(dates, symbols, fields).view(0)
    return BarContext(view, None, (A, B, C, D))


def test_monthly_rebalance_only_on_month_change():
    strategy = UniverseEqualWeight()
    assert strategy.on_bar(context("2021-03-01")) is not None
    assert strategy.on_bar(context("2021-03-31")) is None
    assert strategy.on_bar(context("2021-04-01")) is not None


def test_universe_equal_weight_and_gross_sum():
    weights = UniverseEqualWeight(gross=0.8).on_bar(context())
    assert weights == {A: pytest.approx(0.8)}
    assert sum(weights.values()) == pytest.approx(0.8)


def test_filters_status_st_new_universe_and_nonfinite_close():
    strategy = UniverseEqualWeight()
    ctx = context()
    restricted = BarContext(ctx.view, None, (B, C, D))
    assert strategy.on_bar(restricted) == {}


def test_buy_and_hold_initializes_once_and_keeps_basket():
    strategy = BuyAndHold(gross=0.6)
    assert strategy.on_bar(context()) == {A: pytest.approx(0.6)}
    assert strategy.on_bar(context("2021-04-01")) is None


def test_buy_and_hold_waits_until_universe_has_eligible_names():
    strategy = BuyAndHold()
    ctx = context()
    assert strategy.on_bar(BarContext(ctx.view, None, ())) == {}
    assert strategy.on_bar(ctx) == {A: pytest.approx(0.95)}


def engine_frames():
    dates = pd.bdate_range("2021-03-29", periods=8)
    close = pd.DataFrame(
        {A: [10.0, 10.0, 10.0, 11.0, 11.0, 11.0, 11.0, 11.0],
         B: [20.0, 20.0, 20.0, 19.0, 19.0, 19.0, 19.0, 19.0]},
        index=dates,
    )
    preclose = close.shift().fillna(close.iloc[0])
    fields = {
        "open": close.copy(), "high": close * 1.005, "low": close * 0.995,
        "close": close, "preclose": preclose, "volume": close * 0 + 1e7,
        "amount": close * 1e7, "tradestatus": close * 0 + 1, "is_st": close * 0,
        "is_new": close * 0, "adj_factor": close * 0 + 1,
        "ret": close / preclose - 1, "bad": close * 0,
    }
    return dates, fields


class FakeFeed:
    fields = ENGINE_FIELDS

    def __init__(self, dates, frames):
        self.panel = Panel.from_frames(frames)
        self.dates = dates

    def segments(self, start, end, warmup, keep=None):
        yield Segment(self.panel, 2, np.ones((len(self.dates), 2), dtype=bool))


def test_fakefeed_event_engine_runs_across_month_with_fills_and_finite_equity():
    dates, frames = engine_frames()
    strategy = UniverseEqualWeight()
    cfg = EngineConfig(match=MatchConfig(slippage_bp=0, impact_coef=0, commission_min=0))
    result = EventEngine(cfg).run(strategy, FakeFeed(dates, frames), dates[0], dates[-1])
    assert len(result.daily) == len(dates) - 2
    assert result.fills
    assert np.isfinite(result.daily["equity"]).all()
    assert sum(result.daily.loc[result.daily.index.month == 4, "buy_value"]) > 0


class RecordingStrategy(StrategyBase):
    name = "recorder"

    def __init__(self, delegate):
        self.delegate = delegate
        self.targets = []

    @property
    def spec(self):
        return self.delegate.spec

    def on_bar(self, ctx):
        target = self.delegate.on_bar(ctx)
        self.targets.append(target)
        return target


def test_buy_and_hold_engine_creates_only_initial_target():
    dates, frames = engine_frames()
    strategy = RecordingStrategy(BuyAndHold())
    cfg = EngineConfig(match=MatchConfig(slippage_bp=0, impact_coef=0, commission_min=0))
    result = EventEngine(cfg).run(strategy, FakeFeed(dates, frames), dates[0], dates[-1])
    assert strategy.targets.count(None) == len(dates) - 3
    assert result.fills
