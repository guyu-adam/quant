from functools import partial

import numpy as np
import pandas as pd
import pytest

from q6.engine.event import EngineConfig
from q6.engine.feed import ENGINE_FIELDS
from q6.engine.matching import MatchConfig
from q6.engine.vector import VectorEngine, strategy_weights
from q6.research import metrics
from q6.research.sensitivity import Variant, grid, run_grid
from q6.strategy.base import StrategyBase, StrategySpec

A = "sz.000001"
DAYS = pd.bdate_range("2021-03-01", periods=8)


class FakeFeed:
    fields = ENGINE_FIELDS

    def __init__(self, delist=False):
        self.delist = delist

    def segments(self, start, end, warmup, keep=None):
        from q6.core.pit import Panel
        from q6.engine.feed import Segment

        close = pd.DataFrame({A: [10, 10, 10.2, 10.1, 10.4, 10.3, 10.5, 10.6]}, index=DAYS)
        pre = close.shift(1).fillna(close.iloc[0])
        frames = {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "preclose": pre,
            "volume": close * 0 + 1e7,
            "amount": close * 1e7,
            "tradestatus": close * 0 + 1,
            "is_st": close * 0,
            "is_new": close * 0,
            "adj_factor": close * 0 + 1,
            "ret": close / pre - 1,
            "bad": close * 0,
        }
        panel = Panel.from_frames(frames)
        if not self.delist:
            yield Segment(panel, 0, np.ones((len(panel), 1), dtype=bool))
            return
        p1 = Panel.from_frames({k: v.iloc[:4] for k, v in frames.items()})
        # Delisted symbols vanish from the next segment; the engine settles after two absent sessions.
        p2frames = {k: pd.DataFrame({"sz.000002": [20.0] * 4}, index=DAYS[4:]) for k in frames}
        p2frames["tradestatus"][:] = 1
        p2frames["adj_factor"][:] = 1
        p2frames["volume"][:] = 1e7
        p2frames["amount"][:] = 2e8
        p2frames["high"][:] = 20.2
        p2frames["low"][:] = 19.8
        p2frames["preclose"][:] = 20
        p2frames["ret"][:] = 0
        p2 = Panel.from_frames(p2frames)
        yield Segment(p1, 0, np.ones((len(p1), 1), dtype=bool))
        yield Segment(p2, 0, np.ones((len(p2), 1), dtype=bool))


def fake_feed_factory():
    return FakeFeed()


def delist_feed_factory():
    return FakeFeed(delist=True)


class Hold(StrategyBase):
    name = "hold"

    def __init__(self, n=1, lookback=1):
        self.n, self.lookback = n, lookback
        self.calls = 0

    @property
    def spec(self):
        return StrategySpec(fields=("close",), warmup=1, params={"n": self.n, "lookback": self.lookback})

    def on_bar(self, ctx):
        self.calls += 1
        return {A: 0.8}


def variants(cfgs):
    return [Variant(str(i), partial(Hold), cfg) for i, cfg in enumerate(cfgs)]


def run(cfgs, workers=1):
    return run_grid(
        variants(cfgs), "unused", "unused", DAYS[0], DAYS[-1], workers=workers, feed_factory=fake_feed_factory
    )


def test_grid_cartesian_product_and_field_placement():
    result = grid(EngineConfig(), cost_multiplier=[1, 2], delist_recovery=[0, 0.5], max_participation=[0.05])
    assert len(result) == 4
    assert {x.match.cost_multiplier for x in result} == {1, 2}
    assert {x.match.max_participation for x in result} == {0.05}
    assert {x.delist_recovery for x in result} == {0, 0.5}
    with pytest.raises(ValueError):
        grid(EngineConfig(), not_a_field=[1])


def test_higher_cost_multiplier_does_not_raise_equity():
    rows = run([EngineConfig(match=MatchConfig(cost_multiplier=x)) for x in (1, 2, 5)])
    assert rows.cagr.iloc[0] >= rows.cagr.iloc[1] >= rows.cagr.iloc[2]


def test_workers_one_and_two_preserve_variant_order_and_values():
    cfgs = [EngineConfig(), EngineConfig(match=MatchConfig(cost_multiplier=2)), EngineConfig()]
    one, two = run(cfgs, 1), run(cfgs, 2)
    pd.testing.assert_frame_equal(
        one.drop(columns="peak_rss_mb"), two.drop(columns="peak_rss_mb"), check_exact=True
    )


def test_default_variant_matches_direct_vector_engine():
    variant = Variant("default", partial(Hold), EngineConfig())
    result = run_grid([variant], "unused", "unused", DAYS[0], DAYS[-1], feed_factory=fake_feed_factory).iloc[
        0
    ]
    direct = VectorEngine().run(strategy_weights(Hold()), 1, FakeFeed(), DAYS[0], DAYS[-1])
    returns = direct.returns
    expected = (direct.daily.equity.iloc[-1] / direct.initial_cash) ** (244 / len(returns)) - 1
    assert result.cagr == expected
    assert result.ann_vol == returns.std(ddof=1) * 244**0.5
    assert result.sharpe == metrics.sharpe(returns, rf=0)
    assert result.max_drawdown == metrics.max_drawdown(returns)
    assert result.fees == direct.daily["fees"].sum()
    assert result.slippage_impact == direct.daily["cost_slip_imp"].sum()
    assert result.annual_one_way_turnover == (
        direct.daily["buy_value"].sum() / direct.daily["equity"].mean() / (len(returns) / 244)
    )
    assert result.fills == len(direct.fills)
    assert result.delistings == len(direct.delistings)


def test_recovery_zero_reduces_equity_when_delisting():
    cfgs = grid(EngineConfig(delist_after=2), delist_recovery=[0, 1])
    variants_ = variants(cfgs)
    rows = run_grid(variants_, "unused", "unused", DAYS[0], DAYS[-1], feed_factory=delist_feed_factory)
    assert rows.delistings.tolist() == [1, 1]
    assert rows.iloc[0].cagr < rows.iloc[1].cagr
