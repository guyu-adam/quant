"""P3 5 分钟执行模式：订单按 5 分钟 bar 切片撮合；不传 5 分钟数据时与日线路径逐位相同；续跑逐位一致。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from q6.engine.event import EngineConfig, EngineRun, EventEngine
from q6.engine.matching import MatchConfig
from q6.sim import checkpoint as ckpt
from q6.sim import layout, worker
from q6.strategy.examples import LowVolEqualWeight

from ._synth import SynthFeed

START, END = "2019-02-01", "2020-06-30"
TIMES = [f"{h:02d}{m:02d}00" for h, m in
         [(9, 35 + 5 * k) if 35 + 5 * k < 60 else (10, 35 + 5 * k - 60) for k in range(12)]]


class FakeMin5:
    """由日线合成的 5 分钟 bar：12 根，量 = 日量 / 12，价格在 [low, high] 内从 open 走到 close。
    locked：{(day, symbol)} 当天前 6 根 bar 一字封在涨停价上；missing：{(day, symbol)} 当天没有数据。"""

    def __init__(self, feed: SynthFeed, locked=(), missing=()):
        self.f, self.locked, self.missing = feed.frames, set(locked), set(missing)
        self.calls = 0

    def __call__(self, day, symbols):
        self.calls += 1
        out = {}
        for s in symbols:
            if (day, s) in self.missing or day not in self.f["close"].index:
                continue
            g = {k: float(self.f[k].at[day, s])
                 for k in ("open", "high", "low", "close", "volume", "preclose")}
            px = np.linspace(g["open"], g["close"], 12)
            v = np.full(12, g["volume"] // 12, dtype=np.int64)
            hi, lo = np.minimum(px * 1.001, g["high"]), np.maximum(px * 0.999, g["low"])
            if (day, s) in self.locked:
                up = round(g["preclose"] * 1.1, 2)
                px[:6] = hi[:6] = lo[:6] = up
            out[s] = dict(time=np.array(TIMES), open=px, high=np.maximum(hi, px), low=np.minimum(lo, px),
                          close=px, volume=v, amount=px * v)
        return out


def _run(intraday=None, cfg=None):
    s = LowVolEqualWeight(n=3, lookback=20, every=5)
    r = EngineRun(EventEngine(cfg or EngineConfig()), s, SynthFeed(), START, END, intraday=intraday)
    for _ in r.days():
        pass
    return r.result()


def test_intraday_slices_fill_within_bar_participation():
    feed = SynthFeed()
    m5 = FakeMin5(feed)
    res = _run(m5)
    daily = _run()
    assert m5.calls > 50 and len(res.fills) > len(daily.fills)  # 切片：一张订单多笔子成交
    times = {f.ts.strftime("%H%M") for f in res.fills}
    assert times <= {t[:4] for t in TIMES} and len(times) > 1
    vol = feed.frames["volume"]
    for f in res.fills:  # 每笔子成交 ≤ 该 bar 量 × 10%
        assert f.qty <= 0.1 * (vol.at[pd.Timestamp(f.ts.date()), f.symbol] // 12) + 1e-9
    # 同一策略、同一天的买入总股数在两种模式下同量级（参与率不绑定时应很接近）
    assert abs(res.daily["equity"].iloc[-1] / daily.daily["equity"].iloc[-1] - 1) < 0.2
    assert "MIN5_MISSING" not in res.reasons


def test_locked_bars_skipped_and_missing_falls_back_to_daily():
    feed = SynthFeed()
    base = _run(FakeMin5(feed))
    first_buy = next(f for f in base.fills if f.side.name == "BUY")
    day, sym = pd.Timestamp(first_buy.ts.date()), first_buy.symbol
    res = _run(FakeMin5(feed, locked={(day, sym)}))
    got = [f for f in res.fills
           if f.symbol == sym and pd.Timestamp(f.ts.date()) == day and f.side.name == "BUY"]
    assert got and min(f.ts.strftime("%H%M%S") for f in got) >= TIMES[6]  # 封板的前 6 根一笔没成交
    res2 = _run(FakeMin5(feed, missing={(day, sym)}))
    assert res2.reasons["MIN5_MISSING"] == 1
    fb = [f for f in res2.fills if f.symbol == sym and pd.Timestamp(f.ts.date()) == day]
    assert len(fb) == 1 and fb[0].ts.hour == 15  # 退回日线撮合：一笔、收盘时间戳


def test_no_intraday_is_unchanged():
    a, b = _run(), _run(None)
    pd.testing.assert_frame_equal(a.daily, b.daily, check_exact=True)


def test_intraday_resume_bitwise(tmp_path):
    feed = SynthFeed()
    ref = _run(FakeMin5(feed))
    spec = dict(run_id="m", strategy="lowvol", params=dict(n=3, lookback=20, every=5), start=START, end=END,
                checkpoint_every=7)
    rng = np.random.default_rng(5)
    while worker.run_worker(spec, tmp_path, feed=SynthFeed(), intraday=FakeMin5(feed),
                            stop_after_days=int(rng.integers(5, 90))) != 0:
        pass
    got = ckpt.read_daily(layout.run_db(tmp_path, "m"))
    np.testing.assert_array_equal(got["equity"].to_numpy(), ref.daily["equity"].to_numpy())
    np.testing.assert_array_equal(got["fees"].to_numpy(), ref.daily["fees"].to_numpy())


def test_intraday_costs_more_commission_with_min_fee():
    """拆单后每个子单都有 5 元最低佣金：佣金合计应高于日线一次成交（如实体现拆单成本）。"""
    feed = SynthFeed()
    cfg = EngineConfig(match=MatchConfig(commission_min=5.0))
    a, b = _run(FakeMin5(feed), cfg), _run(None, cfg)
    assert sum(f.commission for f in a.fills) > sum(f.commission for f in b.fills)
