"""Pre-trade target weight cleanup.

Cash availability, sellability, price limits, suspensions, and lot sizes are
handled by the matching layer (``engine/matching.py``), not here.
"""

from __future__ import annotations

import math


def check_target(
    target: dict[str, float], *, cap: float = 0.05, gross: float = 0.95
) -> tuple[dict[str, float], list[str]]:
    if not (math.isfinite(cap) and cap >= 0 and math.isfinite(gross) and gross >= 0):
        raise ValueError("cap/gross 必须是有限非负数")
    result: dict[str, float] = {}
    violations: list[str] = []
    for symbol, raw in target.items():
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            violations.append(f"DROP {symbol} {value}")
            continue
        if value > cap:
            violations.append(f"CAP {symbol} {value:g}->{cap:g}")
            value = cap
        if value > 0:
            result[symbol] = value
    total = sum(result.values())
    if total > gross:
        scale = gross / total
        violations.append(f"GROSS {total:g}->{gross:g}")
        result = {symbol: value * scale for symbol, value in result.items()}
    return result, violations
