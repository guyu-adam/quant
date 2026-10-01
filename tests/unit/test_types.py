"""P1-03：核心类型的不变量。"""

from datetime import datetime

import pytest

from q6.core.types import (
    AccountSnapshot, Bar, Fill, Order, OrderType, Position, Side, validate_target_weights,
)

TS = datetime(2020, 1, 2, 15)


def test_position_zero_cost_rejected():
    """v5 bug 回归：有持仓时 avg_cost=0 必须报错，而不是让止盈除零后被静默吞掉。"""
    with pytest.raises(ValueError):
        Position("sh.600000", 100, 100, 0.0, 10.0)
    Position("sh.600000", 0, 0, 0.0, 10.0)


def test_position_sellable_bounds():
    with pytest.raises(ValueError):
        Position("A", 100, 200, 10.0, 10.0)
    with pytest.raises(ValueError):
        Position("A", -100, 0, 10.0, 10.0)


def test_account_equity_includes_unrealized():
    """v5 bug 回归：权益与盈亏必须含浮动盈亏，日亏风控才能被浮亏触发。"""
    acc = AccountSnapshot(TS, 1000.0, {"A": Position("A", 100, 100, 10.0, 8.0)})
    assert acc.equity == pytest.approx(1800.0)
    assert acc.unrealized_pnl == pytest.approx(-200.0)
    assert acc.weights() == {"A": pytest.approx(800 / 1800)}


def test_account_immutable_and_consistent():
    acc = AccountSnapshot(TS, 0.0, {"A": Position("A", 0, 0, 0.0, 1.0)})
    with pytest.raises(TypeError):
        acc.positions["B"] = None  # type: ignore[index]
    with pytest.raises(ValueError):
        AccountSnapshot(TS, 0.0, {"A": Position("B", 0, 0, 0.0, 1.0)})
    with pytest.raises(ValueError):
        AccountSnapshot(TS, -1.0)


def test_fill_cash_delta():
    f = Fill("o1", "A", Side.BUY, 100, 10.0, TS, commission=5.0, transfer_fee=0.01)
    assert f.cash_delta == pytest.approx(-1005.01)
    g = Fill("o2", "A", Side.SELL, 100, 11.0, TS, commission=5.0, stamp_tax=1.1)
    assert g.cash_delta == pytest.approx(1100 - 6.1)
    with pytest.raises(ValueError):
        Fill("o3", "A", Side.BUY, 100, 10.0, TS, commission=-1)


def test_order_validation():
    with pytest.raises(ValueError):
        Order("1", "A", Side.BUY, 0, TS)
    with pytest.raises(ValueError):
        Order("1", "A", Side.BUY, 100, TS, OrderType.LIMIT)
    with pytest.raises(ValueError):
        Order("1", "A", Side.BUY, 100, TS, OrderType.MARKET, 10.0)
    Order("1", "A", Side.BUY, 100, TS, OrderType.LIMIT, 10.0)


def test_bar_validation():
    Bar(TS, "A", 10, 11, 9.5, 10.5, 1000, 10500.0, 10.0)
    with pytest.raises(ValueError):
        Bar(TS, "A", 10, 9, 9.5, 10.5, 1000, 10500.0, 10.0)  # high < open
    # 停牌 bar 允许价格占位
    Bar(TS, "A", 0, 0, 0, 0, 0, 0.0, 10.0, tradable=False)


def test_target_weights():
    validate_target_weights({"A": 0.5, "B": 0.5})
    with pytest.raises(ValueError):
        validate_target_weights({"A": 0.7, "B": 0.4})
    with pytest.raises(ValueError):
        validate_target_weights({"A": -0.1})
