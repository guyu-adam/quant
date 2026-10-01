"""Sharpe ratio deflation and combinatorially symmetric cross-validation."""

from __future__ import annotations

from collections.abc import Callable
from itertools import combinations
from math import log, sqrt

import numpy as np
from scipy.special import ndtr

_EULER_MASCHERONI = 0.5772156649015329


def psr(sr: float, n: int, skew: float, kurt: float, sr_benchmark: float = 0.0) -> float:
    """Return Probabilistic Sharpe Ratio.

    ``sr`` and ``sr_benchmark`` are non-annualized single-period Sharpe ratios;
    ``kurt`` is ordinary (Pearson) kurtosis, for which a normal distribution is 3.
    """
    if n <= 1:
        raise ValueError("n must be greater than 1")
    variance_factor = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr**2
    if variance_factor <= 0:
        raise ValueError("Sharpe ratio variance factor must be positive")
    z = (sr - sr_benchmark) * sqrt(n - 1) / sqrt(variance_factor)
    return float(ndtr(z))


def expected_max_sr(n_trials: int, var_sr: float) -> float:
    """Expected maximum SR across independent trials using the DSR paper approximation."""
    if n_trials < 1:
        raise ValueError("n_trials must be at least 1")
    if var_sr < 0:
        raise ValueError("var_sr must be non-negative")
    if n_trials == 1 or var_sr == 0:
        return 0.0
    n = float(n_trials)
    return float(
        sqrt(var_sr)
        * (
            (1.0 - _EULER_MASCHERONI) * _NORMAL_PPF(1.0 - 1.0 / n)
            + _EULER_MASCHERONI * _NORMAL_PPF(1.0 - 1.0 / (n * np.e))
        )
    )


def _NORMAL_PPF(probability: float) -> float:
    from scipy.special import ndtri

    return float(ndtri(probability))


def dsr(sr: float, n: int, skew: float, kurt: float, n_trials: int, var_sr: float) -> float:
    """Return the Deflated Sharpe Ratio using expected maximum SR as benchmark."""
    benchmark = expected_max_sr(n_trials, var_sr)
    return psr(sr, n, skew, kurt, sr_benchmark=benchmark)


def _sharpe_matrix(values: np.ndarray) -> np.ndarray:
    deviation = np.std(values, axis=0, ddof=1)  # lookahead: ok 全样本用于回测评估指标
    with np.errstate(divide="ignore", invalid="ignore"):
        result = np.mean(values, axis=0) / deviation  # lookahead: ok 全样本用于回测评估指标
    return np.where(deviation == 0, -np.inf, result)


def pbo_cscv(
    perf: np.ndarray,
    n_splits: int = 16,
    metric: Callable[[np.ndarray], np.ndarray] | None = None,
) -> dict[str, float | np.ndarray | int]:
    """Compute CSCV PBO and OOS rank logits for a time-by-configuration array.

    If T is not divisible by ``n_splits``, discard the first T % S rows before
    splitting the remainder into equal contiguous blocks. ``metric`` receives a
    (selected periods, configurations) array and must return one score per config.
    """
    values = np.asarray(perf, dtype=float)
    if values.ndim != 2:
        raise ValueError("perf must be a two-dimensional (T, N) array")
    t, n_configs = values.shape
    if n_splits % 2:
        raise ValueError("n_splits must be even")
    if n_splits > t:
        raise ValueError("n_splits must not exceed T")
    if n_configs < 2:
        raise ValueError("perf must contain at least two configurations")
    if not np.isfinite(values).all():
        raise ValueError("perf must contain only finite values")
    remainder = t % n_splits
    values = values[remainder:]
    block_size = len(values) // n_splits
    blocks = values.reshape(n_splits, block_size, n_configs)
    scorer = _sharpe_matrix if metric is None else metric
    use_fast_sharpe = metric is None

    if use_fast_sharpe:
        block_sum = blocks.sum(axis=1)
        block_sumsq = np.square(blocks).sum(axis=1)
    logits: list[float] = []
    half = n_splits // 2
    for chosen in combinations(range(n_splits), half):
        mask = np.zeros(n_splits, dtype=bool)
        mask[list(chosen)] = True
        if use_fast_sharpe:
            scores: list[np.ndarray] = []
            for in_sample in (True, False):
                selected = mask if in_sample else ~mask
                count = int(selected.sum()) * block_size
                sums = block_sum[selected].sum(axis=0)
                sumsquares = block_sumsq[selected].sum(axis=0)
                mean = sums / count
                variance = (sumsquares - count * mean**2) / (count - 1)
                scores.append(np.divide(mean, np.sqrt(np.maximum(variance, 0)),
                                        out=np.full(n_configs, -np.inf), where=variance > 0))
            in_scores, out_scores = scores
        else:
            in_scores = np.asarray(scorer(blocks[mask].reshape(-1, n_configs)))
            out_scores = np.asarray(scorer(blocks[~mask].reshape(-1, n_configs)))
            if in_scores.shape != (n_configs,) or out_scores.shape != (n_configs,):
                raise ValueError("metric must return a one-dimensional score per configuration")
        winner = int(np.argmax(in_scores))
        # Average ranks handle ties; rank 1 is the worst score, N the best.
        rank = 1 + np.count_nonzero(out_scores < out_scores[winner])
        rank += (np.count_nonzero(out_scores == out_scores[winner]) - 1) / 2
        omega = rank / (n_configs + 1)
        logits.append(log(omega / (1.0 - omega)))
    logit_array = np.asarray(logits, dtype=float)
    return {"pbo": float(np.mean(logit_array <= 0)), "logits": logit_array,  # lookahead: ok CSCV 组合汇总评估
            "n_combinations": len(logit_array)}
