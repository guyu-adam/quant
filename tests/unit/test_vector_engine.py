"""向量化引擎（P2-08）：同一策略、同一数据，和事件引擎逐日权益 / 成交 / 原因计数逐位一致；
run_many、截断测试、非法输入。

场景沿用 test_event_engine 的合成面板（复制了 frames，避免测试模块互相导入）。
真实快照上的全区间对照见 PROGRESS P2-08。
"""

import numpy as np
import pandas as pd
import pytest

from q6.core.pit import Panel
from q6.engine.event import EngineConfig, EventEngine
from q6.engine.feed import ENGINE_FIELDS, Segment
from q6.engine.matching import MatchConfig
from q6.engine.vector import VectorEngine, check_weights_pit, strategy_weights
from q6.strategy.base import StrategyBase, StrategySpec

A, B = "sz.000001", "sh.600000"
DAYS = pd.bdate_range("2021-03-01", periods=8)
NOCOST = EngineConfig(match=MatchConfig(slippage_bp=0.0, impact_coef=0.0, commission_min=0.0))


def frames(close_a=None, close_b=None, **over):
    ca = np.array(close_a if close_a is not None else [10.0] * len(DAYS), dtype=float)
    cb = np.array(close_b if close_b is not None else [20.0] * len(DAYS), dtype=float)
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

    def __init__(self, *parts):
        """parts：每段一个 frames 字典；多段时各段可以有不同的列（模拟每年重建面板）。"""
        self.parts = parts

    def segments(self, start, end, warmup, keep=None):
        for f in self.parts:
            p = Panel.from_frames(f)
            yield Segment(p, 0, np.ones((len(p), len(p.symbols)), dtype=bool))


class Script(StrategyBase):
    """按 on_bar 被调用的次序返回计划里的权重（与事件引擎调用次序一致）。"""

    name = "script"

    def __init__(self, plan):
        self.plan, self.k = plan, 0

    @property
    def spec(self):
        return StrategySpec(fields=("close",), warmup=1)

    def on_bar(self, ctx):
        self.k += 1
        return self.plan.get(self.k - 1)


def both(parts, plan, cfg=NOCOST):
    e = EventEngine(cfg).run(Script(plan), FakeFeed(*parts), DAYS[0], DAYS[-1])
    s = Script(plan)
    v = VectorEngine(cfg).run(strategy_weights(s), 1, FakeFeed(*parts), DAYS[0], DAYS[-1])
    return e, v


def assert_same(e, v):
    pd.testing.assert_frame_equal(e.daily, v.daily, check_exact=True)
    assert e.reasons == v.reasons
    assert e.delistings == v.delistings
    key = lambda f: (f.ts, f.symbol, f.side, f.qty, f.price, f.fees, f.slippage_cost, f.impact_cost)  # noqa: E731
    assert [key(f) for f in e.fills] == [key(f) for f in v.fills]


def delisted(f, start):
    for k in ("open", "high", "low", "close", "preclose", "volume", "amount", "tradestatus", "ret"):
        f[k].loc[DAYS[start]:, A] = np.nan
    return f


SCENARIOS = {
    "buy_sell_with_costs": ([frames(close_a=[10, 10.2, 10.1, 10.4, 10.3, 10.0, 10.2, 10.5])],
                            {1: {A: 0.5, B: 0.4}, 3: {A: 0.2, B: 0.7}, 5: {}}, EngineConfig()),
    "full_invest_hits_cash": ([frames()], {1: {A: 0.5, B: 0.5}, 3: {A: 1.0}}, EngineConfig()),
    "ten_for_ten": (None, {1: {A: 0.5}}, NOCOST),
    "suspended": (None, {2: {A: 0.5}}, NOCOST),
    "locked_limit_up": (None, {1: {A: 0.5}}, EngineConfig()),
    "delisting": ([delisted(frames(), 4)], {1: {A: 0.5}}, EngineConfig(delist_after=3, delist_recovery=0.5)),
}


def _scenario(name):
    parts, plan, cfg = SCENARIOS[name]
    if name == "ten_for_ten":
        f = frames(close_a=[10, 10, 10, 10, 5.0, 5.0, 5.0, 5.0])
        f["adj_factor"][A] = [1, 1, 1, 1, 2, 2, 2, 2]
        f["preclose"][A] = [10, 10, 10, 10, 5.0, 5.0, 5.0, 5.0]
        parts = [f]
    elif name == "suspended":
        f = frames(close_a=[10, 10, 10, 10, 10, 10, 11, 11])
        f["tradestatus"][A] = [1, 1, 1, 0, 0, 1, 1, 1]
        parts = [f]
    elif name == "locked_limit_up":
        c = [10, 10, 11.0, 11.0, 11.0, 11.0, 11.0, 11.0]  # 第 2 天一字涨停，第 3 天起平盘
        f = frames(close_a=c)
        f["high"][A] = c
        f["low"][A] = [10 * 0.995, 10 * 0.995, 11.0] + [11 * 0.995] * 5
        f["open"][A] = c
        parts = [f]
    return parts, plan, cfg


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_identical_to_event_engine(name):
    parts, plan, cfg = _scenario(name)
    e, v = both(parts, plan, cfg)
    assert_same(e, v)
    if name == "full_invest_hits_cash":
        assert len(e.fills) > 0
    if name == "delisting":
        assert len(v.delistings) == 1
    if name == "locked_limit_up":
        assert v.reasons.get("LOCKED_LIMIT") == 1


def test_state_follows_symbols_across_segments_with_different_columns():
    """第二段的列集合和顺序都变了（新增一只、顺序反转），持仓必须跟着代码走。"""
    C = "sz.000002"
    days1, days2 = DAYS[:4], DAYS[4:]
    full = frames(close_a=[10, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7])
    seg1 = {k: v.loc[days1] for k, v in full.items()}
    seg2 = {}
    for k, v in full.items():
        x = v.loc[days2, [B, A]].copy()
        x[C] = v.loc[days2, A] * 0 + (1.0 if k in ("tradestatus", "adj_factor") else 0.0)
        if k in ("open", "high", "low", "close", "preclose"):
            x[C] = 30.0
        if k in ("volume", "amount"):
            x[C] = 1e7
        seg2[k] = x
    plan = {1: {A: 0.5}, 5: {A: 0.5, C: 0.3}}
    e, v = both([seg1, seg2], plan, EngineConfig())
    assert_same(e, v)
    assert {f.symbol for f in v.fills} == {A, C}


def test_run_many_equals_separate_runs():
    f = frames(close_a=[10, 10.2, 10.1, 10.4, 10.3, 10.0, 10.2, 10.5])
    cfgs = [EngineConfig(), EngineConfig(match=MatchConfig(cost_multiplier=2.0)), NOCOST]
    plans = [{1: {A: 0.5, B: 0.4}, 4: {B: 0.9}}, {1: {A: 0.5, B: 0.4}, 4: {B: 0.9}}, {2: {A: 0.9}}]
    jobs = [(strategy_weights(Script(p)), 1, c) for p, c in zip(plans, cfgs, strict=True)]
    many = VectorEngine().run_many(jobs, FakeFeed(f), DAYS[0], DAYS[-1])
    for (p, c), got in zip(zip(plans, cfgs, strict=True), many, strict=True):
        one = VectorEngine(c).run(strategy_weights(Script(p)), 1, FakeFeed(f), DAYS[0], DAYS[-1])
        assert_same(one, got)
    assert many[1].daily["equity"].iloc[-1] < many[0].daily["equity"].iloc[-1]  # 成本翻倍，权益更低


def _seg():
    p = Panel.from_frames(frames(close_a=[10, 10.2, 10.1, 10.4, 10.3, 10.0, 10.2, 10.5]))
    return Segment(p, 0, np.ones((len(p), 2), dtype=bool))


def test_check_weights_pit_passes_causal_and_catches_leak():
    def causal(seg):
        c = seg.panel.block("close", 0, len(seg.panel))
        mom = np.vstack([np.full((1, c.shape[1]), np.nan), c[1:] / c[:-1] - 1])
        return np.where(mom > 0, 0.4, 0.0)

    def leaky(seg):
        c = seg.panel.block("close", 0, len(seg.panel))
        fut = np.vstack([c[1:] / c[:-1] - 1, np.zeros((1, c.shape[1]))])  # 用了明天的收益
        return np.where(fut > 0, 0.4, 0.0)

    seg = _seg()
    check_weights_pit(causal, seg, cuts=range(1, 8))
    with pytest.raises(AssertionError, match="未来数据"):
        check_weights_pit(leaky, seg, cuts=range(1, 8))


def test_invalid_weights_rejected():
    def over(seg):
        w = np.full((len(seg.panel), 2), np.nan)
        w[1] = [0.7, 0.7]
        return w

    with pytest.raises(ValueError, match="目标权重非法"):
        VectorEngine().run(over, 1, FakeFeed(frames()), DAYS[0], DAYS[-1])

    def bad_shape(seg):
        return np.zeros((3, 2))

    with pytest.raises(ValueError, match="形状"):
        VectorEngine().run(bad_shape, 1, FakeFeed(frames()), DAYS[0], DAYS[-1])
