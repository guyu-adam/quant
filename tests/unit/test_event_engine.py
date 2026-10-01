"""事件引擎（P2-07）：处理顺序、t+1 成交、除权、停牌、预热、PIT。用内存里的合成面板，不依赖快照。"""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from q6.core.pit import Panel
from q6.core.types import Side
from q6.engine.event import EngineConfig, EventEngine
from q6.engine.feed import ENGINE_FIELDS, Segment
from q6.engine.matching import MatchConfig
from q6.strategy.base import StrategyBase, StrategySpec

A, B = "sz.000001", "sh.600000"
DAYS = pd.bdate_range("2021-03-01", periods=8)


def frames(close_a=None, close_b=None, **over):
    ca = np.array(close_a if close_a is not None else [10.0] * len(DAYS))
    cb = np.array(close_b if close_b is not None else [20.0] * len(DAYS))
    close = pd.DataFrame({A: ca, B: cb}, index=DAYS)
    pre = close.shift(1)
    pre.iloc[0] = close.iloc[0]
    f = {
        "open": close.copy(), "high": close * 1.005, "low": close * 0.995, "close": close, "preclose": pre,
        "volume": close * 0 + 1e7, "amount": close * 1e7, "tradestatus": close * 0 + 1, "is_st": close * 0,
        "is_new": close * 0, "adj_factor": close * 0 + 1.0, "ret": close / pre - 1, "bad": close * 0,
    }
    for k, v in over.items():
        f[k] = v
    f["high"] = np.maximum(f["high"], np.maximum(f["open"], f["close"]))
    f["low"] = np.minimum(f["low"], np.minimum(f["open"], f["close"]))
    return f


class FakeFeed:
    fields = ENGINE_FIELDS

    def __init__(self, f):
        self.panel = Panel.from_frames(f)

    def segments(self, start, end, warmup):
        yield Segment(self.panel, 0, np.ones((len(DAYS), 2), dtype=bool))


class Script(StrategyBase):
    """第 k 天返回给定权重，其余天返回 None；记录每次看到的日期和 view 里最晚的日期。"""

    name = "script"

    def __init__(self, plan, warmup=1):
        self.plan, self.warmup, self.seen = plan, warmup, []

    @property
    def spec(self):
        return StrategySpec(fields=("close",), warmup=self.warmup)

    def on_bar(self, ctx):
        self.seen.append((ctx.now, ctx.view.dates()[-1]))
        return self.plan.get(len(self.seen) - 1 + self.warmup - 1)


NOCOST = EngineConfig(match=MatchConfig(slippage_bp=0.0, impact_coef=0.0, commission_min=0.0))


def run(f, plan, warmup=1, cfg=NOCOST):
    s = Script(plan, warmup)
    return EventEngine(cfg).run(s, FakeFeed(f), DAYS[0], DAYS[-1]), s


def test_signal_at_t_fills_at_t_plus_1_open():
    close = [10, 10, 10, 10.6, 10.8, 10.8, 10.8, 10.8]  # 涨幅都在 10% 以内（合成数据也得守涨跌停，否则会被当特例日）
    opn = pd.DataFrame({A: [10, 10, 10, 10.5, 10.8, 10.8, 10.8, 10.8], B: [20.0] * 8}, index=DAYS)
    res, _ = run(frames(close_a=close, open=opn), {2: {A: 0.5}})
    (fill,) = res.fills
    assert fill.ts.date() == DAYS[3].date()  # 第 2 天收盘的信号，第 3 天成交
    assert fill.price == 10.5  # 用第 3 天开盘价，不是第 2 天收盘价
    assert res.daily["buy_value"].iloc[2] == 0 and res.daily["buy_value"].iloc[3] > 0


def test_sell_before_buy_frees_cash_same_day():
    res, _ = run(frames(), {1: {A: 0.98}, 3: {B: 0.98}})
    day4 = [x for x in res.fills if x.ts.date() == DAYS[4].date()]
    assert [x.side for x in day4] == [Side.SELL, Side.BUY]  # 先卖 A，回笼资金当天买 B
    assert day4[1].qty * day4[1].price > 0.9 * 1_000_000


def test_view_never_beyond_today_and_warmup():
    res, s = run(frames(), {}, warmup=3)
    assert len(s.seen) == len(DAYS) - 2  # 前 2 天预热，不调用
    for now, last in s.seen:
        assert now == last


def test_ten_for_ten_keeps_equity_continuous():
    close = [10, 10, 10, 10, 5.0, 5.0, 5.0, 5.0]  # 第 4 天 10 送 10，前收 5
    f = frames(close_a=close)
    f["adj_factor"][A] = [1, 1, 1, 1, 2, 2, 2, 2]
    f["preclose"][A] = [10, 10, 10, 10, 5.0, 5.0, 5.0, 5.0]
    res, _ = run(f, {1: {A: 0.5}})
    eq = res.daily["equity"]
    assert eq.iloc[4] == pytest.approx(eq.iloc[3])  # 除权日价格减半，权益不变
    assert res.daily["n_pos"].iloc[-1] == 1


def test_suspended_day_keeps_mark_and_defers():
    f = frames(close_a=[10, 10, 10, 10, 10, 10, 11, 11])
    f["tradestatus"][A] = [1, 1, 1, 0, 0, 1, 1, 1]
    res, _ = run(f, {2: {A: 0.5}})  # 第 3 天停牌：订单不成交；之后不再给信号
    assert res.fills == []
    assert res.reasons.get("SUSPENDED") == 1


def test_costs_reduce_equity_and_invariant():
    cfg = EngineConfig()
    res, _ = run(frames(), {1: {A: 0.5, B: 0.4}, 4: {}}, cfg=cfg)
    d = res.daily
    assert d["fees"].sum() > 0 and d["cost_slip_imp"].sum() > 0
    # 价格全程不变：最终权益 = 初始资金 − 全部显性费用 − 滑点冲击
    assert d["equity"].iloc[-1] == pytest.approx(1_000_000 - d["fees"].sum() - d["cost_slip_imp"].sum(), rel=1e-9)
    assert d["n_pos"].iloc[-1] == 0


def test_same_bar_order_structurally_impossible():
    from q6.core.types import Bar, Order
    from q6.engine.matching import match_order

    ts = datetime(2021, 3, 2, 15)
    b = Bar(ts, A, 10, 10.2, 9.8, 10, 1000, 1e4, 10)
    with pytest.raises(ValueError):
        match_order(Order("x", A, Side.BUY, 100, ts), b, sellable=0, cash_available=1e6, no_limit_day=False,
                    adv=1e8, sigma=0.02, cfg=MatchConfig())
