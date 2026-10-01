from datetime import datetime

import pytest

from q6.core.clock import AcceleratedClock, BacktestClock, ClockError


def test_backtest_clock_monotonic():
    c = BacktestClock()
    with pytest.raises(ClockError):
        c.now()
    c.advance(datetime(2021, 1, 4, 15))
    c.advance(datetime(2021, 1, 4, 15))  # 同一时刻允许（同一 bar 内多个事件）
    c.advance(datetime(2021, 1, 5, 15))
    assert c.now() == datetime(2021, 1, 5, 15)
    with pytest.raises(ClockError):
        c.advance(datetime(2021, 1, 4, 15))


def test_accelerated_clock_maps_real_seconds():
    t = [100.0]
    c = AcceleratedClock(datetime(2024, 1, 2, 9, 30), speed=60, real_time=lambda: t[0])
    assert c.now() == datetime(2024, 1, 2, 9, 30)
    t[0] += 1.5  # 真实 1.5 秒 = 模拟 90 秒
    assert c.now() == datetime(2024, 1, 2, 9, 31, 30)
    with pytest.raises(ValueError):
        AcceleratedClock(datetime(2024, 1, 2), speed=0)
