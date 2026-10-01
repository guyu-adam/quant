"""撮合（P2-05）：每条规则一个用例 + 标量入口与数组入口逐位一致。"""

import math
from datetime import datetime

import numpy as np
import pytest

from q6.core.types import Bar, ExecStyle, Order, OrderType, Side
from q6.engine.matching import MatchConfig, Reason, match_arrays, match_order

T0 = datetime(2021, 3, 1, 15)
T1 = datetime(2021, 3, 2, 15)
CFG = MatchConfig()


def bar(sym="sz.000001", o=10.0, h=10.5, lo=9.8, c=10.2, vol=1_000_000, pre=10.0, **kw):
    amount = kw.pop("amount", vol * (o + c) / 2)
    return Bar(T1, sym, o, h, lo, c, vol, amount, pre, **kw)


def order(side=Side.BUY, qty=1000, sym="sz.000001", **kw):
    return Order("o1", sym, side, qty, T0, **kw)


def m(o, b, *, sellable=0, cash=1e9, no_limit=False, adv=1e8, sigma=0.02, cfg=CFG):
    return match_order(
        o, b, sellable=sellable, cash_available=cash, no_limit_day=no_limit, adv=adv, sigma=sigma, cfg=cfg
    )


def test_normal_buy_fills_at_open_plus_cost():
    f, why = m(order(), bar())
    assert why is Reason.FILLED and f.qty == 1000
    assert f.price > 10.0 and f.price < 10.0 * 1.01  # 开盘价 + 滑点 5bp + 冲击
    assert f.commission == pytest.approx(5.0)  # 1 万元成交，万 2.5 佣金不到 5 元，按最低 5 元
    assert f.stamp_tax == 0.0  # 2021 年买入不收印花税
    assert f.slippage_cost + f.impact_cost == pytest.approx((f.price - 10.0) * 1000)


def test_sell_pays_stamp_tax():
    f, why = m(order(Side.SELL, 1000), bar(), sellable=1000)
    assert why is Reason.FILLED and f.price < 10.0
    assert f.stamp_tax == pytest.approx(f.notional * 0.001)


def test_one_word_limit_up_cannot_buy_but_can_sell():
    b = bar(o=11.0, h=11.0, lo=11.0, c=11.0)  # 前收 10 → 涨停 11.00
    assert m(order(), b) == (None, Reason.LOCKED_LIMIT)
    f, why = m(order(Side.SELL, 500), b, sellable=500)
    # 一字板当天真实成交价只有 11.00；模型卖价 = 11.00 × (1 − 成本)，仍在涨跌停区间内，偏保守
    assert why is Reason.FILLED and f.qty == 500 and 10.9 < f.price < 11.0


def test_one_word_limit_down_cannot_sell_but_can_buy():
    b = bar(o=9.0, h=9.0, lo=9.0, c=9.0)
    assert m(order(Side.SELL, 500), b, sellable=500) == (None, Reason.LOCKED_LIMIT)
    f, why = m(order(), b)
    assert why is Reason.FILLED and 9.0 < f.price < 9.1


def test_price_clipped_to_limit_band():
    # 开盘就在涨停价 - 0.01，买入加成本后会越过涨停价 → 截到 11.00
    b = bar(o=10.99, h=11.0, lo=10.9, c=10.95)
    f, _ = m(order(), b, adv=1e5, sigma=0.05)  # 大冲击
    assert f.price == 11.0
    assert f.slippage_cost + f.impact_cost == pytest.approx(0.01 * f.qty)


def test_queue_at_limit_up_default_no_fill_and_fraction():
    b = bar(o=11.0, h=11.0, lo=10.6, c=10.8)  # 开盘封涨停、盘中打开：开盘竞价买单在排队
    assert m(order(), b) == (None, Reason.LIMIT_QUEUE)
    f, why = m(order(qty=1000), b, cfg=MatchConfig(limit_fill_frac=0.5))
    assert why is Reason.PARTIAL and f.qty == 500 and f.price == 11.0


def test_suspended_and_new_listing_and_rule_exception():
    assert m(order(), bar(tradable=False)) == (None, Reason.SUSPENDED)
    assert m(order(), bar(), no_limit=True) == (None, Reason.NEW_LISTING)
    exc = bar(o=13.0, h=14.0, lo=12.0, c=13.5)  # 前收 10，最高 14 远超涨停 11：复牌首日类特例
    assert m(order(), exc) == (None, Reason.RULE_EXCEPTION)
    assert m(order(Side.SELL, 100), exc, sellable=100) == (None, Reason.RULE_EXCEPTION)


def test_participation_cap_partial_fill():
    b = bar(vol=12_345)  # 10% = 1234 股 → 1200
    f, why = m(order(qty=5000), b)
    assert why is Reason.PARTIAL and f.qty == 1200
    assert m(order(qty=5000), bar(vol=900)) == (None, Reason.NO_VOLUME)


def test_lots_main_and_star():
    f, _ = m(order(qty=1999), bar())
    assert f.qty == 1900
    assert m(order(qty=199, sym="sh.688981"), bar(sym="sh.688981")) == (None, Reason.NO_VOLUME)
    f, _ = m(order(qty=201, sym="sh.688981"), bar(sym="sh.688981"))
    assert f.qty == 201


def test_sell_odd_lot_all_at_once_and_t1():
    f, why = m(order(Side.SELL, 150), bar(), sellable=150)
    assert why is Reason.FILLED and f.qty == 150
    f, why = m(order(Side.SELL, 120), bar(), sellable=150)
    assert why is Reason.PARTIAL and f.qty == 100
    assert m(order(Side.SELL, 100), bar(), sellable=0) == (None, Reason.NO_SELLABLE)


def test_limit_order():
    assert m(order(order_type=OrderType.LIMIT, limit_price=9.9), bar()) == (None, Reason.LIMIT_PRICE)
    f, _ = m(order(order_type=OrderType.LIMIT, limit_price=10.0), bar())
    assert f is not None


def test_cash_constraint_includes_fees():
    f, why = m(order(qty=1000), bar(), cash=5_000.0)
    assert why is Reason.PARTIAL and f.qty == 400
    assert f.notional + f.fees <= 5_000.0
    assert m(order(qty=1000), bar(), cash=900.0) == (None, Reason.NO_CASH)


def test_exec_styles():
    b = bar(o=10.0, c=10.4, vol=1_000_000, amount=10_300_000.0)
    for style, ref in [
        (ExecStyle.OPEN_AUCTION, 10.0),
        (ExecStyle.VWAP, 10.3),
        (ExecStyle.CLOSE_AUCTION, 10.4),
    ]:
        f, _ = m(order(), b, cfg=MatchConfig(exec_style=style, slippage_bp=0.0, impact_coef=0.0))
        assert f.price == pytest.approx(ref)


def test_same_bar_execution_rejected():
    o = Order("x", "sz.000001", Side.BUY, 100, T1)
    with pytest.raises(ValueError, match="不能在同一根"):
        m(o, bar())


def test_scalar_and_array_paths_identical():
    rng = np.random.default_rng(7)
    n = 400
    codes = rng.choice(["sz.000001", "sh.600000", "sz.300750", "sh.688981"], n)
    pre = np.round(rng.uniform(3, 60, n), 2)
    o = np.round(pre * rng.uniform(0.9, 1.1, n), 2)
    c = np.round(pre * rng.uniform(0.9, 1.1, n), 2)
    h = np.maximum(o, c) + np.round(rng.uniform(0, 0.5, n), 2)
    lo = np.minimum(o, c) - np.round(rng.uniform(0, 0.5, n), 2)
    lock = rng.random(n) < 0.1  # 造一字板
    up = np.floor(pre * 110 + 0.5) / 100
    o, h, lo, c = (np.where(lock, up, a) for a in (o, h, lo, c))
    vol = rng.integers(0, 200_000, n)
    side = rng.choice([1, -1], n)
    qty = rng.integers(1, 30, n) * 100 + rng.integers(0, 2, n) * 37
    sellable = rng.integers(0, 3000, n)
    cash = rng.uniform(0, 200_000, n)
    trad = rng.random(n) > 0.05
    cfg = MatchConfig(limit_fill_frac=0.3)
    arr = match_arrays(
        day=T1.date(),
        codes=codes,
        side=side,
        qty=qty,
        sellable=sellable,
        cash_available=cash,
        limit_price=np.full(n, np.nan),
        open_=o,
        high=h,
        low=lo,
        close=c,
        volume=vol,
        amount=vol * (o + c) / 2,
        preclose=pre,
        tradable=trad,
        is_st=np.zeros(n, bool),
        no_limit_day=np.zeros(n, bool),
        adv=np.full(n, 5e7),
        sigma=np.full(n, 0.03),
        cfg=cfg,
    )
    for i in range(n):
        b = Bar(
            T1,
            codes[i],
            o[i],
            h[i],
            lo[i],
            c[i],
            int(vol[i]),
            float(vol[i] * (o[i] + c[i]) / 2),
            pre[i],
            tradable=bool(trad[i]),
        )
        od = Order(str(i), codes[i], Side(int(side[i])), int(qty[i]), T0)
        f, why = match_order(
            od,
            b,
            sellable=int(sellable[i]),
            cash_available=float(cash[i]),
            no_limit_day=False,
            adv=5e7,
            sigma=0.03,
            cfg=cfg,
        )
        assert int(why) == int(arr.reason[i])
        assert (f.qty if f else 0) == int(arr.qty[i])
        if f:
            assert f.price == arr.price[i]
        else:
            assert math.isnan(arr.price[i])
    # 各种原因都被覆盖到了，不是全部走同一条分支
    assert len(set(arr.reason.tolist())) >= 6


def test_cash_boundary_where_only_fees_decide():
    full, _ = m(order(qty=1000), bar())
    cash = full.notional + 1.0  # 够买 1000 股的成交额，但付不起 5 元最低佣金
    f, why = m(order(qty=1000), bar(), cash=cash)
    assert why is Reason.PARTIAL and f.qty == 900
    f, why = m(order(qty=1000), bar(), cash=full.notional + full.fees)
    assert why is Reason.FILLED and f.qty == 1000
