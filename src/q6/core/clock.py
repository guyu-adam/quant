"""时钟（P2-07）：引擎里"现在几点"的唯一来源。

- BacktestClock：回测用。由引擎逐 bar 推进，只许前进不许后退（时间倒流说明处理顺序写错了，直接报错）。
- AcceleratedClock：P3 模拟盘用。把真实流逝的时间按 speed 倍映射到模拟时间上，
  例如 speed=60 时真实 1 秒 = 模拟 1 分钟；
  real_time 可注入，单测不用真的等。

策略拿不到时钟，只拿到 PITView.now（= 当前 bar 的日期）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol


class ClockError(RuntimeError):
    pass


class Clock(Protocol):
    def now(self) -> datetime: ...


class BacktestClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start

    def now(self) -> datetime:
        if self._now is None:
            raise ClockError("回测时钟还没有推进到第一根 bar")
        return self._now

    def advance(self, ts: datetime) -> datetime:
        if self._now is not None and ts < self._now:
            raise ClockError(f"时间倒流：{ts} < {self._now}")
        self._now = ts
        return ts


class AcceleratedClock:
    def __init__(self, start: datetime, speed: float = 1.0,
                 real_time: Callable[[], float] = time.monotonic) -> None:
        if not speed > 0:
            raise ValueError(f"speed 必须 >0，得到 {speed}")
        self._start, self._speed, self._real = start, float(speed), real_time
        self._t0 = real_time()

    def now(self) -> datetime:
        return self._start + timedelta(seconds=(self._real() - self._t0) * self._speed)
