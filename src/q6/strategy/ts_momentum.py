"""个股层面时序动量策略，使用反波动率权重和组合波动率目标。

本实现不包含架构方案中的指数 ETF 层面版本，因为当前快照没有 ETF 数据。
"""

from __future__ import annotations

import numpy as np

from q6.strategy.base import BarContext, StrategyBase, StrategySpec


class TSMomentum(StrategyBase):
    """每隔 ``every`` 根可调用 bar 调仓，持有正时序动量股票。"""

    name = "ts_momentum"

    def __init__(
        self,
        lookback: int = 252,
        skip: int = 21,
        vol_window: int = 60,
        every: int = 20,
        cap: float = 0.05,
        gross: float = 0.95,
        vol_target: float = 0.15,
    ) -> None:
        if lookback <= skip or skip < 0 or vol_window < 2 or every < 1:
            raise ValueError("lookback > skip >= 0, vol_window >= 2, and every >= 1 are required")
        if not 0 < cap <= gross <= 1 or vol_target <= 0:
            raise ValueError("require 0 < cap <= gross <= 1 and vol_target > 0")
        self.lookback, self.skip, self.vol_window = lookback, skip, vol_window
        self.every, self.cap, self.gross, self.vol_target = every, cap, gross, vol_target
        self._bars = 0

    @property
    def spec(self) -> StrategySpec:
        return StrategySpec(
            fields=("close_hfq", "tradestatus", "is_st", "is_new"),
            warmup=max(self.lookback, self.vol_window) + 1,
            params={
                "lookback": self.lookback, "skip": self.skip, "vol_window": self.vol_window,
                "every": self.every, "cap": self.cap, "gross": self.gross,
                "vol_target": self.vol_target,
            },
        )

    def on_bar(self, ctx: BarContext) -> dict[str, float] | None:
        self._bars += 1
        if (self._bars - 1) % self.every:
            return None

        view = ctx.view
        px = view.window("close_hfq", self.lookback + 1)
        vol_px = view.window("close_hfq", self.vol_window + 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            momentum = px[-self.skip - 1] / px[0] - 1.0
            returns = vol_px[1:] / vol_px[:-1] - 1.0
        latest_trade = view.latest("tradestatus")
        latest_st = view.latest("is_st")
        latest_new = view.latest("is_new")
        valid = (
            ctx.universe_mask()
            & (latest_trade == 1)
            & (latest_st != 1)
            & (latest_new != 1)
            & np.isfinite(latest_trade)
            & np.isfinite(latest_st)
            & np.isfinite(latest_new)
            & np.isfinite(px).all(axis=0)
            & np.isfinite(returns).all(axis=0)
            & np.isfinite(momentum)
            & (momentum > 0)
        )
        sigma = np.std(returns, axis=0, ddof=1)  # lookahead: ok; returns are the trailing PITView window
        selected = valid & np.isfinite(sigma) & (sigma > 0)
        ids = np.flatnonzero(selected)
        if not len(ids):
            return {}

        inv = 1.0 / sigma[ids]
        weights = inv / inv.sum() * self.gross
        weights = np.minimum(weights, self.cap)
        portfolio_returns = returns[:, ids].mean(axis=1)
        portfolio_vol = np.std(portfolio_returns, ddof=1) * np.sqrt(252.0)  # lookahead: ok; PIT window
        scale = (
            min(1.0, self.vol_target / portfolio_vol)
            if np.isfinite(portfolio_vol) and portfolio_vol > 0
            else 1.0
        )
        weights *= scale
        return {view.symbols[j]: float(w) for j, w in zip(ids, weights, strict=True) if w > 0}
