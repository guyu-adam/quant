"""事件引擎（P2-07）：逐交易日、逐订单推进，是两个引擎里的"参照实现"。

每个交易日 i 的固定处理顺序（向量化引擎必须逐步复刻，一致性测试 P2-09 按这个顺序比对）：
1. 开盘前：除权除息（比例 = 当日复权因子 / 该持仓上一次见到的复权因子）→ T+1 解锁。
2. 撮合 i-1 收盘后生成的订单，用第 i 根 bar：**先卖后买**，同方向按代码排序；卖出回笼的现金当日可用于买入。
3. 收盘盯市（停牌标的保留旧价）。
4. 策略在 i 收盘后决策（只拿到 ≤i 的 PITView）→ 目标权重 → 生成订单，留到 i+1 撮合。
5. 记录当日账户。

信号在 t 收盘产生、t+1 成交，这一点由结构保证：订单只在第 4 步产生、只在下一天的第 2 步撮合，
且 matching.match_order 拒绝 created_at ≥ bar.ts 的订单。

目标权重 → 订单：目标市值 = 权重 × 当日收盘权益；差额 / 当日收盘价（不复权）= 股数，手数取整交给撮合。
差额绝对值小于 min_trade_value 的不交易（避免碎单）。权重为 0 的标的清仓。
退市：持仓标的连续 delist_after 个交易日没有任何行情行（停牌日数据源仍有 tradestatus=0 的行，所以"没有行"
意味着已不在交易所挂牌——P2-07 实测全部是换股吸收合并），在第 delist_after 天收盘按最后盯市价 × delist_recovery
折成现金，逐笔记进 BacktestResult.delistings。之前的缺行日照常按最后价估值，计数在 reasons["MISSING_ROW"]。

冲击模型的 adv（近 20 日平均成交额）和 sigma（近 20 日收益标准差）用决策日 ≤t 的数据算好，随订单带到 t+1。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time
from itertools import count

import numpy as np
import pandas as pd

from q6.core.clock import BacktestClock
from q6.core.types import Bar, Fill, Order, Side, validate_target_weights
from q6.engine.broker_sim import BrokerSim
from q6.engine.feed import SnapshotFeed
from q6.engine.matching import MatchConfig, Reason, match_order
from q6.risk.monitor import RiskConfig, RiskMonitor
from q6.strategy.base import BarContext, Strategy

CLOSE = time(15, 0)
_ROW_FIELDS = ("open", "high", "low", "close", "preclose", "volume", "amount",
               "tradestatus", "is_st", "is_new", "adj_factor", "bad")


@dataclass(frozen=True)
class EngineConfig:
    initial_cash: float = 1_000_000.0
    match: MatchConfig = field(default_factory=MatchConfig)
    min_trade_value: float = 2_000.0
    adv_window: int = 20
    sigma_window: int = 20
    delist_after: int = 20
    delist_recovery: float = 1.0
    risk: RiskConfig | None = None  # None = 不启用风控（P2 研究默认）；启用时见 risk_targets


@dataclass
class _Pending:
    order: Order
    adv: float
    sigma: float


@dataclass
class BacktestResult:
    # daily：index=date；列 equity, cash, market_value, n_pos, buy_value, sell_value, fees, cost_slip_imp
    daily: pd.DataFrame
    fills: list[Fill]
    reasons: dict[str, int]  # 未成交 / 部分成交原因计数；MISSING_ROW = 持仓标的当日没有行情行的天数
    initial_cash: float
    delistings: list[dict] = field(default_factory=list)  # date, symbol, qty, last_price, value, cost
    risk_events: list[dict] = field(default_factory=list)  # RiskMonitor.events

    @property
    def returns(self) -> pd.Series:
        """日收益：首日相对初始资金，之后相对前一日权益。"""
        eq = self.daily["equity"].to_numpy()
        prev = np.concatenate(([self.initial_cash], eq[:-1]))
        return pd.Series(eq / prev - 1.0, index=self.daily.index, name="ret")


def _ts(d) -> datetime:
    return datetime.combine(pd.Timestamp(d).date(), CLOSE)


def _bar(rows: dict[str, np.ndarray], j: int, sym: str, ts: datetime) -> Bar:
    tradable = rows["tradestatus"][j] == 1 and rows["bad"][j] != 1 and math.isfinite(rows["close"][j])
    if not tradable:
        return Bar(ts, sym, 1.0, 1.0, 1.0, 1.0, 0, 0.0, 1.0, tradable=False)
    return Bar(ts, sym, rows["open"][j], rows["high"][j], rows["low"][j], rows["close"][j],
               int(rows["volume"][j]), float(rows["amount"][j]), rows["preclose"][j],
               tradable=True, is_st=bool(rows["is_st"][j] == 1))


def risk_targets(monitor: RiskMonitor | None, ts: datetime, equity: float,
                 positions: dict[str, float], target: dict[str, float] | None) -> dict[str, float] | None:
    """第 3 步盯市之后、第 4 步下单之前的风控（架构 §4.4 的固定顺序：撮合 → 盯市 → 风控 → 策略 → 下单）。
    positions：代码 → 持仓市值。熔断触发当天强制按 当前持仓 × breaker_scale 调仓（策略返回 None 也执行）；
    其余触发状态下只许减仓。两个引擎共用这个函数。"""
    if monitor is None:
        return target
    monitor.on_mark(ts, equity)
    cur = {s: v / equity for s, v in positions.items()}
    if monitor.breaker_triggered:
        return monitor.filter_target(target or {}, cur)
    if target is None:
        return None
    return monitor.filter_target(target, cur)


class EventEngine:
    def __init__(self, cfg: EngineConfig | None = None) -> None:
        self.cfg = cfg or EngineConfig()

    def run(self, strategy: Strategy, feed: SnapshotFeed, start, end,
            on_day: Callable[[pd.Timestamp, BrokerSim], None] | None = None) -> BacktestResult:
        r = EngineRun(self, strategy, feed, start, end)
        for day in r.days():
            if on_day is not None:
                on_day(day, r.broker)
        return r.result()


class EngineRun:
    """一次可暂停、可续跑的回放（P3-02）。`EventEngine.run` 就是把它从头跑到尾，所以 Mac 回测和 Win 模拟盘
    走的是同一份逐日代码，不存在两套实现。

    - `days()`：生成器，每处理完一个交易日 yield 一次该日期；调用方可以在两次 yield 之间做检查点、限速、心跳。
    - `state_dict()` / `load_state_dict()`：除行情数据外的全部状态（账户、在途订单、除权基准、订单编号、
      原因计数、缺行计数、时钟、风控、**策略对象本身**、当前数据段的代码集合）。策略接口（P2-14）已冻结，
      所以不要求策略实现 state_dict——整体 pickle 策略对象（已注册策略都是普通对象，可 pickle）。
      没有随机数：撮合的排队模型是确定性期望值口径，引擎不持有 RNG。
    - `drain()`：取走并清空已产生的日记录 / 成交 / 退市记录（模拟盘每个检查点落库一次，内存不随长度增长）。

    续跑：先 load_state_dict 再 days()。数据段按"检查点所在年份"重新加载，并且用**当初加载该段时的代码集合**
    （不是续跑时的持仓），所以面板的行列与不中断运行完全相同；已处理的日子按日期跳过。
    """

    def __init__(self, engine: EventEngine, strategy: Strategy, feed: SnapshotFeed, start, end) -> None:
        missing = set(strategy.spec.fields) - set(feed.fields)
        if missing:
            raise ValueError(f"策略需要的字段 {sorted(missing)} 没有加载（SnapshotFeed(extra_fields=...)）")
        self.cfg = engine.cfg
        self.strategy = strategy
        self.feed = feed
        self.start, self.end = pd.Timestamp(start), pd.Timestamp(end)
        self.broker: BrokerSim | None = None
        self.pending: list[_Pending] = []
        self.last_adj: dict[str, float] = {}
        self.next_id = 0
        self.records: list[dict] = []
        self.fills: list[Fill] = []
        self.reasons: dict[str, int] = {}
        self.absent: dict[str, int] = {}  # 持仓标的连续无行情行的天数
        self.delistings: list[dict] = []
        self.clock = BacktestClock()  # 跨段的日期必须严格前进，段衔接写错（重复 / 倒序）会在这里报错
        self.monitor = RiskMonitor(self.cfg.risk) if self.cfg.risk is not None else None
        self.last_day: pd.Timestamp | None = None
        self.n_days = 0
        self.seg_keep: frozenset[str] | None = None  # 当前数据段加载时 keep() 返回的代码
        self._resume_keep: frozenset[str] | None = None
        self._skip_until: pd.Timestamp | None = None

    # ------------------------------------------------------------ 检查点
    _STATE = ("broker", "pending", "last_adj", "next_id", "reasons", "absent", "clock", "monitor",
              "strategy", "last_day", "n_days", "seg_keep")

    def state_dict(self) -> dict:
        return {k: getattr(self, k) for k in self._STATE}

    def load_state_dict(self, d: dict) -> None:
        if self.n_days or self.records:
            raise RuntimeError("只能在开始回放之前载入检查点")
        for k in self._STATE:
            setattr(self, k, d[k])
        self._resume_keep = self.seg_keep
        self._skip_until = self.last_day

    def drain(self) -> tuple[list[dict], list[Fill], list[dict]]:
        out = (self.records, self.fills, self.delistings)
        self.records, self.fills, self.delistings = [], [], []
        return out

    def result(self) -> BacktestResult:
        daily = pd.DataFrame(self.records).set_index("date")
        return BacktestResult(daily, self.fills, self.reasons, self.cfg.initial_cash, self.delistings,
                              self.monitor.events if self.monitor is not None else [])

    # ------------------------------------------------------------ 回放
    def _keep(self) -> frozenset[str]:
        if self._resume_keep is not None:
            codes, self._resume_keep = self._resume_keep, None
        else:
            held = set(self.broker.state.positions) if self.broker is not None else set()
            codes = frozenset(held | {x.order.symbol for x in self.pending})
        self.seg_keep = codes
        return codes

    def days(self):
        start = self.start
        if self.last_day is not None:  # 续跑：从检查点所在年份的数据段开始（与不中断运行同一段）
            start = max(start, pd.Timestamp(self.last_day.year, 1, 1))
        for seg in self.feed.segments(start, self.end, self.strategy.spec.warmup, keep=self._keep):
            p = seg.panel
            sym_idx = {s: j for j, s in enumerate(p.symbols)}
            for i in range(seg.first, len(p)):
                day = pd.Timestamp(p.date_at(i))
                if self._skip_until is not None and day <= self._skip_until:
                    continue  # 续跑：检查点之前已处理过的日子
                if self.last_day is not None and day <= self.last_day:
                    raise RuntimeError(f"交易日重复或倒序：{day.date()} 不晚于 {self.last_day.date()}")
                self._step(p, seg, sym_idx, i, day)
                yield day
            # 放掉本段面板的全部引用，再让数据源加载下一段；否则两段面板同时在内存里
            seg = p = None  # noqa: F841

    def _step(self, p, seg, sym_idx, i: int, day: pd.Timestamp) -> None:
        cfg, strategy = self.cfg, self.strategy
        spec = strategy.spec
        ts = _ts(day)
        self.clock.advance(ts)
        if self.broker is None:
            self.broker = BrokerSim(cfg.initial_cash, ts)
        broker, reasons, absent, last_adj = self.broker, self.reasons, self.absent, self.last_adj
        rows = {f: p.row(f, i) for f in _ROW_FIELDS}

        # 1. 除权除息 + T+1 解锁
        ratios = {}
        for s in broker.state.positions:
            j = sym_idx.get(s)
            a = rows["adj_factor"][j] if j is not None else math.nan
            if math.isfinite(a) and s in last_adj and a != last_adj[s]:
                ratios[s] = a / last_adj[s]
        broker.start_of_day(ts, ratios)

        # 2. 撮合：先卖后买，同方向按代码
        buy_value = sell_value = cost_si = 0.0
        n_fees_before = broker.state.fees_paid
        for pend in sorted(self.pending, key=lambda x: (x.order.side is Side.BUY, x.order.symbol)):
            o = pend.order
            j = sym_idx.get(o.symbol)
            if j is None:  # 本段面板里没有这只（已退市且无行）：等同停牌
                reasons[Reason.SUSPENDED.name] = reasons.get(Reason.SUSPENDED.name, 0) + 1
                continue
            bar = _bar(rows, j, o.symbol, ts)
            f, why = match_order(
                o, bar, sellable=broker.sellable(o.symbol), cash_available=broker.state.cash,
                no_limit_day=bool(rows["is_new"][j] == 1),
                adv=pend.adv, sigma=pend.sigma, cfg=cfg.match,
            )
            if why is not Reason.FILLED:
                reasons[why.name] = reasons.get(why.name, 0) + 1
            if f is None:
                continue
            broker.apply_fill(f)
            self.fills.append(f)
            strategy.on_fill(f)
            cost_si += f.slippage_cost + f.impact_cost
            if f.side is Side.BUY:
                buy_value += f.notional
            else:
                sell_value += f.notional
        self.pending = []

        # 3. 盯市；记下持仓今天的复权因子，明天开盘据此算除权比例
        closes = {s: float(rows["close"][j]) for s in broker.state.positions
                  if (j := sym_idx.get(s)) is not None and rows["tradestatus"][j] == 1
                  and math.isfinite(rows["close"][j])}
        broker.mark(ts, closes)
        for s in list(broker.state.positions):
            j = sym_idx.get(s)
            if j is None or not math.isfinite(rows["tradestatus"][j]):
                reasons["MISSING_ROW"] = reasons.get("MISSING_ROW", 0) + 1
                absent[s] = absent.get(s, 0) + 1
                if absent[s] >= cfg.delist_after:
                    pos = broker.state.positions[s]
                    value = broker.settle_delisted(ts, s, cfg.delist_recovery)
                    self.delistings.append(dict(date=day, symbol=s, qty=pos.qty, last_price=pos.last_price,
                                                value=value, cost=pos.qty * pos.avg_cost))
                    absent.pop(s)
                    last_adj.pop(s, None)
                continue
            absent.pop(s, None)
            if math.isfinite(rows["adj_factor"][j]):
                last_adj[s] = rows["adj_factor"][j]

        # 4. 风控 → 决策（预热不足时不调用策略）→ 下单
        target = None
        if i + 1 >= spec.warmup:
            view = p.view(i)
            universe = tuple(s for s, m in zip(p.symbols, seg.universe[i], strict=True) if m)
            target = strategy.on_bar(BarContext(view, broker.state, universe))
            if target is not None:
                validate_target_weights(target)
        st = broker.state
        target = risk_targets(self.monitor, ts, st.equity,
                              {s: q.market_value for s, q in st.positions.items()}, target)
        if target is not None:
            ids = count(self.next_id)
            self.pending = _orders(cfg, target, broker, p, i, rows, sym_idx, ts, ids)
            self.next_id = next(ids)

        st = broker.state
        self.records.append(dict(
            date=day, equity=st.equity, cash=st.cash, market_value=st.market_value,
            n_pos=len(st.positions), buy_value=buy_value, sell_value=sell_value,
            fees=st.fees_paid - n_fees_before, cost_slip_imp=cost_si,
        ))
        self.last_day = day
        self.n_days += 1


def _orders(cfg: EngineConfig, target, broker: BrokerSim, p, i: int, rows, sym_idx, ts,
            ids) -> list[_Pending]:
    st = broker.state
    equity = st.equity
    out: list[_Pending] = []
    lo = max(0, i + 1 - max(cfg.adv_window, cfg.sigma_window))
    amount_hist = p.block("amount", lo, i + 1)
    ret_hist = p.block("ret", lo, i + 1)
    for s in sorted(set(target) | set(st.positions)):
        j = sym_idx.get(s)
        if j is None:
            continue
        px = rows["close"][j]
        if not (math.isfinite(px) and px > 0 and rows["tradestatus"][j] == 1):
            continue  # 今天停牌：没有可用的参考价，不下单（明天重新决策）
        pos = st.positions.get(s)
        cur = pos.qty if pos else 0
        w = float(target.get(s, 0.0))
        delta = w * equity / px - cur
        if w == 0.0 and cur > 0:
            delta = -cur
        if abs(delta) * px < cfg.min_trade_value and not (w == 0.0 and cur > 0):
            continue
        qty = int(abs(delta))
        if qty <= 0:
            continue
        a = amount_hist[-cfg.adv_window:, j]
        r = ret_hist[-cfg.sigma_window:, j]
        adv = float(np.nanmean(a)) if np.isfinite(a).any() else math.nan
        sig = float(np.nanstd(r, ddof=1)) if np.isfinite(r).sum() > 1 else math.nan
        if not (adv > 0 and math.isfinite(sig)):
            continue  # 历史不足以估计冲击：不交易（新股已由 is_new 排除，这里是兜底）
        side = Side.BUY if delta > 0 else Side.SELL
        out.append(_Pending(Order(f"o{next(ids)}", s, side, qty, ts), adv, sig))
    return out
