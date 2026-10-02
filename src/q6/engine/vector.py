"""向量化引擎（P2-08）：输入目标权重矩阵，按事件引擎的同一套规则逐日执行，用于扫参。

和事件引擎（参照实现）的关系：
- 撮合规则、费用、成交记录用的是同一批函数（`matching.match_arrays` / `make_fill`），不是另写一份。
- 每日处理顺序逐步复刻 `event.py` 文件头写的 1–5 步；账户口径复刻 `broker_sim.py`
  （含除权零头折现、T+1、退市结算）。
- 不同之处只在"怎么算"：账户是按代码对齐的 numpy 数组而不是不可变快照；当天的卖单一次批量撮合；
  买单先按无限现金批量撮合，再按代码顺序逐单扣现金，碰到现金约束的那一单单独用真实现金重撮
  （和事件引擎逐单传 `cash_available` 的结果相同）。浮点求和顺序不同，权益只在最后几位有差异，
  一致性测试（P2-09）给出实测偏差。

为什么比事件引擎快：全区间回测里 2/3 的时间是加载快照（P2-08 剖析）。`run_many` 让多组配置共用一次数据加载，
扫参时加载成本按配置数摊薄；日内循环没有逐单的 Python 对象。架构方案原写 numba：实测瓶颈不在算术，
没有用 numba（见 PROGRESS P2-08 测速）。

策略输入：`weights_fn(seg) -> W`，W 形状 = (len(seg.panel), N)，与 `seg.panel.symbols` 对齐；
第 i 行 = 第 i 天收盘后的目标权重，**整行 NaN = 当天不调仓**（等同 on_bar 返回 None）；
非 NaN 行里 NaN 不允许。只读取 i+1 ≥ warmup 且 i ≥ seg.first 的行。
- `strategy_weights(strategy)`：把任何 Strategy 逐日用 PITView 调 on_bar 生成 W——逐日调用，天然 PIT，
  用于一致性测试。
  限制：传给策略的是固定的空账户（向量化引擎只支持决策不依赖账户状态的策略），on_fill 不会被调用。
- 自己写的向量化 weights_fn 拿到的是整段面板（含本段后面的行），**必须**通过 `check_weights_pit`
  截断测试才能用。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from q6.core.clock import BacktestClock
from q6.core.pit import Panel
from q6.core.types import AccountSnapshot, Fill, Side
from q6.engine.event import BacktestResult, EngineConfig, _ts, risk_targets
from q6.engine.feed import Segment, SnapshotFeed
from q6.engine.matching import Reason, make_fill, match_arrays
from q6.risk.monitor import RiskMonitor
from q6.strategy.base import BarContext, Strategy

WeightsFn = Callable[[Segment], np.ndarray]
_BIG_CASH = 1e18  # 买单第一遍批量撮合时的"无限现金"


@dataclass
class _Acct:
    """一组配置的账户，按代码保存（面板列每段都不同，跨段按代码重新对齐）。"""

    cfg: EngineConfig
    weights_fn: WeightsFn
    warmup: int
    cash: float
    realized: float = 0.0
    fees_paid: float = 0.0
    pos: dict[str, list] = field(default_factory=dict)  # 代码 → [qty, sellable, avg_cost, last_price]
    last_adj: dict[str, float] = field(default_factory=dict)
    absent: dict[str, int] = field(default_factory=dict)
    # 代码 → (side, qty, adv, sigma)
    pending: dict[str, tuple[int, int, float, float]] = field(default_factory=dict)
    records: list[dict] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    reasons: dict[str, int] = field(default_factory=dict)
    delistings: list[dict] = field(default_factory=list)
    monitor: RiskMonitor | None = None

    def bump(self, key: str, n: int = 1) -> None:
        self.reasons[key] = self.reasons.get(key, 0) + n


def strategy_weights(strategy: Strategy, initial_cash: float = 1_000_000.0) -> WeightsFn:
    """逐日调用 strategy.on_bar 生成 W（PIT：第 i 行只用 ≤i 的视图）。只对 i ≥ seg.first 且预热足够的行调用，
    调用次序与事件引擎相同，所以带计数器的策略（每 every 个 bar 调仓）行为一致。"""
    warmup = strategy.spec.warmup

    def fn(seg: Segment) -> np.ndarray:
        p = seg.panel
        n = len(p.symbols)
        w = np.full((len(p), n), np.nan)
        col = {s: j for j, s in enumerate(p.symbols)}
        for i in range(seg.first, len(p)):
            if i + 1 < warmup:
                continue
            view = p.view(i)
            acct = AccountSnapshot(ts=_ts(p.date_at(i)), cash=initial_cash)
            universe = tuple(s for s, m in zip(p.symbols, seg.universe[i], strict=True) if m)
            target = strategy.on_bar(BarContext(view, acct, universe))
            if target is None:
                continue
            row = np.zeros(n)
            for s, x in target.items():
                j = col.get(s)
                if j is not None:
                    row[j] = x
            w[i] = row
        return w

    return fn


def check_weights_pit(weights_fn: WeightsFn, seg: Segment, cuts: Sequence[int]) -> None:
    """截断测试：把面板截到第 c 行（含）再算 W，前 c+1 行必须和整段算出来的逐位相同。
    不同就说明用了未来数据。"""
    full = np.asarray(weights_fn(seg))
    p = seg.panel
    for c in cuts:
        if not seg.first <= c < len(p):
            raise ValueError(f"截断点 {c} 不在 [{seg.first}, {len(p)})")
        sub = Panel([p.date_at(t) for t in range(c + 1)], p.symbols,
                    {f: p.block(f, 0, c + 1) for f in p.field_names}, copy=False)
        part = np.asarray(weights_fn(Segment(sub, seg.first, seg.universe[: c + 1])))
        a, b = full[: c + 1], part
        same = (a == b) | (np.isnan(a) & np.isnan(b))
        if not same.all():
            i, j = np.argwhere(~same)[0]
            raise AssertionError(f"weights_fn 用了未来数据：截到第 {c} 行后第 {i} 行 {p.symbols[j]} "
                                 f"由 {a[i, j]} 变为 {b[i, j]}")


class VectorEngine:
    def __init__(self, cfg: EngineConfig | None = None) -> None:
        self.cfg = cfg or EngineConfig()

    def run(self, weights_fn: WeightsFn, warmup: int, feed: SnapshotFeed, start, end) -> BacktestResult:
        return self.run_many([(weights_fn, warmup, self.cfg)], feed, start, end)[0]

    def run_many(self, jobs: Sequence[tuple[WeightsFn, int, EngineConfig]], feed: SnapshotFeed,
                 start, end) -> list[BacktestResult]:
        """多组 (weights_fn, warmup, cfg) 共用一次快照加载。每段的面板带上所有配置的持仓与在途订单代码；
        策略只能在可投资范围内选股（策略卡硬性要求），所以多出来的列不改变任何策略的输入。"""
        if not jobs:
            return []
        accts = [_Acct(cfg, fn, warmup, cfg.initial_cash,
                       monitor=RiskMonitor(cfg.risk) if cfg.risk is not None else None)
                 for fn, warmup, cfg in jobs]
        max_warmup = max(a.warmup for a in accts)
        clock = BacktestClock()
        last_day: pd.Timestamp | None = None

        def keep() -> set[str]:
            out: set[str] = set()
            for a in accts:
                out |= set(a.pos) | set(a.pending)
            return out

        for seg in feed.segments(start, end, max_warmup, keep=keep):
            p = seg.panel
            rows = {f: [p.row(f, i) for i in range(len(p))] for f in ("adj_factor", "tradestatus", "close")}
            rows["sym_idx"] = {s: j for j, s in enumerate(p.symbols)}
            weights = []
            for a in accts:
                w = np.asarray(a.weights_fn(seg), dtype=np.float64)
                if w.shape != (len(p), len(p.symbols)):
                    raise ValueError(f"weights_fn 返回形状 {w.shape} ≠ {(len(p), len(p.symbols))}")
                weights.append(w)
            for i in range(seg.first, len(p)):
                day = pd.Timestamp(p.date_at(i))
                if last_day is not None and day <= last_day:
                    raise RuntimeError(f"交易日重复或倒序：{day.date()} 不晚于 {last_day.date()}")
                last_day = day
                ts = _ts(day)
                clock.advance(ts)
                bar = _BarRows(p, i)
                for a, w in zip(accts, weights, strict=True):
                    self._day(a, p, i, ts, day, bar, w, seg, rows)
            seg = p = rows = weights = bar = None  # noqa: F841  放掉本段引用再加载下一段

        out = []
        for a in accts:
            daily = pd.DataFrame(a.records).set_index("date")
            out.append(BacktestResult(daily, a.fills, a.reasons, a.cfg.initial_cash, a.delistings,
                                      a.monitor.events if a.monitor is not None else []))
        return out

    # ------------------------------------------------------------------ 单日
    def _day(self, a: _Acct, p: Panel, i: int, ts: datetime, day: pd.Timestamp, bar: _BarRows,
             w: np.ndarray, seg: Segment, rows: dict) -> None:
        cfg = a.cfg
        sym_idx = rows["sym_idx"]
        adj = rows["adj_factor"][i]
        status = rows["tradestatus"][i]
        close = rows["close"][i]

        # 1. 除权除息（比例 = 今日复权因子 / 上次见到的）→ T+1 解锁；口径同 BrokerSim.start_of_day
        for s, q in list(a.pos.items()):
            j = sym_idx.get(s)
            x = adj[j] if j is not None else math.nan
            r = 1.0
            if math.isfinite(x) and s in a.last_adj and x != a.last_adj[s]:
                r = x / a.last_adj[s]
            if r != 1.0:
                if not (r > 0 and math.isfinite(r)):
                    raise RuntimeError(f"{s} 除权比例非法 {r}")
                exact = q[0] * r
                nq = int(math.floor(exact + 1e-9))
                frac = exact - nq
                ref = q[3] / r
                avg = q[2] / r
                a.cash += frac * ref
                a.realized += frac * (ref - avg)
                if nq <= 0:
                    raise RuntimeError(f"{s} 除权后持仓变为 0（比例 {r}），数据异常")
                q[0], q[2], q[3] = nq, avg, ref
            q[1] = q[0]

        # 2. 撮合昨天收盘生成的订单：先卖后买，同方向按代码
        buy_value = sell_value = cost_si = 0.0
        fees_before = a.fees_paid
        if a.pending:
            orders = sorted(a.pending.items())
            a.pending = {}
            present = []
            for s, o in orders:
                if s not in sym_idx:  # 本段没有这只：等同停牌
                    a.bump(Reason.SUSPENDED.name)
                else:
                    present.append((s, o))
            sells = [(s, o) for s, o in present if o[0] < 0]
            buys = [(s, o) for s, o in present if o[0] > 0]
            if sells:
                res = self._match(a, bar, day, sells, [_BIG_CASH] * len(sells), sym_idx)
                for k, (s, _o) in enumerate(sells):
                    f = self._fill(a, res, k, s, Side.SELL, ts)
                    if f is not None:
                        sell_value += f.notional
                        cost_si += f.slippage_cost + f.impact_cost
            if buys:
                res = self._match(a, bar, day, buys, [_BIG_CASH] * len(buys), sym_idx)
                for k, (s, o) in enumerate(buys):
                    q, px = int(res.qty[k]), float(res.price[k])
                    if q > 0:
                        f = make_fill("v", s, Side.BUY, q, px, float(res.ref_price[k]),
                                      float(res.slippage_rate[k]), float(res.impact_rate[k]), ts, cfg.match)
                        # 现金可能不够：用真实现金单独重撮（同事件引擎）
                        if f.notional + f.fees > a.cash - 1.0:
                            res1 = self._match(a, bar, day, [(s, o)], [a.cash], sym_idx)
                            f = self._fill(a, res1, 0, s, Side.BUY, ts)
                        else:
                            self._apply(a, f)
                            if int(res.reason[k]) != Reason.FILLED:
                                a.bump(Reason(int(res.reason[k])).name)
                    else:
                        f = None
                        a.bump(Reason(int(res.reason[k])).name)
                    if f is not None:
                        buy_value += f.notional
                        cost_si += f.slippage_cost + f.impact_cost

        # 3. 盯市（停牌保留旧价）→ 缺行计数 / 退市结算 → 记下复权因子
        for s, q in a.pos.items():
            j = sym_idx.get(s)
            if j is not None and status[j] == 1 and math.isfinite(close[j]):
                q[3] = float(close[j])
        for s in list(a.pos):
            j = sym_idx.get(s)
            if j is None or not math.isfinite(status[j]):
                a.bump("MISSING_ROW")
                a.absent[s] = a.absent.get(s, 0) + 1
                if a.absent[s] >= cfg.delist_after:
                    q = a.pos.pop(s)
                    value = q[0] * q[3] * cfg.delist_recovery
                    a.cash += value
                    a.realized += value - q[0] * q[2]
                    a.delistings.append(dict(date=day, symbol=s, qty=q[0], last_price=q[3],
                                             value=value, cost=q[0] * q[2]))
                    a.absent.pop(s)
                    a.last_adj.pop(s, None)
                continue
            a.absent.pop(s, None)
            if math.isfinite(adj[j]):
                a.last_adj[s] = adj[j]

        # 4. 风控 → 决策：W 第 i 行 → 次日订单
        row = None
        if i + 1 >= a.warmup and not np.isnan(w[i]).all():
            row = w[i]
            if np.isnan(row).any() or (row < -1e-9).any() or row.sum() > 1 + 1e-9:
                raise ValueError(f"{day.date()} 目标权重非法（含 NaN / 负数 / 合计 >1）")
        if a.monitor is not None:
            syms = p.symbols
            target = None if row is None else {syms[j]: float(row[j]) for j in np.flatnonzero(row != 0)}
            equity = a.cash + sum(q[0] * q[3] for q in a.pos.values())
            target = risk_targets(a.monitor, ts, equity, {s: q[0] * q[3] for s, q in a.pos.items()}, target)
            if target is None:
                row = None
            else:
                row = np.zeros(len(syms))
                for s, x in target.items():
                    if s in sym_idx:
                        row[sym_idx[s]] = x
        if row is not None:
            self._orders(a, p, i, row, close, status, sym_idx)

        # 5. 记录
        mv = sum(q[0] * q[3] for q in a.pos.values())
        a.records.append(dict(
            date=day, equity=a.cash + mv, cash=a.cash, market_value=mv, n_pos=len(a.pos),
            buy_value=buy_value, sell_value=sell_value, fees=a.fees_paid - fees_before, cost_slip_imp=cost_si,
        ))

    # ------------------------------------------------------------------ 撮合 / 记账
    def _match(self, a: _Acct, bar: _BarRows, day, orders, cash, sym_idx):
        js = [sym_idx[s] for s, _ in orders]
        return match_arrays(
            day=day.date(), codes=[s for s, _ in orders], side=[o[0] for _, o in orders],
            qty=[o[1] for _, o in orders],
            sellable=[a.pos[s][1] if s in a.pos else 0 for s, _ in orders],
            cash_available=cash, limit_price=[math.nan] * len(orders),
            **bar.take(js), adv=[o[2] for _, o in orders], sigma=[o[3] for _, o in orders], cfg=a.cfg.match,
        )

    def _fill(self, a: _Acct, res, k: int, s: str, side: Side, ts: datetime) -> Fill | None:
        why = int(res.reason[k])
        if why != Reason.FILLED:
            a.bump(Reason(why).name)
        q = int(res.qty[k])
        if q == 0:
            return None
        f = make_fill("v", s, side, q, float(res.price[k]), float(res.ref_price[k]),
                      float(res.slippage_rate[k]), float(res.impact_rate[k]), ts, a.cfg.match)
        self._apply(a, f)
        return f

    @staticmethod
    def _apply(a: _Acct, f: Fill) -> None:
        """口径同 BrokerSim.apply_fill。"""
        a.cash += f.cash_delta
        q = a.pos.get(f.symbol)
        if f.side is Side.BUY:
            if a.cash < -1e-6:
                raise RuntimeError(f"现金不足：买入 {f.symbol} {f.qty} 股后现金 {a.cash:.2f}")
            old_qty = q[0] if q else 0
            old_cost = old_qty * q[2] if q else 0.0
            new_qty = old_qty + f.qty
            avg = (old_cost + f.notional + f.fees) / new_qty
            a.pos[f.symbol] = [new_qty, q[1] if q else 0, avg, q[3] if q else f.price]
        else:
            if q is None or f.qty > q[1]:
                raise RuntimeError(f"卖出 {f.symbol} {f.qty} 股超过可卖")
            a.realized += (f.price - q[2]) * f.qty - f.fees
            left = q[0] - f.qty
            if left:
                q[0], q[1] = left, q[1] - f.qty
            else:
                del a.pos[f.symbol]
        a.fees_paid += f.fees
        a.fills.append(f)

    def _orders(self, a: _Acct, p: Panel, i: int, row: np.ndarray, close: np.ndarray,
                status: np.ndarray, sym_idx: dict[str, int]) -> None:
        """口径同 event._orders：目标市值 = 权重 × 当日收盘权益，差额 / 收盘价 = 股数。"""
        cfg = a.cfg
        equity = a.cash + sum(q[0] * q[3] for q in a.pos.values())
        cand = set(np.flatnonzero(row != 0).tolist()) | {sym_idx[s] for s in a.pos if s in sym_idx}
        if not cand:
            return
        lo = max(0, i + 1 - max(cfg.adv_window, cfg.sigma_window))
        js = np.array(sorted(cand))  # 面板列按代码排序，下标顺序 = 代码顺序
        amount_hist = p.block("amount", lo, i + 1)[:, js]
        ret_hist = p.block("ret", lo, i + 1)[:, js]
        syms = p.symbols
        for k, j in enumerate(js):
            px = close[j]
            if not (math.isfinite(px) and px > 0 and status[j] == 1):
                continue
            s = syms[j]
            q = a.pos.get(s)
            cur = q[0] if q else 0
            wj = float(row[j])
            delta = wj * equity / px - cur
            if wj == 0.0 and cur > 0:
                delta = -cur
            if abs(delta) * px < cfg.min_trade_value and not (wj == 0.0 and cur > 0):
                continue
            qty = int(abs(delta))
            if qty <= 0:
                continue
            am = amount_hist[-cfg.adv_window:, k]
            r = ret_hist[-cfg.sigma_window:, k]
            adv = float(np.nanmean(am)) if np.isfinite(am).any() else math.nan
            sig = float(np.nanstd(r, ddof=1)) if np.isfinite(r).sum() > 1 else math.nan
            if not (adv > 0 and math.isfinite(sig)):
                continue
            a.pending[s] = (1 if delta > 0 else -1, qty, adv, sig)


class _BarRows:
    """第 i 根 bar 的撮合输入，按列下标取。不可交易的列给占位值（同 event._bar）。"""

    _F = ("open", "high", "low", "close", "preclose", "volume", "amount",
          "tradestatus", "is_st", "is_new", "bad")

    def __init__(self, p: Panel, i: int) -> None:
        self.r = {f: p.row(f, i) for f in self._F}

    def take(self, js: list[int]) -> dict:
        r = {f: self.r[f][js] for f in self._F}
        trad = (r["tradestatus"] == 1) & (r["bad"] != 1) & np.isfinite(r["close"])
        one = np.ones(len(js))
        pick = lambda f: np.where(trad, r[f], one)  # noqa: E731
        return dict(
            open_=pick("open"), high=pick("high"), low=pick("low"), close=pick("close"),
            volume=np.where(trad, np.floor(np.nan_to_num(r["volume"])), 0.0),
            amount=np.where(trad, r["amount"], 0.0), preclose=pick("preclose"),
            tradable=trad, is_st=trad & (r["is_st"] == 1), no_limit_day=r["is_new"] == 1,
        )
