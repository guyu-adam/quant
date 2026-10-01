"""Event-driven benchmarks for the PIT universe and an initial buy-and-hold basket."""

from __future__ import annotations

import numpy as np

from q6.strategy.base import BarContext, StrategyBase, StrategySpec


class UniverseEqualWeight(StrategyBase):
    """中证 800 宇宙等权月度再平衡、扣全部成本的基准。

    选股范围剔除了 ST / 新股 / 停牌，和中证 800 指数本身的口径不同。
    """

    name = "universe_ew"

    def __init__(self, gross: float = 0.95) -> None:
        self.gross = gross
        self._last_month: tuple[int, int] | None = None

    @property
    def spec(self) -> StrategySpec:
        return StrategySpec(
            fields=("close", "tradestatus", "is_st", "is_new"),
            warmup=1,
            params={"gross": self.gross},
        )

    def on_bar(self, ctx: BarContext) -> dict[str, float] | None:
        month = (ctx.now.year, ctx.now.month)
        if month == self._last_month:
            return None
        self._last_month = month

        view = ctx.view
        close = view.latest("close")
        status = view.latest("tradestatus")
        st = view.latest("is_st")
        new = view.latest("is_new")
        eligible = (
            ctx.universe_mask()
            & np.isfinite(close)
            & (status == 1)
            & (st != 1)
            & (new != 1)
        )
        selected = np.flatnonzero(eligible)
        if not len(selected):
            return {}
        weight = self.gross / len(selected)
        return {view.symbols[j]: weight for j in selected}


class BuyAndHold(StrategyBase):
    """初始一篮子持有到底；成分变化不跟随，退市由引擎结算。"""

    name = "buy_and_hold"

    def __init__(self, gross: float = 0.95) -> None:
        self.gross = gross
        self._initialized = False

    @property
    def spec(self) -> StrategySpec:
        return StrategySpec(
            fields=("close", "tradestatus", "is_st", "is_new"),
            warmup=1,
            params={"gross": self.gross},
        )

    def on_bar(self, ctx: BarContext) -> dict[str, float] | None:
        if self._initialized:
            return None

        view = ctx.view
        close = view.latest("close")
        status = view.latest("tradestatus")
        st = view.latest("is_st")
        new = view.latest("is_new")
        eligible = (
            ctx.universe_mask()
            & np.isfinite(close)
            & (status == 1)
            & (st != 1)
            & (new != 1)
        )
        selected = np.flatnonzero(eligible)
        if not len(selected):
            return {}
        self._initialized = True
        weight = self.gross / len(selected)
        return {view.symbols[j]: weight for j in selected}
