"""核心领域类型（P1-03，接口冻结）。

设计约束：
- 全部不可变（frozen + slots）。账户状态的演化由 engine/broker_sim 产生新快照，而不是原地改，
  这样检查点 / 续跑时可以整块序列化，不会出现"半更新"的状态。
- 构造时校验不变量。v5 的两个真 bug（avg_cost=0 导致止盈失效、日亏只算已实现盈亏）都属于
  "非法状态被静默接受"，所以这里宁可在构造时抛异常。
- 价格单位：元（float）；数量单位：股（int）；时间：datetime.datetime（日频 bar 用当日 15:00）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class Side(Enum):
    BUY = 1
    SELL = -1


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(Enum):
    NEW = "new"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class ExecStyle(Enum):
    """信号在 t 产生后，t+1 用哪种价格成交。由引擎配置决定，策略不能选"当根 bar"。"""

    OPEN_AUCTION = "open_auction"  # 次日开盘集合竞价
    VWAP = "vwap"  # 次日全天 VWAP（用 amount/volume 近似）
    CLOSE_AUCTION = "close_auction"  # 次日收盘集合竞价


def _finite(name: str, x: float) -> None:
    if not math.isfinite(x):
        raise ValueError(f"{name} 必须是有限数，得到 {x!r}")


@dataclass(frozen=True, slots=True)
class Bar:
    """单个标的单根 bar。价格均为**不复权**价（涨跌停、手数按不复权价判断）。"""

    ts: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int  # 股
    amount: float  # 元
    preclose: float  # 交易所公布的前收盘价（已考虑除权），涨跌停基准
    tradable: bool = True  # False = 停牌
    is_st: bool = False

    def __post_init__(self) -> None:
        for n in ("open", "high", "low", "close", "amount", "preclose"):
            _finite(n, getattr(self, n))
        if self.volume < 0 or self.amount < 0:
            raise ValueError(f"{self.symbol}@{self.ts}: 成交量/额为负")
        if self.tradable:
            if not (self.low <= min(self.open, self.close) and max(self.open, self.close) <= self.high):
                raise ValueError(f"{self.symbol}@{self.ts}: OHLC 不自洽 o={self.open} h={self.high} "
                                 f"l={self.low} c={self.close}")
            if self.low <= 0 or self.preclose <= 0:
                raise ValueError(f"{self.symbol}@{self.ts}: 可交易 bar 价格必须为正")


@dataclass(frozen=True, slots=True)
class Order:
    id: str
    symbol: str
    side: Side
    qty: int  # 股，>0；A 股买入须为 100 的整数倍由 market/rules_cn 校验，不在这里
    created_at: datetime  # 下单时刻 = 信号所在 bar 的时间戳；最早成交在下一根 bar
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    strategy_id: str = ""

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"订单数量必须 >0，得到 {self.qty}")
        if self.order_type is OrderType.LIMIT:
            if self.limit_price is None or not (self.limit_price > 0 and math.isfinite(self.limit_price)):
                raise ValueError("限价单必须给出正的有限 limit_price")
        elif self.limit_price is not None:
            raise ValueError("市价单不能带 limit_price")


@dataclass(frozen=True, slots=True)
class Fill:
    """一笔成交。各项成本分开记录，报告里要做成本分解，不能只留一个总数。"""

    order_id: str
    symbol: str
    side: Side
    qty: int
    price: float  # 成交价（已含滑点与冲击后的价格）
    ts: datetime
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0
    other_fees: float = 0.0  # 经手费、证管费等
    slippage_cost: float = 0.0  # 相对参考价的滑点金额（信息用途，已体现在 price 里）
    impact_cost: float = 0.0  # 冲击成本金额（信息用途，已体现在 price 里）

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError("成交数量必须 >0")
        _finite("price", self.price)
        if self.price <= 0:
            raise ValueError("成交价必须 >0")
        for n in ("commission", "stamp_tax", "transfer_fee", "other_fees"):
            v = getattr(self, n)
            _finite(n, v)
            if v < 0:
                raise ValueError(f"{n} 不能为负")

    @property
    def notional(self) -> float:
        return self.qty * self.price

    @property
    def fees(self) -> float:
        """实际从现金里扣掉的显性费用（滑点 / 冲击已在成交价里，不重复计）。"""
        return self.commission + self.stamp_tax + self.transfer_fee + self.other_fees

    @property
    def cash_delta(self) -> float:
        """这笔成交对现金的影响：买入为负，卖出为正，均已扣费。"""
        return -self.side.value * self.notional - self.fees


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    qty: int  # 总持仓（股），≥0（A 股不做空）
    sellable_qty: int  # T+1：当日买入部分不可卖
    avg_cost: float  # 加权平均持仓成本（含买入费用），qty>0 时必须 >0
    last_price: float  # 最近一次盯市价（不复权）

    def __post_init__(self) -> None:
        if self.qty < 0:
            raise ValueError(f"{self.symbol}: 持仓不能为负")
        if not 0 <= self.sellable_qty <= self.qty:
            raise ValueError(f"{self.symbol}: 可卖数量 {self.sellable_qty} 超出 [0, {self.qty}]")
        _finite("avg_cost", self.avg_cost)
        _finite("last_price", self.last_price)
        if self.qty > 0 and self.avg_cost <= 0:
            # v5 bug 回归：avg_cost 被写成 0，止盈逻辑除零后被静默吞掉
            raise ValueError(f"{self.symbol}: 有持仓时 avg_cost 必须 >0")

    @property
    def market_value(self) -> float:
        return self.qty * self.last_price

    @property
    def unrealized_pnl(self) -> float:
        return self.qty * (self.last_price - self.avg_cost)


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """某一时刻的账户全貌。equity 永远按盯市计算，风控的日亏限额以它为准（含浮亏）。"""

    ts: datetime
    cash: float
    positions: Mapping[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0  # 累计已实现盈亏（净费用）
    fees_paid: float = 0.0  # 累计显性费用

    def __post_init__(self) -> None:
        _finite("cash", self.cash)
        if self.cash < -1e-6:
            raise ValueError(f"现金为负 {self.cash}：A 股现金账户不允许透支")
        for k, p in self.positions.items():
            if k != p.symbol:
                raise ValueError(f"positions 键 {k} 与 Position.symbol {p.symbol} 不一致")
        object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))

    @property
    def market_value(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    @property
    def equity(self) -> float:
        return self.cash + self.market_value

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions.values())

    def weights(self) -> dict[str, float]:
        eq = self.equity
        if eq <= 0:
            return {}
        return {s: p.market_value / eq for s, p in self.positions.items() if p.qty > 0}


# 策略输出：目标权重（占总权益比例，≥0，合计 ≤1）。由 portfolio/ 转成订单。
TargetWeights = Mapping[str, float]


def validate_target_weights(w: TargetWeights, *, tol: float = 1e-9) -> None:
    total = 0.0
    for s, x in w.items():
        if not math.isfinite(x) or x < -tol:
            raise ValueError(f"目标权重非法 {s}={x}（A 股仅多头）")
        total += x
    if total > 1 + tol:
        raise ValueError(f"目标权重合计 {total:.6f} > 1（不允许杠杆）")
