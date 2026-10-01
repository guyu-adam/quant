"""Walk-forward（P2-20）：窗口切分、purge 间隔、锁箱期拒绝、研究视界强制隔离、按日期路由。"""

import numpy as np
import pandas as pd
import pytest

from q6.core.pit import Panel
from q6.data import lockbox
from q6.data.lockbox import HorizonError, LockboxError, research_horizon
from q6.engine.event import EngineConfig, EventEngine
from q6.engine.feed import ENGINE_FIELDS, Segment
from q6.engine.matching import MatchConfig
from q6.research.walkforward import make_windows, run_walkforward
from q6.strategy.base import StrategyBase, StrategySpec

CAL = pd.bdate_range("2010-01-01", "2016-12-30")


def test_default_windows_contiguous_non_overlapping():
    ws = make_windows(CAL, "2010-01-01", "2016-12-30")
    assert ws[0].test_start == pd.Timestamp("2013-01-01")
    assert len(ws) == 8  # 2013-01 … 2016-07，每段 6 个月
    for a, b in zip(ws, ws[1:], strict=False):
        assert a.test_end < b.test_start
        assert CAL.get_loc(b.test_start) == CAL.get_loc(a.test_end) + 1  # 测试段首尾相接，样本外无缺口
    for w in ws:
        assert w.train_end < w.test_start
        assert (w.train_end - w.train_start).days >= 3 * 365 - 5


@pytest.mark.parametrize("purge,embargo", [(0, 0), (6, 0), (21, 5)])
def test_gap_is_exactly_purge_plus_embargo_trading_days(purge, embargo):
    for w in make_windows(CAL, "2010-01-01", "2016-12-30", purge_days=purge, embargo_days=embargo):
        assert CAL.get_loc(w.test_start) - CAL.get_loc(w.train_end) - 1 == purge + embargo


def test_expanding_training_starts_at_beginning():
    ws = make_windows(CAL, "2010-01-01", "2016-12-30", expanding=True)
    assert all(w.train_start == CAL[0] for w in ws)


def test_rejects_lockbox_and_overlapping_tests():
    with pytest.raises(LockboxError):
        make_windows(pd.bdate_range("2020-01-01", "2024-12-31"), "2020-01-01", "2024-07-01")
    with pytest.raises(ValueError, match="重叠"):
        make_windows(CAL, "2010-01-01", "2016-12-30", test_months=6, step_months=3)


def _panel(days):
    return Panel(days, ["a"], {"close": np.ones((len(days), 1))})


def test_research_horizon_blocks_later_dates_and_nests():
    days = pd.bdate_range("2012-06-25", "2012-07-06")
    with research_horizon("2012-06-29"):
        _panel(days[:5])  # ≤ 视界：可以
        with pytest.raises(HorizonError):
            _panel(days)
        with research_horizon("2012-07-31"):  # 嵌套取较早的
            assert lockbox.current_horizon() == pd.Timestamp("2012-06-29")
            with pytest.raises(HorizonError):
                _panel(days)
    assert lockbox.current_horizon() is None
    _panel(days)  # 出了视界恢复


# ---------------------------------------------------------------- run_walkforward（合成数据）
A = "sz.000001"
DAYS = pd.bdate_range("2014-01-01", "2016-06-30")


class FakeFeed:
    fields = ENGINE_FIELDS

    def __init__(self):
        n = len(DAYS)
        c = np.full((n, 1), 10.0)
        f = {k: c.copy() for k in ("open", "close", "preclose")}
        f.update(high=c * 1.005, low=c * 0.995, volume=c * 0 + 1e7, amount=c * 1e8, tradestatus=c * 0 + 1,
                 is_st=c * 0, is_new=c * 0, adj_factor=c * 0 + 1, ret=c * 0, bad=c * 0)
        self.f = f
        self.calendar = DAYS

    def segments(self, start, end, warmup, keep=None):
        first = int(np.searchsorted(DAYS.values, pd.Timestamp(start).to_datetime64()))
        last = int(np.searchsorted(DAYS.values, pd.Timestamp(end).to_datetime64(), side="right"))
        sub = Panel(DAYS[:last], [A], {k: v[:last] for k, v in self.f.items()})
        yield Segment(sub, first, np.ones((last, 1), dtype=bool))


class Tagged(StrategyBase):
    name = "tagged"

    def __init__(self, tag):
        self.tag, self.days = tag, []

    @property
    def spec(self):
        return StrategySpec(fields=("close",), warmup=1, params={"tag": self.tag})

    def on_bar(self, ctx):
        self.days.append(ctx.now)
        return {A: 0.5} if self.tag % 2 == 0 else {}


def test_fit_runs_under_horizon_and_router_dispatches_by_date():
    ws = make_windows(DAYS, "2014-01-01", "2016-06-30", train_years=1, purge_days=5)
    seen, built = [], []

    def fit(w):
        seen.append((w.k, lockbox.current_horizon()))
        return w.k

    def build(k):
        s = Tagged(k)
        built.append(s)
        return s

    cfg = EngineConfig(match=MatchConfig(slippage_bp=0.0, impact_coef=0.0, commission_min=0.0))
    res = run_walkforward(ws, fit, build, FakeFeed(), EventEngine(cfg))
    assert [h for _, h in seen] == [w.train_end for w in ws]
    for w, s in zip(ws, built, strict=True):
        assert s.days and min(s.days) >= w.test_start and max(s.days) <= w.test_end
    assert res.oos.daily.index[0] == ws[0].test_start and res.oos.daily.index[-1] == ws[-1].test_end
    assert res.params == list(range(len(ws)))


def test_leaky_fit_is_stopped():
    ws = make_windows(DAYS, "2014-01-01", "2016-06-30", train_years=1)

    def leaky(w):
        return _panel(pd.bdate_range(w.train_end, w.test_start))  # 训练时偷看测试期首日

    with pytest.raises(HorizonError):
        run_walkforward(ws, leaky, lambda p: Tagged(0), FakeFeed())
