"""Project long-only target weights onto portfolio constraints.

The algorithm filters non-positive/non-finite targets, scales down (never up) to
gross, then alternates single-name and optional industry clip-redistribute steps.
Redistribution follows original target proportions among names with remaining
capacity. Infeasible capacity is left as cash. Finally, a turnover limit blends
the result toward current weights; if current holdings already breach caps, that
blend may still breach them, and we intentionally do not project again because
doing so could exceed the turnover limit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from q6.core.types import validate_target_weights


@dataclass(frozen=True)
class Constraints:
    cap: float = 0.05
    gross: float = 0.95
    industry_cap: float = 0.20
    turnover_cap: float | None = None


@dataclass(frozen=True)
class ProjectResult:
    weights: dict[str, float]
    notes: tuple[str, ...]


_TOL = 1e-12


def _capacity(names: list[str], c: Constraints, industry: dict[str, str] | None) -> float:
    if industry is None:
        return len(names) * c.cap
    groups: dict[str, int] = {}
    for name in names:
        group = industry.get(name)
        if group is not None:
            groups[group] = groups.get(group, 0) + 1
    covered = sum(min(count * c.cap, c.industry_cap) for count in groups.values())
    uncovered = sum(1 for name in names if name not in industry) * c.cap
    return covered + uncovered


def project(
    target: dict[str, float],
    c: Constraints = Constraints(),  # noqa: B008 - immutable API default required by task contract
    *,
    industry: dict[str, str] | None = None,
    current: dict[str, float] | None = None,
) -> ProjectResult:
    """Return deterministic projected weights and any feasibility/turnover notes."""
    if not (math.isfinite(c.cap) and 0 <= c.cap <= 1 and math.isfinite(c.gross) and 0 <= c.gross <= 1):
        raise ValueError("cap and gross must be finite values in [0, 1]")
    if not math.isfinite(c.industry_cap) or not 0 <= c.industry_cap <= 1:
        raise ValueError("industry_cap must be finite and in [0, 1]")
    if c.turnover_cap is not None and (not math.isfinite(c.turnover_cap) or c.turnover_cap < 0):
        raise ValueError("turnover_cap must be finite and non-negative")
    clean = {s: float(target[s]) for s in sorted(target) if math.isfinite(target[s]) and target[s] > 0}
    if not clean:
        return ProjectResult({}, ())
    total = sum(clean.values())
    goal = min(total, c.gross)
    weights = {s: v * goal / total for s, v in clean.items()}
    notes: list[str] = []
    capacity = _capacity(list(clean), c, industry)
    if capacity + _TOL < goal:
        weights = {s: min(w, c.cap) for s, w in weights.items()}
        if industry is not None:
            for group in sorted(set(industry.get(s) for s in clean if industry.get(s) is not None)):
                members = [s for s in clean if industry.get(s) == group]
                group_total = sum(weights[s] for s in members)
                if group_total > c.industry_cap:
                    for s in members:
                        weights[s] *= c.industry_cap / group_total
        notes.append(f"INFEASIBLE_GROSS: capacity {capacity:.12g} < target {goal:.12g}, keep cash")
    else:
        for _ in range(100):
            before = weights.copy()
            excess = 0.0
            for s in clean:
                if weights[s] > c.cap:
                    excess += weights[s] - c.cap
                    weights[s] = c.cap
            free = [s for s in clean if weights[s] < c.cap - _TOL]
            denom = sum(clean[s] for s in free)
            if excess > _TOL and denom > 0:
                for s in free:
                    weights[s] += excess * clean[s] / denom
            if industry is not None:
                groups: dict[str, list[str]] = {}
                for s in clean:
                    if s in industry:
                        groups.setdefault(industry[s], []).append(s)
                excess = 0.0
                for group in sorted(groups):
                    members = groups[group]
                    group_total = sum(weights[s] for s in members)
                    if group_total > c.industry_cap + _TOL:
                        factor = c.industry_cap / group_total
                        for s in members:
                            weights[s] *= factor
                        excess += group_total - c.industry_cap
                free = [
                    s
                    for s in clean
                    if s not in industry
                    or sum(weights[x] for x in groups.get(industry[s], ())) < c.industry_cap - _TOL
                ]
                free = [s for s in free if weights[s] < c.cap - _TOL]
                denom = sum(clean[s] for s in free)
                if excess > _TOL and denom > 0:
                    for s in free:
                        weights[s] += excess * clean[s] / denom
            if max(abs(weights[s] - before[s]) for s in clean) <= _TOL:
                break
    if current is not None and c.turnover_cap is not None:
        names = sorted(set(weights) | set(current))
        cur = {s: max(0.0, float(current.get(s, 0.0))) for s in names}
        turnover = 0.5 * sum(abs(weights.get(s, 0.0) - cur[s]) for s in names)
        if turnover > c.turnover_cap + _TOL:
            lam = c.turnover_cap / turnover if turnover else 0.0
            weights = {s: cur[s] + lam * (weights.get(s, 0.0) - cur[s]) for s in names}
            notes.append("TURNOVER_BLEND_MAY_VIOLATE_CAP")
    weights = {s: float(weights[s]) for s in sorted(weights) if weights[s] > 0}
    validate_target_weights(weights)
    return ProjectResult(weights, tuple(notes))
