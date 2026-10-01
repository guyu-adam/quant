"""撮合（P2-05）：信号在 t 收盘产生的订单，在 t+1 这根日线 bar 上能成交多少、什么价。

**唯一实现**：规则全部写在向量化的 `match_arrays` 里；事件引擎逐单调用的 `match_order` 只是把单个订单
包成长度 1 的数组再调它。所以两个引擎的撮合规则在构造上就一致，一致性测试（P2-09）只需要比账务。

规则按优先级（前面的命中就不再往下看，原因记在 reason 里）：
1. 停牌（bar.tradable=False）→ 不成交。
2. 新股不设涨跌幅的前几日（调用方传 no_limit_day=True）→ 不成交（保守：不参与新股前几日）。
3. 规则表外的特例日：当日最高价高于算出的涨停价、或最低价低于跌停价（重组 / 恢复上市复牌首日、
   退市整理期首日等，见 PROGRESS P2-03 全量比对）→ 不成交。
   这些状态现实中都提前公告，不算未来信息；只会少成交，偏保守。
4. 一字涨停（最高 = 最低 = 涨停价）买不进；一字跌停卖不出。
5. 参考价正好在涨停价上的买单 / 在跌停价上的卖单（封板排队）：只成交 `limit_fill_frac` 比例
   （确定性的期望值口径，不抽随机数，两个引擎逐位可比）。
   默认 0 = 封板一律成交不了，偏保守；敏感性分析里调这个参数。
6. 成交量参与率上限：不超过 `max_participation × 当日成交量`（股）。日线没有集合竞价单独的量，开盘 / 收盘
   竞价也用全天量做上限，这是已知的近似（会高估竞价容量），报告里写明。
7. 手数：买入主板 / 创业板 100 股整数倍，科创板 ≥200 股、1 股递增；卖出可一次卖完零股（rules_cn）。
8. 限价单：买单参考价 > 限价、卖单参考价 < 限价 → 不成交。限价单按参考价成交（不做价格改善）。
9. 成交价 = 参考价 × (1 ± 滑点 + 冲击)，再截到 [跌停价, 涨停价] 之内（越过涨跌停的价格不可能成交）。
10. 买单受可用现金约束：成交额 + 全部费用 ≤ cash_available，不够就按手数往下减。

参考价：OPEN_AUCTION = 开盘价；VWAP = 成交额 / 成交量；CLOSE_AUCTION = 收盘价。全部是**不复权**价。
冲击模型需要的 adv（元）和 sigma（日收益标准差）必须由调用方用 ≤t 的数据算好传进来，撮合不自己估计。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

import numpy as np

from q6.core.types import Bar, ExecStyle, Fill, Order, OrderType, Side
from q6.market import fees_cn, impact, rules_cn


class Reason(IntEnum):
    FILLED = 0
    PARTIAL = 1
    SUSPENDED = 2
    NEW_LISTING = 3
    RULE_EXCEPTION = 4
    LOCKED_LIMIT = 5  # 一字板
    LIMIT_QUEUE = 6  # 封板排队，按 limit_fill_frac 一单都没排到
    NO_VOLUME = 7  # 参与率上限后不足一手
    LIMIT_PRICE = 8  # 限价不满足
    NO_CASH = 9  # 现金不足一手
    NO_SELLABLE = 10  # 可卖数量为 0


@dataclass(frozen=True, slots=True)
class MatchConfig:
    exec_style: ExecStyle = ExecStyle.OPEN_AUCTION
    max_participation: float = 0.1
    limit_fill_frac: float = 0.0
    slippage_bp: float = 5.0
    impact_coef: float = 0.1
    cost_multiplier: float = 1.0
    commission_rate: float = 0.00025
    commission_min: float = 5.0

    def __post_init__(self) -> None:
        if not 0 < self.max_participation <= 1:
            raise ValueError("max_participation 必须在 (0, 1]")
        if not 0 <= self.limit_fill_frac <= 1:
            raise ValueError("limit_fill_frac 必须在 [0, 1]")


@dataclass(frozen=True, slots=True)
class MatchArrays:
    """match_arrays 的输出，逐订单对齐。"""

    qty: np.ndarray  # int64，成交股数（0 = 未成交）
    price: np.ndarray  # float64，成交价（未成交为 nan）
    ref_price: np.ndarray  # float64，参考价
    slippage_rate: np.ndarray
    impact_rate: np.ndarray
    reason: np.ndarray  # int8，Reason


def _ref_price(style: ExecStyle, open_, close, volume, amount) -> np.ndarray:
    if style is ExecStyle.OPEN_AUCTION:
        return open_.astype(np.float64)
    if style is ExecStyle.CLOSE_AUCTION:
        return close.astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap = np.where(volume > 0, amount / np.maximum(volume, 1), np.nan)
    return vwap


def _lot_round(codes: np.ndarray, side: np.ndarray, qty: np.ndarray, sellable: np.ndarray) -> np.ndarray:
    star = np.array([c.startswith(("sh.688", "sh.689")) for c in codes], dtype=bool)
    buy = side > 0
    out = np.where(star, np.where(qty >= rules_cn.STAR_MIN_BUY, qty, 0), qty // rules_cn.LOT * rules_cn.LOT)
    # 卖出：卖完全部可卖（含零股）总是合法；否则同买入规则
    sell_all = (~buy) & (qty >= sellable) & (sellable > 0)
    out = np.where(sell_all, sellable, out)
    return out.astype(np.int64)


def match_arrays(
    *,
    day,
    codes,
    side,
    qty,
    sellable,
    cash_available,
    limit_price,
    open_,
    high,
    low,
    close,
    volume,
    amount,
    preclose,
    tradable,
    is_st,
    no_limit_day,
    adv,
    sigma,
    cfg: MatchConfig,
) -> MatchArrays:
    """同一交易日 `day` 的一批订单逐单撮合。side: +1 买 / -1 卖；limit_price: 市价单为 nan。

    每个订单独立撮合（同一标的多张订单之间不共享参与率额度——调用方应先按标的合并为一张净订单）。
    """
    codes = np.asarray(codes, dtype=object)
    n = len(codes)
    side = np.asarray(side, dtype=np.int8)
    qty = np.asarray(qty, dtype=np.int64)
    sellable = np.asarray(sellable, dtype=np.int64)
    cash = np.asarray(cash_available, dtype=np.float64)
    lim = np.asarray(limit_price, dtype=np.float64)
    o, h, lo, c = (np.asarray(a, dtype=np.float64) for a in (open_, high, low, close))
    vol = np.asarray(volume, dtype=np.float64)
    amt = np.asarray(amount, dtype=np.float64)
    pre = np.asarray(preclose, dtype=np.float64)
    trad = np.asarray(tradable, dtype=bool)
    st = np.asarray(is_st, dtype=bool)
    nolim = np.asarray(no_limit_day, dtype=bool)
    adv = np.asarray(adv, dtype=np.float64)
    sigma = np.asarray(sigma, dtype=np.float64)
    if np.any((side != 1) & (side != -1)):
        raise ValueError("side 只能是 +1 / -1")
    if np.any(qty <= 0):
        raise ValueError("订单数量必须 >0")

    reason = np.full(n, Reason.FILLED, dtype=np.int8)
    undecided = np.ones(n, dtype=bool)

    def decide(mask: np.ndarray, why: Reason) -> None:
        nonlocal undecided
        hit = mask & undecided
        reason[hit] = why
        undecided = undecided & ~hit

    decide(~trad, Reason.SUSPENDED)
    decide(nolim, Reason.NEW_LISTING)

    # 涨跌停价只对仍待定（可交易、非新股）的订单算；停牌行的价格是占位值，不能拿去算
    up = np.full(n, np.nan)
    dn = np.full(n, np.nan)
    if undecided.any():
        idx = np.flatnonzero(undecided)
        pct = rules_cn.limit_pct_array(codes[idx], st[idx], np.full(len(idx), np.datetime64(day, "D")))
        u, d = rules_cn.limit_prices(pre[idx], pct)
        up[idx], dn[idx] = u, d
    eps = 1e-9
    buy = side > 0
    decide((h > up + eps) | (lo < dn - eps), Reason.RULE_EXCEPTION)
    locked_up = (np.abs(h - up) < eps) & (np.abs(lo - up) < eps)
    locked_dn = (np.abs(h - dn) < eps) & (np.abs(lo - dn) < eps)
    decide((buy & locked_up) | (~buy & locked_dn), Reason.LOCKED_LIMIT)
    decide(~buy & (sellable <= 0), Reason.NO_SELLABLE)

    ref = _ref_price(cfg.exec_style, o, c, vol, amt)
    decide(~np.isfinite(ref) | (ref <= 0), Reason.NO_VOLUME)
    is_lim = np.isfinite(lim)
    decide(is_lim & ((buy & (ref > lim + eps)) | (~buy & (ref < lim - eps))), Reason.LIMIT_PRICE)

    # 数量：可卖上限 → 参与率上限 → 封板排队比例 → 手数
    want = np.where(buy, qty, np.minimum(qty, sellable))
    cap = np.floor(cfg.max_participation * vol).astype(np.int64)
    q = np.minimum(want, cap)
    queue = (buy & (np.abs(ref - up) < eps)) | (~buy & (np.abs(ref - dn) < eps))
    q = np.where(queue, np.floor(q * cfg.limit_fill_frac).astype(np.int64), q)
    q = np.where(undecided, _lot_round(codes, side, q, sellable), 0)
    decide(queue & (q == 0), Reason.LIMIT_QUEUE)
    decide(q == 0, Reason.NO_VOLUME)

    # 价格：滑点 + 冲击，截到涨跌停区间
    slip, imp = impact.cost_rate_array(
        np.where(undecided, q * ref, 0.0),
        np.where(undecided, adv, 1.0),
        np.where(undecided, sigma, 0.0),
        slippage_bp=cfg.slippage_bp,
        impact_coef=cfg.impact_coef,
        cost_multiplier=cfg.cost_multiplier,
    )
    raw = np.where(buy, ref * (1 + slip + imp), ref * (1 - slip - imp))
    price = np.clip(raw, dn, up)
    price = np.round(price, 6)

    # 买单现金约束（含费用，佣金有最低 5 元，只能逐单算）
    for i in np.flatnonzero(undecided & buy):
        step = 1 if codes[i].startswith(("sh.688", "sh.689")) else rules_cn.LOT
        floor_q = rules_cn.STAR_MIN_BUY if step == 1 else rules_cn.LOT
        # 先按"不计费用"能买的最大量截一刀（只会偏多），再逐个单位往下减到连费用也付得起
        qi = min(int(q[i]), int(max(cash[i], 0.0) // price[i]) // step * step)

        def cost(k: int, i: int = i) -> float:
            fee = fees_cn.compute_fees(
                codes[i], Side.BUY, day, float(price[i]), k,
                commission_rate=cfg.commission_rate, commission_min=cfg.commission_min,
                cost_multiplier=cfg.cost_multiplier,
            )
            return k * float(price[i]) + fee.total

        while qi >= floor_q and cost(qi) > cash[i] + 1e-9:
            qi -= step
        q[i] = qi if qi >= floor_q else 0
    decide(buy & (q == 0), Reason.NO_CASH)

    q = np.where(undecided, q, 0)
    reason[undecided & (q < want)] = Reason.PARTIAL
    price = np.where(q > 0, price, np.nan)
    return MatchArrays(q, price, ref, np.where(q > 0, slip, 0.0), np.where(q > 0, imp, 0.0), reason)


def match_order(
    order: Order,
    bar: Bar,
    *,
    sellable: int,
    cash_available: float,
    no_limit_day: bool,
    adv: float,
    sigma: float,
    cfg: MatchConfig,
) -> tuple[Fill | None, Reason]:
    """事件引擎用：单张订单在下一根 bar 上撮合。订单必须产生于这根 bar 之前（不允许当根成交）。"""
    if order.symbol != bar.symbol:
        raise ValueError(f"订单 {order.symbol} 与 bar {bar.symbol} 不是同一标的")
    if not order.created_at < bar.ts:
        raise ValueError(f"订单创建于 {order.created_at}，不能在同一根或更早的 bar（{bar.ts}）上成交")
    lim = order.limit_price if order.order_type is OrderType.LIMIT else math.nan
    r = match_arrays(
        day=bar.ts.date(),
        codes=[order.symbol],
        side=[order.side.value],
        qty=[order.qty],
        sellable=[sellable],
        cash_available=[cash_available],
        limit_price=[lim],
        open_=[bar.open],
        high=[bar.high],
        low=[bar.low],
        close=[bar.close],
        volume=[bar.volume],
        amount=[bar.amount],
        preclose=[bar.preclose],
        tradable=[bar.tradable],
        is_st=[bar.is_st],
        no_limit_day=[no_limit_day],
        adv=[adv],
        sigma=[sigma],
        cfg=cfg,
    )
    why = Reason(int(r.reason[0]))
    q = int(r.qty[0])
    if q == 0:
        return None, why
    return make_fill(order.id, order.symbol, order.side, q, float(r.price[0]), float(r.ref_price[0]),
                     float(r.slippage_rate[0]), float(r.impact_rate[0]), bar.ts, cfg), why


def make_fill(order_id: str, symbol: str, side: Side, qty: int, price: float, ref: float,
              slip_rate: float, imp_rate: float, ts: datetime, cfg: MatchConfig) -> Fill:
    """按成交价算费用并拆分滑点 / 冲击金额。两个引擎都用它生成 Fill，保证费用口径一致。"""
    fee = fees_cn.compute_fees(
        symbol, side, ts.date(), price, qty,
        commission_rate=cfg.commission_rate, commission_min=cfg.commission_min,
        cost_multiplier=cfg.cost_multiplier,
    )
    # 价格被涨跌停截断时，实际的价格让步小于模型值：按实际让步金额、按两者比例拆分
    total = abs(price - ref) * qty
    rate = slip_rate + imp_rate
    slip_cost = total * slip_rate / rate if rate > 0 else 0.0
    return Fill(
        order_id=order_id, symbol=symbol, side=side, qty=qty, price=price, ts=ts,
        commission=fee.commission, stamp_tax=fee.stamp_tax, transfer_fee=fee.transfer_fee,
        slippage_cost=slip_cost, impact_cost=total - slip_cost,
    )
