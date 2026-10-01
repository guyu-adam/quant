"""Daily mark-to-market loss limits and drawdown breaker.

The breaker resets its peak to the equity on the release mark. This prevents the
same historical drawdown from immediately re-triggering the breaker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from q6.core.types import validate_target_weights


@dataclass(frozen=True)
class RiskConfig:
    daily_loss_limit: float = -0.03
    max_drawdown: float = -0.20
    cooldown_days: int = 20
    breaker_scale: float = 0.0


@dataclass(frozen=True)
class RiskState:
    daily_loss_hit: bool
    breaker_on: bool
    cooldown_left: int
    peak: float
    drawdown: float
    day_return: float


class RiskMonitor:
    def __init__(self, cfg: RiskConfig = RiskConfig()) -> None:  # noqa: B008 - API default is specified by task
        if (
            cfg.cooldown_days < 1
            or not math.isfinite(cfg.daily_loss_limit)
            or not math.isfinite(cfg.max_drawdown)
        ):
            raise ValueError("风险配置非法")
        if not math.isfinite(cfg.breaker_scale) or cfg.breaker_scale < 0:
            raise ValueError("breaker_scale 必须是有限非负数")
        self.cfg = cfg
        self._last_ts: datetime | None = None
        self._last_equity: float | None = None
        self._peak = 0.0
        self._breaker_on = False
        self._cooldown_left = 0
        self._daily_loss_hit = False
        self._events: list[dict] = []
        self._state = RiskState(False, False, 0, 0.0, 0.0, 0.0)

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def on_mark(self, ts: datetime, equity: float) -> RiskState:
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError("equity 必须是正的有限数")
        if self._last_ts is not None and ts <= self._last_ts:
            raise ValueError("ts 必须严格递增")
        if self._last_equity is None:
            self._last_ts, self._last_equity, self._peak = ts, equity, equity
            self._state = RiskState(False, False, 0, equity, 0.0, 0.0)
            return self._state

        day_return = equity / self._last_equity - 1
        daily_hit = day_return <= self.cfg.daily_loss_limit
        if daily_hit:
            self._events.append({"date": ts, "kind": "DAILY_LOSS", "equity": equity, "value": day_return})

        if self._breaker_on:
            self._cooldown_left -= 1
            if self._cooldown_left == 0:
                self._breaker_on = False
                self._peak = equity
                self._events.append({"date": ts, "kind": "BREAKER_OFF", "equity": equity, "value": 0.0})
        else:
            self._peak = max(self._peak, equity)
            drawdown = equity / self._peak - 1
            if equity <= self._peak * (1 + self.cfg.max_drawdown):
                self._breaker_on = True
                self._cooldown_left = self.cfg.cooldown_days
                self._events.append({"date": ts, "kind": "BREAKER_ON", "equity": equity, "value": drawdown})

        drawdown = equity / self._peak - 1
        self._last_ts, self._last_equity = ts, equity
        self._daily_loss_hit = daily_hit
        self._state = RiskState(
            daily_hit, self._breaker_on, self._cooldown_left, self._peak, drawdown, day_return
        )
        return self._state

    def filter_target(self, target: dict[str, float], current: dict[str, float]) -> dict[str, float]:
        if self._breaker_on:
            result = {
                s: w * self.cfg.breaker_scale
                for s, w in current.items()
                if w * self.cfg.breaker_scale > 0
            }
        elif self._daily_loss_hit:
            result = {
                s: min(target.get(s, 0.0), current.get(s, 0.0))
                for s in set(target) | set(current)
                if min(target.get(s, 0.0), current.get(s, 0.0)) > 0
            }
        else:
            result = dict(target)
        validate_target_weights(result)
        return result
