"""本模块产出的是训练目标，禁止作为特征或在 on_bar 里调用。

Cross-sectional excess-return ranks equal raw-return ranks because subtracting
the same date's benchmark return cannot change the ordering; no benchmark is
needed here.
"""

from __future__ import annotations

import numbers

import numpy as np
import pandas as pd


def forward_rank_label(
    close_hfq: pd.DataFrame,
    horizon: int,
    *,
    eligible: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Build t+1-to-t+1+horizon forward-return percentile labels and label end dates."""
    if isinstance(horizon, bool) or not isinstance(horizon, numbers.Integral) or horizon < 0:
        raise ValueError("horizon must be a non-negative integer")
    if eligible is not None and (
        not eligible.index.equals(close_hfq.index) or not eligible.columns.equals(close_hfq.columns)
    ):
        raise ValueError("eligible must have the same index and columns as close_hfq")

    offset = int(horizon) + 1
    # lookahead: ok 标签定义，只用于训练目标
    future_start = close_hfq.shift(-1)
    # lookahead: ok 标签定义，只用于训练目标
    future_end = close_hfq.shift(-offset)
    returns = future_end.div(future_start).sub(1)
    finite = pd.DataFrame(
        np.isfinite(returns.to_numpy(dtype=float)), index=returns.index, columns=returns.columns
    )
    valid = finite if eligible is None else finite & eligible.fillna(False).astype(bool)
    ranked = returns.where(valid).rank(axis=1, method="average", pct=True)

    ends = pd.Series(pd.NaT, index=close_hfq.index, dtype="datetime64[ns]", name="label_end")
    if offset < len(close_hfq.index):
        ends.iloc[:-offset] = pd.to_datetime(close_hfq.index[offset:])
    return ranked, ends
