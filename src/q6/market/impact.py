"""Fixed slippage and square-root market impact cost model."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from q6.core.types import Side


@dataclass(frozen=True, slots=True)
class ImpactResult:
    slippage_rate: float
    impact_rate: float

    @property
    def total_rate(self) -> float:
        return self.slippage_rate + self.impact_rate


def _validate_parameters(slippage_bp: float, impact_coef: float, cost_multiplier: float) -> None:
    for name, value in (
        ("slippage_bp", slippage_bp),
        ("impact_coef", impact_coef),
        ("cost_multiplier", cost_multiplier),
    ):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} 必须是有限非负数，得到 {value!r}")


def cost_rate(
    qty_value: float,
    adv_value: float,
    sigma: float,
    *,
    slippage_bp: float = 5.0,
    impact_coef: float = 0.1,
    cost_multiplier: float = 1.0,
) -> ImpactResult:
    """Return separately reported one-sided slippage and impact rates."""
    _validate_parameters(slippage_bp, impact_coef, cost_multiplier)
    if not math.isfinite(qty_value) or qty_value < 0:
        raise ValueError(f"qty_value 必须是有限非负数，得到 {qty_value!r}")
    if not math.isfinite(sigma) or sigma < 0:
        raise ValueError(f"sigma 必须是有限非负数，得到 {sigma!r}")
    if qty_value == 0:
        return ImpactResult(0.0, 0.0)
    if not math.isfinite(adv_value) or adv_value <= 0:
        raise ValueError(f"qty_value > 0 时 adv_value 必须是有限正数，得到 {adv_value!r}")
    slippage = cost_multiplier * slippage_bp * 1e-4
    impact = cost_multiplier * impact_coef * sigma * math.sqrt(qty_value / adv_value)
    if not math.isfinite(slippage) or not math.isfinite(impact):
        raise ValueError("成本率计算结果必须是有限数")
    return ImpactResult(slippage, impact)


def exec_price(ref_price: float, side: Side, r: ImpactResult) -> float:
    """Apply the modeled cost to a positive reference price."""
    if not math.isfinite(ref_price) or ref_price <= 0:
        raise ValueError(f"ref_price 必须是有限正数，得到 {ref_price!r}")
    if not isinstance(side, Side):
        raise ValueError(f"side 必须是 Side，得到 {side!r}")
    if not math.isfinite(r.total_rate) or r.total_rate < 0:
        raise ValueError("成本率必须是有限非负数")
    if side is Side.BUY:
        return ref_price * (1 + r.total_rate)
    if r.total_rate >= 1:
        raise ValueError("卖出成本率必须小于 1")
    return ref_price * (1 - r.total_rate)


def cost_rate_array(
    qty_value: np.ndarray,
    adv_value: np.ndarray,
    sigma: np.ndarray,
    *,
    slippage_bp: float = 5.0,
    impact_coef: float = 0.1,
    cost_multiplier: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized cost model; all inputs follow NumPy broadcasting rules."""
    _validate_parameters(slippage_bp, impact_coef, cost_multiplier)
    try:
        qty, adv, vol = np.broadcast_arrays(
            np.asarray(qty_value, dtype=float),
            np.asarray(adv_value, dtype=float),
            np.asarray(sigma, dtype=float),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("qty_value、adv_value、sigma 必须可广播为数值数组") from error
    if np.any(~np.isfinite(qty)) or np.any(qty < 0):
        raise ValueError("qty_value 必须全部是有限非负数")
    if np.any(~np.isfinite(vol)) or np.any(vol < 0):
        raise ValueError("sigma 必须全部是有限非负数")
    active = qty > 0
    if np.any(active & (~np.isfinite(adv) | (adv <= 0))):
        raise ValueError("qty_value > 0 时 adv_value 必须是有限正数")
    slippage = np.zeros(qty.shape, dtype=float)
    impact = np.zeros(qty.shape, dtype=float)
    slippage[active] = cost_multiplier * slippage_bp * 1e-4
    impact[active] = cost_multiplier * impact_coef * vol[active] * np.sqrt(qty[active] / adv[active])
    if np.any(~np.isfinite(slippage)) or np.any(~np.isfinite(impact)):
        raise ValueError("成本率计算结果必须是有限数")
    return slippage, impact
