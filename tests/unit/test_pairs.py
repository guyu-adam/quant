import numpy as np
import pandas as pd

from q6.core.pit import Panel
from q6.core.types import AccountSnapshot
from q6.engine.event import EngineConfig, EventEngine
from q6.engine.feed import ENGINE_FIELDS, Segment
from q6.engine.matching import MatchConfig
from q6.strategy.base import BarContext
from q6.strategy.pairs import PairsDistance

SYMS = ("sz.000001", "sh.600000", "sz.000002", "sh.600001")


def make_context(t=70, changed=False):
    dates = pd.bdate_range("2015-01-05", periods=t)
    x = np.linspace(0, 0.03, t)
    prices = np.column_stack([10 * (1 + x), 10 * (1 + x * 1.001),
                              10 * (1 + x * 2), 10 * (1 - x)])
    if changed and t > 55:
        prices[55:] = np.arange(1, t - 54)[:, None] * np.array([3, 1, 4, 2])[None, :]
    fields = {"close_hfq": prices, "amount": np.full_like(prices, 1e6),
              "tradestatus": np.ones_like(prices), "is_st": np.zeros_like(prices),
              "is_new": np.zeros_like(prices)}
    panel = Panel(dates, SYMS, fields)
    view = panel.view(t - 1)
    return BarContext(view, AccountSnapshot(view.now.to_pydatetime(), 1e6), SYMS)


def make_code_context(symbols, prices, day):
    dates = pd.DatetimeIndex([pd.Timestamp(day)])
    values = np.asarray([prices[s] for s in symbols], dtype=float)[None, :]
    fields = {"close_hfq": values, "amount": np.full_like(values, 1e6),
              "tradestatus": np.ones_like(values), "is_st": np.zeros_like(values),
              "is_new": np.zeros_like(values)}
    view = Panel(dates, symbols, fields).view(0)
    return BarContext(view, AccountSnapshot(view.now.to_pydatetime(), 1e6), tuple(symbols))


def test_forms_nonoverlapping_pairs_and_frozen_stats():
    s = PairsDistance(formation=50, trading=10, n_pairs=2, pool=4)
    s.on_bar(make_context())
    assert len(s._pairs) == 2
    legs = [leg for pair in s._pairs for leg in pair[:2]]
    assert len(legs) == len(set(legs))
    assert all(np.isfinite(pair[2:]).all() if isinstance(pair[2:], np.ndarray) else
               np.isfinite(pair[2]) and np.isfinite(pair[3]) for pair in s._pairs)


def test_all_ineligible_returns_empty():
    s = PairsDistance(formation=50, trading=10, n_pairs=2, pool=4)
    ctx = make_context()
    # Formation has valid data; subsequent bar excludes every symbol from universe.
    s.on_bar(ctx)
    empty = BarContext(ctx.view, ctx.account, ())
    assert s.on_bar(empty) == {}


def test_target_is_long_only_and_bounded():
    s = PairsDistance(formation=50, trading=10, n_pairs=2, pool=4)
    got = s.on_bar(make_context())
    assert all(weight >= 0 for weight in got.values())
    assert sum(got.values()) <= 0.95


def test_pair_membership_is_disjoint():
    s = PairsDistance(formation=50, trading=10, n_pairs=2, pool=4)
    s.on_bar(make_context())
    members = [x for p in s._pairs for x in p[:2]]
    assert len(members) == len(set(members))


def test_open_pair_keeps_same_code_when_segment_column_order_changes():
    s = PairsDistance(formation=50, trading=10, n_pairs=1, pool=2)
    # Advance past formation, then install a known formed pair for this transition test.
    s.on_bar(make_context())
    s._pairs = [("A", "B", 0.0, 1.0, 10.0, 10.0)]
    first = make_code_context(("A", "B"), {"A": 40.0, "B": 10.0}, "2015-12-31")
    assert s.on_bar(first) == {"B": 0.95}

    second = make_code_context(("B", "A", "C"),
                               {"A": 40.0, "B": 10.0, "C": 20.0}, "2016-01-04")
    assert s.on_bar(second) == {"B": 0.95}


def test_event_engine_runs_full_interval_with_trades_and_finite_equity():
    days = pd.bdate_range("2015-01-05", periods=90)
    ca = np.full(len(days), 10.0)
    cb = np.full(len(days), 10.0)
    cb[50:55] = np.round(np.linspace(10, 8, 5), 2)
    cb[55:] = np.round(np.linspace(8, 10, len(days) - 55), 2)
    close = pd.DataFrame({SYMS[0]: ca, SYMS[1]: cb}, index=days)
    fields = {"open": close.copy(), "high": close * 1.01, "low": close * 0.99,
              "close": close, "preclose": close.shift(1).fillna(close),
              "volume": close * 0 + 1e7, "amount": close * 1e7,
              "tradestatus": close * 0 + 1, "is_st": close * 0, "is_new": close * 0,
              "adj_factor": close * 0 + 1, "ret": close.pct_change().fillna(0), "bad": close * 0,
              "close_hfq": close}
    class Feed:
        fields = ENGINE_FIELDS + ("close_hfq",)
        def segments(self, start, end, warmup, keep=None):
            panel = Panel.from_frames(fields)
            yield Segment(panel, 0, np.ones((len(days), 2), dtype=bool))
    strategy = PairsDistance(formation=20, trading=90, n_pairs=1, pool=2,
                             entry=0.3, exit=0.05, stop=3.0)
    cfg = EngineConfig(match=MatchConfig(slippage_bp=0, impact_coef=0, commission_min=0))
    result = EventEngine(cfg).run(strategy, Feed(), days[0], days[-1])
    assert len(result.daily) == len(days)
    assert result.fills
    assert np.isfinite(result.daily["equity"]).all()
