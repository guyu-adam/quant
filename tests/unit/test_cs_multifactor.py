import numpy as np
import pandas as pd

from q6.core.pit import Panel
from q6.engine.event import EngineConfig, EventEngine
from q6.engine.feed import ENGINE_FIELDS, Segment
from q6.engine.matching import MatchConfig
from q6.strategy.base import BarContext
from q6.strategy.cs_multifactor import CSMultiFactor

SYMS = ("sh.600000", "sh.600001", "sz.000001", "sz.000002", "sz.000003")
DAYS = pd.bdate_range("2021-01-04", periods=40)


def make_frames():
    t, n = len(DAYS), len(SYMS)
    trend = np.arange(t, dtype=float)[:, None]
    strength = np.arange(n, dtype=float)[None, :]
    close_hfq = 20 + trend * (0.05 + strength * 0.02) + strength
    ret = np.vstack([np.zeros((1, n)), close_hfq[1:] / close_hfq[:-1] - 1])
    amount = np.broadcast_to(1e8 + strength * 1e7, (t, n)).copy()
    turn = np.broadcast_to(1.0 + strength, (t, n)).copy()
    close = close_hfq.copy()
    f = {
        "open": close.copy(), "high": close * 1.005, "low": close * 0.995,
        "close": close, "preclose": np.vstack([close[:1], close[:-1]]),
        "volume": close * 1e6, "amount": amount, "tradestatus": np.ones((t, n)),
        "is_st": np.zeros((t, n)), "is_new": np.zeros((t, n)), "adj_factor": np.ones((t, n)),
        "ret": ret, "bad": np.zeros((t, n)), "close_hfq": close_hfq, "turn": turn,
    }
    return {k: pd.DataFrame(v, index=DAYS, columns=SYMS) for k, v in f.items()}


class Feed:
    fields = tuple(dict.fromkeys((*ENGINE_FIELDS, "close_hfq", "turn")))

    def __init__(self, frames):
        self.panel = Panel.from_frames(frames)

    def segments(self, start, end, warmup, keep=None):
        yield Segment(self.panel, 0, np.ones((len(DAYS), len(SYMS)), dtype=bool))


def context(frames, index, universe=SYMS):
    panel = Panel.from_frames(frames)
    view = panel.view(index)
    return BarContext(view, None, tuple(universe))


def strategy(**kwargs):
    return CSMultiFactor(factors="rev_20,size", n=3, every=1, history=30, **kwargs)


def test_picks_top_equal_weight_and_gross():
    f = make_frames()
    result = strategy().on_bar(context(f, 29))
    assert len(result) == 3
    assert sum(result.values()) == 0.95
    assert len(set(result.values())) == 1


def test_universe_and_status_filters_exclude_names():
    f = make_frames()
    f["tradestatus"].loc[DAYS[29], SYMS[4]] = 0
    f["is_st"].loc[DAYS[29], SYMS[3]] = 1
    f["is_new"].loc[DAYS[29], SYMS[2]] = 1
    result = strategy().on_bar(context(f, 29, universe=SYMS[:4]))
    assert set(result) <= set(SYMS[:2])


def test_insufficient_factor_coverage_excludes_stock():
    f = make_frames()
    f["close_hfq"].loc[DAYS[10:30], SYMS[4]] = np.nan
    f["ret"].loc[DAYS[10:30], SYMS[4]] = np.nan
    f["amount"].loc[DAYS[10:30], SYMS[4]] = np.nan
    f["turn"].loc[DAYS[10:30], SYMS[4]] = np.nan
    result = strategy().on_bar(context(f, 29))
    assert SYMS[4] not in result


def test_all_ineligible_returns_empty_weights():
    f = make_frames()
    f["tradestatus"].loc[DAYS[29], :] = 0
    assert strategy().on_bar(context(f, 29)) == {}


def test_every_bar_cadence_and_event_engine_run():
    f = make_frames()
    s = CSMultiFactor(factors="rev_20,size", n=3, every=5, history=30)
    # first call rebalances, next four calls retain current holdings
    assert s.on_bar(context(f, 29)) is not None
    assert all(s.on_bar(context(f, i)) is None for i in range(30, 34))
    cfg = EngineConfig(match=MatchConfig(slippage_bp=0, impact_coef=0, commission_min=0))
    tested = CSMultiFactor(factors="rev_20,size", n=3, every=5, history=30)
    res = EventEngine(cfg).run(tested, Feed(f), DAYS[0], DAYS[-1])
    assert len(res.daily) == len(DAYS)
    assert res.fills
    assert np.isfinite(res.daily["equity"]).all()
