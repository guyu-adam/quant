"""短期反转：以近期弱势、流动性足够且非跌停的股票做多。"""

from __future__ import annotations

import numpy as np

from q6.market.rules_cn import limit_pct_array, limit_prices
from q6.strategy.base import BarContext, StrategyBase, StrategySpec


class ShortReversal(StrategyBase):
    name = "short_reversal"

    def __init__(self, lookback: int = 5, n: int = 30, every: int = 5,
                 liq_window: int = 20, gross: float = 0.95) -> None:
        self.lookback, self.n, self.every = lookback, n, every
        self.liq_window, self.gross = liq_window, gross
        self._bars = 0

    @property
    def spec(self) -> StrategySpec:
        return StrategySpec(
            fields=("close_hfq", "amount", "close", "preclose", "tradestatus", "is_st", "is_new"),
            warmup=max(self.lookback + 1, self.liq_window),
            params=dict(lookback=self.lookback, n=self.n, every=self.every,
                        liq_window=self.liq_window, gross=self.gross),
        )

    def on_bar(self, ctx: BarContext):
        self._bars += 1
        if (self._bars - 1) % self.every:
            return None
        v = ctx.view
        symbols = v.symbols
        mask = ctx.universe_mask()
        status, st, new = v.latest("tradestatus"), v.latest("is_st"), v.latest("is_new")
        amount = v.window("amount", self.liq_window)
        close = v.latest("close")
        preclose = v.latest("preclose")
        pct = limit_pct_array(symbols, st == 1, [v.now] * len(symbols))
        down = np.full(len(symbols), np.nan)
        valid_preclose = np.isfinite(preclose) & (preclose > 0)
        if valid_preclose.any():
            _, down[valid_preclose] = limit_prices(np.round(preclose[valid_preclose], 2), pct[valid_preclose])
        px = v.window("close_hfq", self.lookback + 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            returns = px[-1] / px[0] - 1
        valid_amount = np.isfinite(amount).all(axis=0)
        liq = np.mean(amount, axis=0)  # lookahead: ok 仅使用截至 t 的成交额窗口
        scope = mask & (status == 1) & (st != 1) & (new != 1) & valid_amount
        if not scope.any():
            return {}
        median = float(np.median(liq[scope]))  # lookahead: ok 截面中位数仅由 ≤t 数据计算
        eligible = (scope & np.isfinite(returns) & np.isfinite(close) & np.isfinite(down)
                    & (close > down))
        eligible &= liq >= median
        picks = np.flatnonzero(eligible)
        picks = picks[np.argsort(returns[picks], kind="stable")[:self.n]]
        if not len(picks):
            return {}
        weight = self.gross / len(picks)
        return {symbols[j]: weight for j in picks}
