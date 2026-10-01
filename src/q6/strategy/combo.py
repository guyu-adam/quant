"""Compose strategies with normalized weights.

Combo accepts strategy objects as a documented exception to the scalar-only
strategy constructor rule. Members with shorter warmup begin receiving calls
when the longest-warmup member becomes active, so their internal cadence starts
later than it would when run alone.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from q6.core.types import Fill
from q6.strategy.base import BarContext, Strategy, StrategyBase, StrategySpec


class Combo(StrategyBase):
    """Blend enabled strategy targets; member objects are accepted by design."""

    name = "combo"

    def __init__(
        self,
        members: Sequence[tuple[Strategy, float]],
        enabled: Sequence[bool] | None = None,
        gross: float = 0.95,
    ) -> None:
        if not math.isfinite(gross) or gross < 0:
            raise ValueError("gross must be finite and non-negative")
        if enabled is not None and len(enabled) != len(members):
            raise ValueError("enabled length must match members")
        flags = tuple(enabled) if enabled is not None else (True,) * len(members)
        active: list[tuple[Strategy, float]] = []
        for (strategy, weight), is_enabled in zip(members, flags, strict=True):
            if not math.isfinite(weight) or weight < 0:
                raise ValueError("member weights must be finite and non-negative")
            if is_enabled:
                active.append((strategy, weight))
        total = sum(weight for _, weight in active)
        if not active or total <= 0:
            raise ValueError("enabled members must have at least one positive weight")
        self._members = tuple((strategy, weight / total) for strategy, weight in active)
        self.gross = gross
        self._last: list[dict[str, float]] = [{} for _ in self._members]
        fields: list[str] = []
        for strategy, _ in self._members:
            for field in strategy.spec.fields:
                if field not in fields:
                    fields.append(field)
        self._spec = StrategySpec(
            fields=tuple(fields),
            warmup=max(strategy.spec.warmup for strategy, _ in self._members),
            params={
                "members": ",".join(f"{s.name}:{w}" for s, w in self._members),
                "gross": gross,
            },
        )

    @property
    def spec(self) -> StrategySpec:
        return self._spec

    def on_bar(self, ctx: BarContext) -> dict[str, float] | None:
        changed = False
        for i, (strategy, _) in enumerate(self._members):
            target = strategy.on_bar(ctx)
            if target is not None:
                self._last[i] = dict(target)
                changed = True
        if not changed:
            return None
        combined: dict[str, float] = {}
        for (_, weight), target in zip(self._members, self._last, strict=True):
            for symbol, value in target.items():
                combined[symbol] = combined.get(symbol, 0.0) + weight * value
        gross = sum(combined.values())
        if gross > self.gross:
            scale = self.gross / gross
            combined = {symbol: value * scale for symbol, value in combined.items()}
        return combined

    def on_fill(self, fill: Fill) -> None:
        for strategy, _ in self._members:
            strategy.on_fill(fill)
