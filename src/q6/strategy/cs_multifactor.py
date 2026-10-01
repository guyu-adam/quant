"""截面多因子等权策略。

没有行业数据，因此不做行业中性化；size 用 amount/turn 近似流通市值
（P2-13 已知局限）；当前因子集合没有价值或质量因子。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from q6.alpha import ops
from q6.alpha.library import FACTORS
from q6.strategy.base import BarContext, StrategyBase, StrategySpec


class CSMultiFactor(StrategyBase):
    name = "cs_multifactor"

    def __init__(
        self,
        factors: str = "mom_12_1,rev_20,lowvol_60,low_turn_20,amihud_20,size",
        n: int = 50,
        every: int = 20,
        gross: float = 0.95,
        history: int = 260,
    ) -> None:
        names = tuple(part.strip() for part in factors.split(",") if part.strip())
        if not names or any(name not in FACTORS for name in names):
            raise ValueError("factors must be a comma-separated list of names in FACTORS")
        if len(set(names)) != len(names):
            raise ValueError("factors must not contain duplicates")
        if n <= 0 or every <= 0 or history <= 0 or not 0 < gross <= 0.95:
            raise ValueError("n/every/history must be positive and gross must be in (0, 0.95]")
        self.factors, self.n, self.every = names, n, every
        self.gross, self.history = float(gross), history
        self._calls = 0

    @property
    def spec(self) -> StrategySpec:
        return StrategySpec(
            fields=("close_hfq", "ret", "turn", "amount", "tradestatus", "is_st", "is_new"),
            warmup=self.history,
            params={"factors": ",".join(self.factors), "n": self.n, "every": self.every,
                    "gross": self.gross, "history": self.history},
        )

    def on_bar(self, ctx: BarContext) -> dict[str, float] | None:
        self._calls += 1
        if (self._calls - 1) % self.every:
            return None

        view = ctx.view
        symbols = view.symbols
        dates = view.dates(self.history)
        close = view.window("close_hfq", self.history)
        ret = view.window("ret", self.history)
        turn = view.window("turn", self.history)
        amount = view.window("amount", self.history)
        raw = {"close": close, "ret": ret, "turn": turn, "amount": amount}
        # Keep this field construction aligned with q6.alpha.inputs.build_inputs.
        with np.errstate(divide="ignore", invalid="ignore"):
            raw["float_cap"] = amount / np.where(turn > 0, turn / 100.0, np.nan)
        frames = {key: pd.DataFrame(value, index=dates, columns=symbols) for key, value in raw.items()}

        eligible = (
            ctx.universe_mask()
            & (view.latest("tradestatus") == 1)
            & (view.latest("is_st") != 1)
            & (view.latest("is_new") != 1)
        )
        # Keep these factor keys aligned with q6.alpha.inputs.build_inputs.
        factor_data = {
            "close": frames["close"], "ret": frames["ret"], "turn": frames["turn"],
            "amount": frames["amount"], "float_cap": frames["float_cap"],
        }
        z_by_factor = []
        for name in self.factors:
            factor_values = FACTORS[name](factor_data).iloc[-1].to_numpy(dtype=float, copy=True)
            factor_values[~eligible] = np.nan
            row = pd.DataFrame([factor_values], index=[dates[-1]], columns=symbols)
            z_by_factor.append(ops.cs_zscore(row).iloc[-1].to_numpy(dtype=float))
        # Each factor z-score is standardized only across the eligible cross-section.
        # NaN factors are omitted per stock; fewer than half means exclusion.
        zarr = np.asarray(z_by_factor)
        available = np.isfinite(zarr)
        count = available.sum(axis=0)
        combined = np.divide(
            np.nansum(zarr, axis=0), count, out=np.full(len(symbols), np.nan), where=count > 0
        )
        combined[(count < math.ceil(len(self.factors) / 2)) | ~eligible] = np.nan
        candidates = np.flatnonzero(np.isfinite(combined))
        if not len(candidates):
            return {}
        order = candidates[np.argsort(-combined[candidates], kind="stable")[: self.n]]
        weight = self.gross / len(order)
        return {symbols[j]: weight for j in order}
