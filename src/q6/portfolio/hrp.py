"""Hierarchical risk parity weights following López de Prado (2016)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform


def hrp_weights(returns: pd.DataFrame) -> pd.Series:
    """Calculate HRP weights from a complete returns matrix."""
    if returns.shape[1] == 0:
        raise ValueError("returns must contain at least one asset")
    if returns.isna().any().any() or not np.isfinite(returns.to_numpy(dtype=float)).all():
        raise ValueError("returns contain NaN or non-finite values")
    names = list(returns.columns)
    cov = returns.cov().to_numpy(dtype=float)
    variances = np.diag(cov)
    if not np.isfinite(variances).all() or np.any(variances <= 0):
        raise ValueError("returns contain a zero-variance or invalid column")
    if len(names) == 1:
        return pd.Series([1.0], index=names, dtype=float)
    corr = returns.corr().to_numpy(dtype=float)
    distance = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, None))
    condensed = squareform(distance, checks=False)
    order = leaves_list(linkage(condensed, method="single"))
    ordered_cov = cov[np.ix_(order, order)]
    weights = np.ones(len(names), dtype=float)

    def cluster_variance(indices: np.ndarray) -> float:
        block = ordered_cov[np.ix_(indices, indices)]
        inv_diag = 1.0 / np.diag(block)
        vector = inv_diag / inv_diag.sum()
        return float(vector @ block @ vector)

    clusters = [np.arange(len(names))]
    while clusters:
        next_clusters: list[np.ndarray] = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            split = len(cluster) // 2
            left, right = cluster[:split], cluster[split:]
            var_left, var_right = cluster_variance(left), cluster_variance(right)
            alpha = 1.0 - var_left / (var_left + var_right)
            weights[left] *= alpha
            weights[right] *= 1.0 - alpha
            next_clusters.extend((left, right))
        clusters = next_clusters
    result = np.empty_like(weights)
    result[order] = weights
    result /= result.sum()
    return pd.Series(result, index=names, dtype=float)
