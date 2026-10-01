"""策略接口（P2-14，冻结）。

新策略只需实现三个方法，不用改引擎：
- `spec`：声明需要的字段（快照 daily 的列名，例如 "close_hfq"）和预热期（需要多少根历史 bar）。
- `on_bar(ctx)`：t 日收盘后被调用，只能通过 `ctx.view`（PITView，只看得到 ≤t 的数据）读数据，
  返回目标权重（占总权益比例，≥0，合计 ≤1）；返回 None = 保持当前持仓不调仓。t+1 才会成交。
- `on_fill(fill)`：可选，成交回报。

策略拿不到 Panel、拿不到下一根 bar、拿不到撮合器。下单、手数、涨跌停、风控都由框架处理。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from q6.core.pit import PITView
from q6.core.types import AccountSnapshot, Fill, TargetWeights


@dataclass(frozen=True, slots=True)
class StrategySpec:
    fields: tuple[str, ...]  # 需要的 daily 列（引擎会额外带上撮合需要的列，策略也可以读它们）
    warmup: int  # 预热 bar 数：view 里至少有这么多行历史才会调用 on_bar
    params: Mapping[str, object] = field(default_factory=dict)  # 写进登记簿，供复现 / 计数


@dataclass(frozen=True, slots=True)
class BarContext:
    view: PITView  # 唯一的数据入口
    account: AccountSnapshot  # t 日收盘盯市后的账户（只读快照）
    universe: tuple[str, ...]  # t 日可投资范围（月度中证 800 成分，按 ≤t 的最近月末）

    @property
    def now(self) -> pd.Timestamp:
        return self.view.now

    def universe_mask(self) -> np.ndarray:
        """与 view.symbols 对齐的布尔掩码。"""
        u = set(self.universe)
        return np.array([s in u for s in self.view.symbols], dtype=bool)


@runtime_checkable
class Strategy(Protocol):
    name: str

    @property
    def spec(self) -> StrategySpec: ...

    def on_bar(self, ctx: BarContext) -> TargetWeights | None: ...

    def on_fill(self, fill: Fill) -> None: ...


class StrategyBase:
    """可选基类：给出空的 on_fill。"""

    name = "base"

    def on_fill(self, fill: Fill) -> None:  # noqa: B027 - 有意留空的默认实现
        return None
