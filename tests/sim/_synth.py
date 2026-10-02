"""合成的多年数据源：按年分段、段内代码 = 当年成分 ∪ keep()，行为同 SnapshotFeed（续跑测试要覆盖跨段）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from q6.core.pit import Panel
from q6.engine.feed import ENGINE_FIELDS, Segment

SYMS = tuple(f"sz.{i:06d}" for i in range(1, 9))


def make_frames(start="2019-01-01", periods=560, seed=7):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=periods)
    n = len(SYMS)
    r = rng.normal(0.0003, 0.02, (len(days), n)).clip(-0.09, 0.09)
    close = pd.DataFrame(10 * np.exp(np.cumsum(np.log1p(r), axis=0)), index=days, columns=SYMS).round(2)
    pre = close.shift(1)
    pre.iloc[0] = close.iloc[0]
    status = pd.DataFrame(1.0, index=days, columns=SYMS)
    status.iloc[100:110, 2] = 0.0  # 停牌一段
    adj = pd.DataFrame(1.0, index=days, columns=SYMS)
    adj.iloc[300:, 3] = 1.25  # 一次送转
    pre.iloc[300, 3] = close.iloc[299, 3] / 1.25
    f = {"open": pre * (1 + rng.normal(0, 0.005, close.shape)), "close": close, "preclose": pre,
         "volume": close * 0 + 2e6, "amount": close * 2e6, "tradestatus": status, "is_st": close * 0,
         "is_new": close * 0, "adj_factor": adj, "bad": close * 0}
    f["open"] = f["open"].clip(pre * 0.91, pre * 1.09).round(2)
    f["high"] = np.maximum(f["open"], close) * 1.004
    f["low"] = np.minimum(f["open"], close) * 0.996
    f["high"] = f["high"].clip(upper=(pre * 1.1).round(2))
    f["low"] = f["low"].clip(lower=(pre * 0.9).round(2))
    f["ret"] = close * adj / (close * adj).shift(1) - 1
    f["close_hfq"] = close * adj
    return days, {k: f[k] for k in (*ENGINE_FIELDS, "close_hfq")}


class SynthFeed:
    fields = (*ENGINE_FIELDS, "close_hfq")

    def __init__(self, seed=7, periods=560):
        self.days, self.frames = make_frames(periods=periods, seed=seed)
        self.calendar = self.days
        self.loads: list[tuple[int, tuple[str, ...]]] = []

    def members(self, year):  # 每年轮换成分：偶数年前 6 只，奇数年后 6 只
        return set(SYMS[:6]) if year % 2 == 0 else set(SYMS[2:])

    def segments(self, start, end, warmup, keep=None):
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        run_days = self.days[(self.days >= start) & (self.days <= end)]
        for year in sorted(set(run_days.year)):
            days = run_days[run_days.year == year]
            i0 = int(self.days.get_loc(days[0]))
            lo, hi = max(0, i0 - warmup), int(self.days.get_loc(days[-1])) + 1
            codes = tuple(sorted(self.members(year) | set(keep() if keep else ())))
            self.loads.append((year, codes))
            panel = Panel.from_frames({k: v.iloc[lo:hi][list(codes)] for k, v in self.frames.items()})
            uni = np.array([[c in self.members(d.year) for c in codes] for d in self.days[lo:hi]])
            yield Segment(panel, i0 - lo, uni)
