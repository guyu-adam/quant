"""Walk-forward（P2-20）：滚动训练 / 测试窗口，样本外结果只拼接测试窗口。

窗口（架构 §4.7）：训练 3 年、测试 6 个月、步长 6 个月（均可配）。按交易日历切：
- 第 k 个测试窗口 = [start + train_years + k·step_months, 该日 + test_months) 内的交易日；
  最后一个窗口截到 end。
- 训练窗口 = 测试首日之前、再往前空出 gap = purge_days + embargo_days 个交易日后的 train_years 年
  （expanding=True 时从 start 起）。
  purge：t 日样本的标签用到 t+1+h 日收盘（`ml.labels`：t+1 成交、持有 h 日），所以 purge_days 至少取 h+1，
  训练集最后一个样本的标签信息才不会落进测试期。embargo：前向滚动里训练集永远在测试集之前，López de Prado 的
  embargo（剔除测试集**之后**的训练样本）用不上；这里把它当额外的保守间隔，默认 0。

强制隔离（不靠自觉）：`run_walkforward` 在 `lockbox.research_horizon(window.train_end)` 里调用 fit——
块内任何快照读取、任何 Panel 构造只要碰到训练窗口之后的日期就抛 HorizonError（和锁箱期同一套两层检查）。

样本外拼接：所有窗口的模型先训练好，然后**一次连续**跑测试区间 [第一个测试首日, 最后一个测试末日]：
`_Router` 策略按当天日期把 on_bar 交给该日所属窗口的策略实例（每个窗口一个新实例，计数器从窗口首日重新开始）。
账户跨窗口延续，持仓不会在窗口边界被强制清仓再买回（那会凭空多出换手成本）。
锁箱期：end 不得晚于锁箱期起点前一天（未解锁时直接拒绝，不静默截断）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from q6.core.types import Fill
from q6.data import lockbox
from q6.engine.event import BacktestResult, EventEngine
from q6.engine.feed import SnapshotFeed
from q6.strategy.base import BarContext, Strategy, StrategySpec


@dataclass(frozen=True)
class Window:
    k: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def make_windows(calendar: pd.DatetimeIndex, start, end, *, train_years: int = 3, test_months: int = 6,
                 step_months: int = 6, purge_days: int = 0, embargo_days: int = 0,
                 expanding: bool = False) -> list[Window]:
    if train_years <= 0 or test_months <= 0 or step_months <= 0 or purge_days < 0 or embargo_days < 0:
        raise ValueError("train_years / test_months / step_months 必须 >0，purge_days / embargo_days 必须 ≥0")
    if step_months < test_months:
        raise ValueError("step_months < test_months 会让测试窗口重叠，样本外拼接会重复计入")
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if not lockbox.is_unlocked() and end >= lockbox.LOCKBOX_TS:
        raise lockbox.LockboxError(f"walk-forward 的 end={end.date()} 进入锁箱期（≥{lockbox.LOCKBOX_START}）")
    cal = pd.DatetimeIndex(calendar).sort_values()
    cal = cal[(cal >= start) & (cal <= end)]
    if not len(cal):
        raise ValueError("区间内没有交易日")
    gap = purge_days + embargo_days
    out: list[Window] = []
    k = 0
    while True:
        t0 = start + pd.DateOffset(years=train_years) + pd.DateOffset(months=k * step_months)
        t1 = t0 + pd.DateOffset(months=test_months)
        test = cal[(cal >= t0) & (cal < t1)]
        if not len(test) or test[0] > end:
            break
        i0 = int(cal.get_loc(test[0]))
        j = i0 - 1 - gap
        if j < 0:
            raise ValueError(f"第 {k} 个窗口在 purge/embargo 之后没有训练数据")
        train_end = cal[j]
        train_start = cal[0] if expanding else cal[cal >= train_end - pd.DateOffset(years=train_years)][0]
        out.append(Window(k, train_start, train_end, test[0], test[-1]))
        k += 1
    if not out:
        raise ValueError("区间太短，放不下一个训练 + 测试窗口")
    return out


class _Router:
    """按日期把 on_bar 交给所属窗口的策略。窗口按测试首日排序且不重叠。"""

    name = "walkforward"

    def __init__(self, windows: list[Window], strategies: list[Strategy]) -> None:
        self.windows, self.strategies = windows, strategies
        fields: dict[str, None] = {}
        for s in strategies:
            fields.update(dict.fromkeys(s.spec.fields))
        self._spec = StrategySpec(fields=tuple(fields), warmup=max(s.spec.warmup for s in strategies),
                                  params={"windows": len(windows), "inner": strategies[0].name})

    @property
    def spec(self) -> StrategySpec:
        return self._spec

    def _active(self, day: pd.Timestamp) -> Strategy | None:
        for w, s in zip(self.windows, self.strategies, strict=True):
            if w.test_start <= day <= w.test_end:
                return s
        return None

    def on_bar(self, ctx: BarContext):
        s = self._active(ctx.now)
        return None if s is None else s.on_bar(ctx)

    def on_fill(self, fill: Fill) -> None:
        s = self._active(pd.Timestamp(fill.ts.date()))
        if s is not None:
            s.on_fill(fill)


@dataclass
class WalkForwardResult:
    windows: list[Window]
    params: list[Any]
    oos: BacktestResult  # 只含测试区间的连续回测


def run_walkforward(windows: list[Window], fit: Callable[[Window], Any], build: Callable[[Any], Strategy],
                    feed: SnapshotFeed, engine: EventEngine | None = None) -> WalkForwardResult:
    """fit(window) → 参数 / 模型（在研究视界 window.train_end 内执行）；build(参数) → 新的策略实例。"""
    params = []
    for w in windows:
        with lockbox.research_horizon(w.train_end):
            params.append(fit(w))
    strategies = [build(p) for p in params]
    router = _Router(windows, strategies)
    missing = set(router.spec.fields) - set(feed.fields)
    if missing:
        raise ValueError(f"策略需要的字段 {sorted(missing)} 没有加载（SnapshotFeed(extra_fields=...)）")
    oos = (engine or EventEngine()).run(router, feed, windows[0].test_start, windows[-1].test_end)
    return WalkForwardResult(windows, params, oos)
