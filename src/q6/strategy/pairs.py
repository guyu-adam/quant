"""Distance based pairs with strictly separated formation and trading phases.

Based on the distance approach of Gatev et al. (2006): few frozen formation
parameters make the method interpretable and avoid multiple-testing correction.
No industry data is available, so pairs are not industry matched. A shares
cannot be shorted here; only the apparently undervalued leg is bought, so this
implementation is not market neutral.
"""

from __future__ import annotations

from itertools import combinations

import numpy as np

from q6.strategy.base import BarContext, StrategyBase, StrategySpec


class PairsDistance(StrategyBase):
    name = "pairs_distance"

    def __init__(self, formation: int = 250, trading: int = 125, n_pairs: int = 10,
                 pool: int = 100, entry: float = 2.0, exit: float = 0.5,
                 stop: float = 4.0, gross: float = 0.95) -> None:
        self.formation, self.trading, self.n_pairs, self.pool = formation, trading, n_pairs, pool
        self.entry, self.exit, self.stop, self.gross = entry, exit, stop, gross
        self._calls = 0
        self._pairs: list[tuple[str, str, float, float, float, float]] = []
        self._open: dict[tuple[str, str], str] = {}
        self._stopped: set[tuple[str, str]] = set()

    @property
    def spec(self) -> StrategySpec:
        return StrategySpec(
            fields=("close_hfq", "amount", "tradestatus", "is_st", "is_new"),
            warmup=max(self.formation, 60),
            params=dict(formation=self.formation, trading=self.trading, n_pairs=self.n_pairs,
                        pool=self.pool, entry=self.entry, exit=self.exit, stop=self.stop, gross=self.gross),
        )

    def _form(self, ctx: BarContext) -> None:
        v = ctx.view
        mask = ctx.universe_mask()
        status, st, new = v.latest("tradestatus"), v.latest("is_st"), v.latest("is_new")
        amount = v.window("amount", 60)
        liquidity = np.mean(amount, axis=0)  # lookahead: ok 仅使用截至形成日的 60 日窗口
        px = v.window("close_hfq", self.formation)
        valid = (mask & (status == 1) & (st != 1) & (new != 1)
                 & np.isfinite(amount).all(axis=0) & np.isfinite(liquidity)
                 & np.isfinite(px).all(axis=0) & (px[0] > 0))
        candidates = np.flatnonzero(valid)
        candidates = candidates[np.argsort(-liquidity[candidates], kind="stable")[:self.pool]]
        normalized = px[:, candidates] / px[0:1, candidates] if len(candidates) else np.empty((len(px), 0))
        ranked = []
        for a, b in combinations(range(len(candidates)), 2):
            spread = normalized[:, a] - normalized[:, b]
            ssd = float(np.dot(spread, spread))
            ranked.append((ssd, a, b, float(np.mean(spread)),  # lookahead: ok 形成窗口截至当日
                           float(np.std(spread, ddof=1))))  # lookahead: ok 形成窗口截至当日
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        used: set[int] = set()
        pairs = []
        for _, a, b, mu, sigma in ranked:
            if a in used or b in used or not np.isfinite(sigma) or sigma <= 0:
                continue
            pairs.append((v.symbols[int(candidates[a])], v.symbols[int(candidates[b])], mu, sigma,
                          float(px[0, candidates[a]]), float(px[0, candidates[b]])))
            used.update((a, b))
            if len(pairs) == self.n_pairs:
                break
        self._pairs = pairs
        self._open.clear()
        self._stopped.clear()

    def on_bar(self, ctx: BarContext):
        self._calls += 1
        # First eligible call forms the first cohort; subsequent formations are trading bars apart.
        if (self._calls - 1) % self.trading == 0:
            self._form(ctx)
        v = ctx.view
        px = v.latest("close_hfq")
        symbols = v.symbols
        symbol_index = {symbol: j for j, symbol in enumerate(symbols)}
        eligible = (ctx.universe_mask() & (v.latest("tradestatus") == 1)
                    & (v.latest("is_st") != 1) & (v.latest("is_new") != 1))
        active: list[tuple[str, str]] = []
        for a, b, mu, sigma, base_a, base_b in self._pairs:
            key = (a, b)
            if a not in symbol_index or b not in symbol_index:
                if key in self._open:
                    active.append(key)
                continue
            ia, ib = symbol_index[a], symbol_index[b]
            if not (eligible[ia] and eligible[ib] and np.isfinite(px[ia]) and np.isfinite(px[ib])):
                if key in self._open:
                    active.append(key)
                continue
            z = ((px[ia] / base_a - px[ib] / base_b) - mu) / sigma
            az = abs(z)
            if key in self._open:
                if az < self.exit:
                    del self._open[key]
                elif az > self.stop:
                    del self._open[key]
                    self._stopped.add(key)
                else:
                    active.append(key)
            elif key not in self._stopped and az > self.entry:
                self._open[key] = b if z > 0 else a
                active.append(key)
        if not active:
            return {}
        weight = self.gross / self.n_pairs
        # Duplicate legs can occur across pairs; target weights aggregate safely.
        result: dict[str, float] = {}
        for key in active:
            leg = self._open.get(key)
            if leg is not None:
                result[leg] = result.get(leg, 0.0) + weight
        return result
