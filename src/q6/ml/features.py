"""Point-in-time-safe time-series features for ML models."""

from __future__ import annotations

import numpy as np
import pandas as pd

from q6.alpha import ops
from q6.alpha.library import FACTORS

WINDOWS = (5, 10, 20, 60, 120)
ROLLING_NAMES = ("ret", "vol", "turn", "rank", "range", "ma", "maxret", "minret", "amihud")
_ROLLING_NAMES = {
    ("range", 20): "range_ts_20",
    ("amihud", 20): "amihud_ts_20",
}
FEATURE_NAMES = tuple(FACTORS) + tuple(
    _ROLLING_NAMES.get((name, window), f"{name}_{window}")
    for window in WINDOWS
    for name in ROLLING_NAMES
)


def build_features(d: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Build 75 causal features, each with the same wide shape as the inputs."""
    features = {name: factor(d) for name, factor in FACTORS.items()}
    close, high, low, turn, amount = (d[k] for k in ("close", "high", "low", "turn", "amount"))
    safe_amount = amount.where(amount > 0)
    for window in WINDOWS:
        ret = ops.returns(close, window)
        rolling = {
            "ret": ret,
            "vol": ops.ts_std(ret, window),
            "turn": ops.ts_mean(turn, window),
            "rank": ops.ts_rank(close, window),
            "range": ops.ts_mean((high - low).div(close), window),
            "ma": close.div(ops.ts_mean(close, window)) - 1,
            "maxret": ops.ts_max(ret, window),
            "minret": ops.ts_min(ret, window),
            "amihud": ops.ts_mean(ret.abs().div(safe_amount) * 1e8, window),
        }
        features.update(
            {_ROLLING_NAMES.get((name, window), f"{name}_{window}"): value for name, value in rolling.items()}
        )
    return features


def assert_no_inf(long_df: pd.DataFrame) -> None:
    """Raise on any infinite value, naming the offending feature column."""
    if long_df.empty:
        return
    values = long_df.to_numpy(dtype=float, copy=False)
    bad = np.isinf(values)
    if bad.any():
        positions = np.flatnonzero(bad.any(axis=0))
        names = [str(long_df.columns[pos]) for pos in positions]
        raise ValueError(f"infinite values in feature(s): {', '.join(names)}")


def to_long(
    features: dict[str, pd.DataFrame], mask: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Convert wide feature frames to (date, code) rows in canonical feature order."""
    missing = [name for name in FEATURE_NAMES if name not in features]
    if missing:
        raise ValueError(f"missing feature(s): {', '.join(missing)}")
    first = features[FEATURE_NAMES[0]]
    for name in FEATURE_NAMES:
        frame = features[name]
        if not frame.index.equals(first.index) or not frame.columns.equals(first.columns):
            raise ValueError(f"feature {name} has mismatched index or columns")
    if mask is not None and (not mask.index.equals(first.index) or not mask.columns.equals(first.columns)):
        raise ValueError("mask must have the same index and columns as feature frames")
    arrays = [features[name].to_numpy().reshape(-1) for name in FEATURE_NAMES]
    long = pd.DataFrame(dict(zip(FEATURE_NAMES, arrays, strict=True)))
    row_index = pd.MultiIndex.from_product([first.index, first.columns], names=["date", "code"])
    long.index = row_index
    if mask is not None:
        long = long.loc[mask.to_numpy(dtype=bool).reshape(-1)]
    return long
