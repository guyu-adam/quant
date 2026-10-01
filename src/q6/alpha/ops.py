"""Pandas implementations of common time-series and cross-sectional alpha operators."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _window(d: int) -> int:
    if isinstance(d, bool) or not isinstance(d, (int, np.integer)) or d <= 0:
        raise ValueError("d must be a positive integer")
    return int(d)


def _same_shape(a: pd.DataFrame, b: pd.DataFrame) -> None:
    if not isinstance(a, pd.DataFrame) or not isinstance(b, pd.DataFrame):
        raise TypeError("inputs must be pandas DataFrames")
    if not a.index.equals(b.index) or not a.columns.equals(b.columns):
        raise ValueError("inputs must have identical index and columns")


def delay(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return x shifted by d rows, so output[t] = x[t-d]."""
    return x.shift(_window(d))


def delta(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return x[t] - x[t-d]."""
    return x - delay(x, d)


def ts_sum(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return the complete d-row rolling sum."""
    return x.rolling(_window(d), min_periods=_window(d)).sum()


def ts_mean(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return the complete d-row rolling arithmetic mean."""
    return x.rolling(_window(d), min_periods=_window(d)).mean()


def ts_std(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return the complete d-row rolling sample standard deviation (ddof=1)."""
    return x.rolling(_window(d), min_periods=_window(d)).std(ddof=1)


def ts_min(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return the complete d-row rolling minimum."""
    return x.rolling(_window(d), min_periods=_window(d)).min()


def ts_max(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return the complete d-row rolling maximum."""
    return x.rolling(_window(d), min_periods=_window(d)).max()


def ts_argmin(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return the zero-based position of the minimum in each complete window."""
    return _window_extreme(x, d, False)


def ts_argmax(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return the zero-based position of the maximum in each complete window."""
    return _window_extreme(x, d, True)


def _window_extreme(x: pd.DataFrame, d: int, maximum: bool) -> pd.DataFrame:
    d = _window(d)
    if len(x) < d:
        return pd.DataFrame(np.nan, index=x.index, columns=x.columns)
    a = x.to_numpy(dtype=float, copy=False)
    out = np.full(a.shape, np.nan)
    step = max(1, 20_000_000 // (a.shape[0] * d))
    for start in range(0, a.shape[1], step):
        block = a[:, start : start + step]
        win = np.lib.stride_tricks.sliding_window_view(block, d, axis=0)
        valid = ~np.isnan(win).any(axis=-1)
        idx = np.argmax(win, axis=-1) if maximum else np.argmin(win, axis=-1)
        dest = out[d - 1 :, start : start + step]
        dest[:] = np.where(valid, idx, np.nan)
    return pd.DataFrame(out, index=x.index, columns=x.columns)


def ts_rank(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return today's midrank percentile among d observations, normalized by d-1."""
    d = _window(d)
    if d == 1:
        return pd.DataFrame(np.nan, index=x.index, columns=x.columns)
    if len(x) < d:
        return pd.DataFrame(np.nan, index=x.index, columns=x.columns)
    a = x.to_numpy(dtype=float, copy=False)
    out = np.full(a.shape, np.nan)
    step = max(1, 20_000_000 // (a.shape[0] * d))
    for start in range(0, a.shape[1], step):
        win = np.lib.stride_tricks.sliding_window_view(a[:, start : start + step], d, axis=0)
        last = win[..., -1, None]
        valid = ~np.isnan(win).any(axis=-1)
        ranks = ((win < last).sum(axis=-1) + 0.5 * ((win == last).sum(axis=-1) - 1)) / (d - 1)
        out[d - 1 :, start : start + step] = np.where(valid, ranks, np.nan)
    return pd.DataFrame(out, index=x.index, columns=x.columns)


def ts_zscore(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return (x minus rolling mean) divided by rolling sample standard deviation."""
    s = ts_std(x, d)
    return (x - ts_mean(x, d)).div(s.where(s != 0))


def ts_product(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return the complete d-row rolling product."""
    d = _window(d)
    return _window_reduce(x, d, lambda a: np.prod(a, axis=-1))


def ts_corr(x: pd.DataFrame, y: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return complete-window rolling Pearson correlation between x and y."""
    _same_shape(x, y)
    return x.rolling(_window(d), min_periods=_window(d)).corr(y)


def ts_cov(x: pd.DataFrame, y: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return complete-window rolling sample covariance (ddof=1) of x and y."""
    _same_shape(x, y)
    return x.rolling(_window(d), min_periods=_window(d)).cov(y, ddof=1)


def decay_linear(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Return rolling weighted mean with chronological weights 1 through d."""
    d = _window(d)
    weights = np.arange(1, d + 1, dtype=float)
    return _window_reduce(x, d, lambda a: np.tensordot(a, weights, axes=([-1], [0])) / weights.sum())


def _window_reduce(x: pd.DataFrame, d: int, reducer) -> pd.DataFrame:
    if len(x) < d:
        return pd.DataFrame(np.nan, index=x.index, columns=x.columns)
    a = x.to_numpy(dtype=float, copy=False)
    out = np.full(a.shape, np.nan)
    step = max(1, 20_000_000 // (a.shape[0] * d))
    for start in range(0, a.shape[1], step):
        win = np.lib.stride_tricks.sliding_window_view(a[:, start : start + step], d, axis=0)
        valid = ~np.isnan(win).any(axis=-1)
        values = reducer(win)
        out[d - 1 :, start : start + step] = np.where(valid, values, np.nan)
    return pd.DataFrame(out, index=x.index, columns=x.columns)


def ewm_mean(x: pd.DataFrame, halflife: float) -> pd.DataFrame:
    """Return the adjust=False exponentially weighted mean with the given positive halflife."""
    valid_type = isinstance(halflife, (int, float, np.integer, np.floating))
    if isinstance(halflife, bool) or not valid_type or halflife <= 0:
        raise ValueError("halflife must be positive")
    return x.ewm(halflife=float(halflife), adjust=False, min_periods=1).mean()


def returns(x: pd.DataFrame, d: int = 1) -> pd.DataFrame:
    """Return x[t] / x[t-d] - 1."""
    return x.div(delay(x, d)) - 1


def cs_rank(x: pd.DataFrame) -> pd.DataFrame:
    """Return row-wise average percentile ranks, ignoring missing values."""
    return x.rank(axis=1, pct=True, method="average")


def cs_zscore(x: pd.DataFrame) -> pd.DataFrame:
    """Return row-wise sample-standardized values, with constant rows entirely missing."""
    s = x.std(axis=1, ddof=1).replace(0, np.nan)
    return x.sub(x.mean(axis=1), axis=0).div(s, axis=0)


def cs_demean(x: pd.DataFrame) -> pd.DataFrame:
    """Return each value minus its row's mean."""
    return x.sub(x.mean(axis=1), axis=0)


def cs_scale(x: pd.DataFrame, a: float = 1.0) -> pd.DataFrame:
    """Scale each row so the sum of absolute values equals a; zero rows become missing."""
    denom = x.abs().sum(axis=1).replace(0, np.nan)
    return x.mul(a).div(denom, axis=0)


def cs_winsorize(x: pd.DataFrame, k: float = 3.0) -> pd.DataFrame:
    """Clip each row to median plus or minus k times 1.4826 times its MAD."""
    med = x.median(axis=1)
    mad = x.sub(med, axis=0).abs().median(axis=1)
    radius = k * 1.4826 * mad
    return x.clip(lower=med - radius, upper=med + radius, axis=0)


def sign(x: pd.DataFrame) -> pd.DataFrame:
    """Return the elementwise sign of x."""
    return np.sign(x)


def signed_power(x: pd.DataFrame, p: float) -> pd.DataFrame:
    """Return sign(x) times abs(x) raised to p."""
    return np.sign(x) * x.abs().pow(p)


def safe_div(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Return a divided by b, replacing zero-denominator results with missing values."""
    _same_shape(a, b)
    return a.div(b).where(b != 0)
