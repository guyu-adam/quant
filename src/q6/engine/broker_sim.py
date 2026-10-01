"""账户与模拟券商（P2-06）：持仓成本、T+1 可卖、现金守恒、盯市、除权除息。

状态全部是不可变的 AccountSnapshot，每一步产生新快照（检查点直接序列化快照即可，不会有半更新状态）。

口径：
- 持仓成本 avg_cost = 加权平均，**含买入费用**；卖出不改变 avg_cost。
- 已实现盈亏（卖出时）= (成交价 − avg_cost) × 数量 − 卖出费用。买入费用通过 avg_cost 进入盈亏，不重复记。
- T+1：当日买入的数量不可卖；`start_of_day` 把全部持仓解锁为可卖。
- 盯市：停牌标的保留最近一次价格。权益 = 现金 + 盯市市值，风控日亏限额用它（含浮亏）。
- 除权除息：价格是不复权的，除权日如果不处理，持仓会凭空"亏掉"送转部分。用复权因子比
  ratio = 前一日收盘 / 当日前收（= 后复权因子之比）把持仓数量放大 ratio 倍，avg_cost 除以 ratio，
  不足 1 股的零头按前收折成现金（同时结转对应成本）。对送转股和拆股是精确的；对现金分红相当于按前收
  再投资，**没有扣红利税**（持有 <1 个月 20%、1 个月 ~ 1 年 10%），结果偏乐观，PROGRESS 已记录。

不变量（hypothesis 测试）：权益 = 初始资金 + 累计已实现盈亏 + 浮动盈亏。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime

from q6.core.types import AccountSnapshot, Fill, Position, Side


class BrokerError(RuntimeError):
    pass


class BrokerSim:
    def __init__(self, initial_cash: float, ts: datetime) -> None:
        if not (initial_cash > 0 and math.isfinite(initial_cash)):
            raise ValueError("初始资金必须是正的有限数")
        self.initial_cash = float(initial_cash)
        self.state = AccountSnapshot(ts=ts, cash=float(initial_cash))

    # ------------------------------------------------------------ 日内事件
    def start_of_day(self, ts: datetime, ratios: Mapping[str, float] | None = None) -> AccountSnapshot:
        """开盘前：先处理除权除息（ratios 只需给出 ≠1 的标的），再把全部持仓解锁为可卖（T+1）。"""
        st = self.state
        if ts < st.ts:
            raise BrokerError(f"时间倒流：{ts} < {st.ts}")
        cash, realized = st.cash, st.realized_pnl
        positions: dict[str, Position] = {}
        for sym, p in st.positions.items():
            r = 1.0 if ratios is None else float(ratios.get(sym, 1.0))
            if not (r > 0 and math.isfinite(r)):
                raise BrokerError(f"{sym} 除权比例非法 {r}")
            qty, avg, last = p.qty, p.avg_cost, p.last_price
            if r != 1.0:
                exact = p.qty * r
                qty = int(math.floor(exact + 1e-9))
                frac = exact - qty
                ref = p.last_price / r  # 除权参考价 = 前收（last_price 是前一日收盘）
                avg = p.avg_cost / r
                cash += frac * ref  # 零头按参考价折现
                realized += frac * (ref - avg)  # 零头对应的成本随之结转
                last = ref
            if qty > 0:
                positions[sym] = Position(sym, qty, qty, avg, last)
            elif p.qty > 0:
                raise BrokerError(f"{sym} 除权后持仓变为 0（比例 {r}），数据异常")
        self.state = AccountSnapshot(ts, cash, positions, realized, st.fees_paid)
        return self.state

    def apply_fill(self, fill: Fill) -> AccountSnapshot:
        st = self.state
        if fill.ts < st.ts:
            raise BrokerError(f"成交时间 {fill.ts} 早于账户时间 {st.ts}")
        positions = dict(st.positions)
        p = positions.get(fill.symbol)
        cash = st.cash + fill.cash_delta
        realized = st.realized_pnl
        if fill.side is Side.BUY:
            if cash < -1e-6:
                raise BrokerError(f"现金不足：买入 {fill.symbol} {fill.qty} 股后现金 {cash:.2f}")
            old_qty = p.qty if p else 0
            old_cost = old_qty * p.avg_cost if p else 0.0
            new_qty = old_qty + fill.qty
            avg = (old_cost + fill.notional + fill.fees) / new_qty
            positions[fill.symbol] = Position(
                fill.symbol, new_qty, p.sellable_qty if p else 0, avg, fill.price if not p else p.last_price
            )
        else:
            if p is None or fill.qty > p.sellable_qty:
                have = p.sellable_qty if p else 0
                raise BrokerError(f"卖出 {fill.symbol} {fill.qty} 股超过可卖 {have}（T+1 / 无持仓）")
            realized += (fill.price - p.avg_cost) * fill.qty - fill.fees
            left = p.qty - fill.qty
            if left:
                positions[fill.symbol] = Position(
                    fill.symbol, left, p.sellable_qty - fill.qty, p.avg_cost, p.last_price
                )
            else:
                del positions[fill.symbol]
        self.state = AccountSnapshot(fill.ts, cash, positions, realized, st.fees_paid + fill.fees)
        return self.state

    def mark(self, ts: datetime, prices: Mapping[str, float]) -> AccountSnapshot:
        """收盘盯市。prices 里没有的标的（停牌）保留上一次价格。"""
        st = self.state
        positions = {}
        for sym, p in st.positions.items():
            px = prices.get(sym)
            if px is not None and not (px > 0 and math.isfinite(px)):
                raise BrokerError(f"{sym} 盯市价格非法 {px}")
            positions[sym] = Position(
                sym, p.qty, p.sellable_qty, p.avg_cost, p.last_price if px is None else px
            )
        self.state = AccountSnapshot(ts, st.cash, positions, st.realized_pnl, st.fees_paid)
        return self.state

    # ------------------------------------------------------------ 查询
    def sellable(self, symbol: str) -> int:
        p = self.state.positions.get(symbol)
        return p.sellable_qty if p else 0

    def invariant_gap(self) -> float:
        """权益 − (初始资金 + 已实现 + 浮动)。应恒为 0（浮点误差内）。"""
        st = self.state
        return st.equity - (self.initial_cash + st.realized_pnl + st.unrealized_pnl)
