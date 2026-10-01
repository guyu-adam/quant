"""样板策略：用来跑通引擎、做引擎一致性测试。不是研究结论，不进最终报告的策略池。"""

from __future__ import annotations

import numpy as np

from q6.strategy.base import BarContext, StrategyBase, StrategySpec


class LowVolEqualWeight(StrategyBase):
    """每 `every` 个交易日调仓：在可投资范围内、当日可交易的股票里，
    取近 `lookback` 日收益波动最低的 `n` 只等权。"""

    name = "example_lowvol"

    def __init__(self, n: int = 30, lookback: int = 60, every: int = 20, gross: float = 0.95) -> None:
        self.n, self.lookback, self.every, self.gross = n, lookback, every, gross
        self._k = 0

    @property
    def spec(self) -> StrategySpec:
        return StrategySpec(fields=("close_hfq",), warmup=self.lookback + 1,
                            params=dict(n=self.n, lookback=self.lookback, every=self.every, gross=self.gross))

    def on_bar(self, ctx: BarContext):
        self._k += 1
        if (self._k - 1) % self.every:
            return None
        v = ctx.view
        px = v.window("close_hfq", self.lookback + 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            r = px[1:] / px[:-1] - 1
        ok = ctx.universe_mask() & np.isfinite(r).all(axis=0) & (v.latest("tradestatus") == 1)
        vol = np.where(ok, np.std(r, axis=0, ddof=1), np.inf)  # lookahead: ok 窗口内（≤t）逐列标准差
        pick = np.argsort(vol, kind="stable")[: self.n]
        pick = pick[np.isfinite(vol[pick])]
        if len(pick) == 0:
            return {}
        w = self.gross / len(pick)
        return {v.symbols[j]: w for j in pick}
