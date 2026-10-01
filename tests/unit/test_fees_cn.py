from datetime import date, timedelta

import pytest

from q6.core.types import Side
from q6.market.fees_cn import compute_fees, rates_on


@pytest.mark.parametrize(
    ("change", "old_buy", "old_sell", "new_buy", "new_sell"),
    [
        (date(2005, 1, 24), 0.002, 0.002, 0.001, 0.001),
        (date(2007, 5, 30), 0.001, 0.001, 0.003, 0.003),
        (date(2008, 4, 24), 0.003, 0.003, 0.001, 0.001),
        (date(2008, 9, 19), 0.001, 0.001, 0.0, 0.001),
        (date(2023, 8, 28), 0.0, 0.001, 0.0, 0.0005),
    ],
)
def test_stamp_change_day_and_previous_day(change, old_buy, old_sell, new_buy, new_sell):
    before, after = rates_on(change - timedelta(days=1)), rates_on(change)
    assert (before.stamp_buy, before.stamp_sell) == (old_buy, old_sell)
    assert (after.stamp_buy, after.stamp_sell) == (new_buy, new_sell)


@pytest.mark.parametrize(
    ("change", "old_face", "old_sh", "old_sz", "new_face", "new_sh", "new_sz"),
    [
        (date(2012, 6, 1), 0.0005, 0.0, 0.0000255, 0.000375, 0.0, 0.0000255),
        (date(2015, 8, 1), 0.000375, 0.0, 0.0000255, 0.0, 0.00002, 0.00002),
        (date(2022, 4, 29), 0.0, 0.00002, 0.00002, 0.0, 0.00001, 0.00001),
    ],
)
def test_transfer_change_day_and_previous_day(change, old_face, old_sh, old_sz, new_face, new_sh, new_sz):
    before, after = rates_on(change - timedelta(days=1)), rates_on(change)
    assert (before.transfer_sh_face, before.transfer_turnover_sh, before.transfer_turnover_sz) == (
        old_face,
        old_sh,
        old_sz,
    )
    assert (after.transfer_sh_face, after.transfer_turnover_sh, after.transfer_turnover_sz) == (
        new_face,
        new_sh,
        new_sz,
    )


def test_transfer_market_costs_at_2015_change():
    # 沪市旧值 1000×1×0.000375=0.375；新值 10000×0.00002=0.2。
    assert compute_fees("sh.600000", Side.BUY, "2015-07-31", 10, 1000).transfer_fee == pytest.approx(0.375)
    assert compute_fees("sh.600000", Side.BUY, "2015-08-01", 10, 1000).transfer_fee == pytest.approx(0.2)
    # 深市旧值 10000×0.0000255=0.255；新值 10000×0.00002=0.2。
    assert compute_fees("sz.000001", Side.BUY, "2015-07-31", 10, 1000).transfer_fee == pytest.approx(0.255)
    assert compute_fees("sz.000001", Side.BUY, "2015-08-01", 10, 1000).transfer_fee == pytest.approx(0.2)


def test_hand_calculated_examples_and_commission_minimum():
    # 2023-08-28 卖出：佣金 max(10000×0.00025,5)=5，印花税 10000×0.0005=5，过户费 10000×0.00001=0.1。
    out = compute_fees("sh.600000", Side.SELL, "2023-08-28", 10, 1000)
    assert (out.commission, out.stamp_tax, out.transfer_fee, out.total) == pytest.approx((5, 5, 0.1, 10.1))
    # 佣金最低额生效：max(1000×0.00025,5)=5。
    assert compute_fees("sz.000001", Side.BUY, "2023-08-28", 10, 100).commission == 5
    # 佣金最低额不生效：max(100000×0.00025,5)=25。
    assert compute_fees("sz.000001", Side.BUY, "2023-08-28", 10, 10000).commission == 25


def test_multiplier_scales_all_costs():
    one = compute_fees("sh.600000", Side.SELL, "2023-08-28", 10, 1000)
    two = compute_fees("sh.600000", Side.SELL, "2023-08-28", 10, 1000, cost_multiplier=2)
    assert (two.commission, two.stamp_tax, two.transfer_fee) == pytest.approx(
        (2 * one.commission, 2 * one.stamp_tax, 2 * one.transfer_fee)
    )


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"qty": -1}, "sh.600000"),
        ({"price": 0}, "sh.600000"),
        ({"price": float("nan")}, "sh.600000"),
        ({"cost_multiplier": -1}, "sh.600000"),
        ({}, "bj.830001"),
    ],
)
def test_invalid_inputs_raise(kwargs, code):
    params = {"side": Side.BUY, "d": "2023-08-28", "price": 10, "qty": 100}
    params.update(kwargs)
    with pytest.raises(ValueError):
        compute_fees(code, **params)


def test_zero_quantity_has_no_minimum_fee():
    assert compute_fees("sh.600000", Side.BUY, "2023-08-28", 10, 0).total == 0
