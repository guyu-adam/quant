"""账户（P2-06）：持仓成本、T+1、除权、盯市，以及 hypothesis 不变量。"""

from datetime import datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from q6.core.types import Fill, Side
from q6.engine.broker_sim import BrokerError, BrokerSim

D0 = datetime(2021, 3, 1, 15)


def fill(side, qty, price, ts=D0, sym="sz.000001", fees=5.0):
    return Fill("o", sym, side, qty, price, ts, commission=fees)


def test_avg_cost_includes_buy_fees_and_is_never_zero():
    b = BrokerSim(100_000, D0)
    b.apply_fill(fill(Side.BUY, 1000, 10.0, fees=5.0))
    b.apply_fill(fill(Side.BUY, 1000, 12.0, fees=6.0))
    p = b.state.positions["sz.000001"]
    assert p.avg_cost == pytest.approx((10_000 + 5 + 12_000 + 6) / 2000)
    assert p.avg_cost > 0  # v5 回归：avg_cost 被写成 0
    assert b.state.cash == pytest.approx(100_000 - 22_011)


def test_t_plus_1():
    b = BrokerSim(100_000, D0)
    b.apply_fill(fill(Side.BUY, 1000, 10.0))
    assert b.sellable("sz.000001") == 0
    with pytest.raises(BrokerError, match="T\\+1"):
        b.apply_fill(fill(Side.SELL, 100, 10.0))
    b.start_of_day(D0 + timedelta(days=1))
    assert b.sellable("sz.000001") == 1000
    b.apply_fill(fill(Side.BUY, 500, 10.0, ts=D0 + timedelta(days=1)))
    assert b.sellable("sz.000001") == 1000  # 当日新买的 500 不可卖


def test_sell_realized_pnl_and_close_out():
    b = BrokerSim(100_000, D0)
    b.apply_fill(fill(Side.BUY, 1000, 10.0, fees=5.0))
    b.start_of_day(D0 + timedelta(days=1))
    b.apply_fill(fill(Side.SELL, 1000, 11.0, ts=D0 + timedelta(days=1), fees=16.0))
    assert "sz.000001" not in b.state.positions
    assert b.state.realized_pnl == pytest.approx(1000 * (11.0 - 10.005) - 16.0)
    assert b.state.equity == pytest.approx(100_000 + b.state.realized_pnl)
    assert b.state.fees_paid == pytest.approx(21.0)


def test_cash_overdraft_rejected():
    b = BrokerSim(1_000, D0)
    with pytest.raises(BrokerError, match="现金不足"):
        b.apply_fill(fill(Side.BUY, 100, 10.0, fees=5.0))


def test_ten_for_ten_bonus_keeps_wealth():
    b = BrokerSim(100_000, D0)
    b.apply_fill(fill(Side.BUY, 1000, 20.0, fees=0.0))
    b.mark(D0, {"sz.000001": 20.0})
    eq = b.state.equity
    b.start_of_day(D0 + timedelta(days=1), {"sz.000001": 2.0})  # 10 送 10：前收 10
    p = b.state.positions["sz.000001"]
    assert (p.qty, p.sellable_qty, p.avg_cost, p.last_price) == (2000, 2000, 10.0, 10.0)
    assert b.state.equity == pytest.approx(eq)


def test_cash_dividend_fractional_share_to_cash():
    b = BrokerSim(100_000, D0)
    b.apply_fill(fill(Side.BUY, 1000, 10.0, fees=0.0))
    b.mark(D0, {"sz.000001": 10.2})
    eq = b.state.equity
    b.start_of_day(D0 + timedelta(days=1), {"sz.000001": 10.2 / 10.0})  # 每股派 0.2，前收 10.0
    p = b.state.positions["sz.000001"]
    assert p.qty == 1020 and p.last_price == pytest.approx(10.0)
    assert b.state.equity == pytest.approx(eq)
    assert b.invariant_gap() == pytest.approx(0, abs=1e-6)


def test_mark_keeps_suspended_price():
    b = BrokerSim(100_000, D0)
    b.apply_fill(fill(Side.BUY, 100, 10.0))
    b.mark(D0, {"sz.000001": 10.5})
    b.mark(D0 + timedelta(days=1), {})
    assert b.state.positions["sz.000001"].last_price == 10.5
    with pytest.raises(BrokerError):
        b.mark(D0, {"sz.000001": float("nan")})


SYMS = ["sh.600000", "sz.000001", "sz.300750"]

op = st.one_of(
    st.tuples(
        st.just("buy"), st.sampled_from(SYMS), st.integers(1, 30), st.floats(1.0, 100.0), st.floats(0.0, 20.0)
    ),
    st.tuples(
        st.just("sell"),
        st.sampled_from(SYMS),
        st.floats(0.0, 1.0),
        st.floats(1.0, 100.0),
        st.floats(0.0, 20.0),
    ),
    st.tuples(st.just("mark"), st.sampled_from(SYMS), st.floats(0.5, 150.0)),
    st.tuples(st.just("day"), st.sampled_from(SYMS), st.sampled_from([1.0, 1.0, 1.013, 1.5, 2.0])),
)


@settings(max_examples=300, deadline=None)
@given(st.lists(op, min_size=1, max_size=60))
def test_invariants_hold_for_any_sequence(ops):
    b = BrokerSim(1_000_000, D0)
    ts = D0
    for o in ops:
        kind, sym = o[0], o[1]
        if kind == "buy":
            qty, px, fee = o[2] * 100, round(o[3], 2), o[4]
            if qty * px + fee > b.state.cash:
                continue
            b.apply_fill(Fill("o", sym, Side.BUY, qty, px, ts, commission=fee))
        elif kind == "sell":
            can = b.sellable(sym)
            qty = int(can * o[2])
            if qty == 0:
                continue
            b.apply_fill(Fill("o", sym, Side.SELL, qty, round(o[3], 2), ts, commission=o[4]))
        elif kind == "mark":
            b.mark(ts, {sym: round(o[2], 2)})
        else:
            ts = ts + timedelta(days=1)
            b.start_of_day(ts, {sym: o[2]})
        s = b.state
        assert s.cash >= -1e-6
        for p in s.positions.values():
            assert p.qty > 0 and 0 <= p.sellable_qty <= p.qty and p.avg_cost > 0
        assert abs(b.invariant_gap()) <= 1e-6 * max(1.0, s.equity)
