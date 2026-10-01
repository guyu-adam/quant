"""Performance metrics and cross sectional information coefficient analysis."""

from __future__ import annotations

import numpy as np
import pandas as pd

PERIODS = 252


def _returns(r: pd.Series) -> np.ndarray:
    if not isinstance(r, pd.Series):
        raise TypeError("r must be a pandas Series")
    values = r.to_numpy(dtype=float)
    if np.isnan(values).any():
        raise ValueError("r contains NaN")
    return values


def nav(r: pd.Series) -> np.ndarray:
    values = _returns(r)
    return np.concatenate(([1.0], np.cumprod(1.0 + values)))


def total_return(r: pd.Series) -> float:
    return float(nav(r)[-1] - 1.0)


def ann_return(r: pd.Series) -> float:
    values = _returns(r)
    ending_nav = nav(r)[-1]
    if ending_nav <= 0:
        return -1.0
    if len(values) == 0:
        return float("nan")
    return float(ending_nav ** (PERIODS / len(values)) - 1.0)


def ann_vol(r: pd.Series) -> float:
    return float(np.std(_returns(r), ddof=1) * np.sqrt(PERIODS))  # lookahead: ok 全回测区间评估统计


def sharpe(r: pd.Series, rf: float | pd.Series = 0) -> float:
    excess = _returns(r) - np.asarray(rf)
    deviation = np.std(excess, ddof=1)  # lookahead: ok 全回测区间评估统计
    if not deviation:
        return float("nan")
    return float(
        np.mean(excess) / deviation * np.sqrt(PERIODS)  # lookahead: ok 全回测区间评估统计
    )


def sortino(r: pd.Series, rf: float | pd.Series = 0) -> float:
    excess = _returns(r) - np.asarray(rf)
    downside = np.sqrt(np.mean(np.minimum(excess, 0.0) ** 2))  # lookahead: ok 全回测区间评估统计
    if not downside:
        return float("nan")
    return float(
        np.mean(excess) / downside * np.sqrt(PERIODS)  # lookahead: ok 全回测区间评估统计
    )


def max_drawdown(r: pd.Series) -> float:
    values = nav(r)
    return float(np.min(values / np.maximum.accumulate(values) - 1.0))  # lookahead: ok 全回测区间评估统计


def max_drawdown_duration(r: pd.Series) -> int:
    values = nav(r)
    peak = values[0]
    duration = longest = 0
    for value in values[1:]:
        if value >= peak:
            peak = value
            duration = 0
        else:
            duration += 1
            longest = max(longest, duration)
    return longest


def calmar(r: pd.Series) -> float:
    drawdown = max_drawdown(r)
    return ann_return(r) / abs(drawdown) if drawdown else float("nan")


def win_rate(r: pd.Series) -> float:
    values = _returns(r)
    active = values != 0
    return float(np.count_nonzero(values > 0) / np.count_nonzero(active)) if active.any() else float("nan")


def pl_ratio(r: pd.Series) -> float:
    values = _returns(r)
    winners, losers = values[values > 0], values[values < 0]
    if not len(losers):
        return float("nan")
    return float(
        np.mean(winners) / abs(np.mean(losers))  # lookahead: ok 全回测区间评估统计
    )


def ann_turnover(turnover: pd.Series) -> float:
    return float(turnover.mean() * PERIODS)  # lookahead: ok 全回测区间评估统计


def cost_share(gross_pnl: pd.Series, cost: pd.Series) -> float:
    gross = float(gross_pnl.sum())
    return float(cost.sum() / gross) if gross > 0 else float("nan")


def capacity(
    trade_w: pd.DataFrame, adv: pd.DataFrame, max_participation: float = 0.1, q: float = 0.05
) -> float:
    if not trade_w.index.equals(adv.index) or not trade_w.columns.equals(adv.columns):
        raise ValueError("trade_w and adv must have identical index and columns")
    trades = trade_w.to_numpy(dtype=float)
    volumes = adv.to_numpy(dtype=float)
    mask = trades != 0
    if not mask.any():
        return float("nan")
    caps = np.zeros(mask.sum(), dtype=float)
    selected_adv = volumes[mask]
    selected_trade = np.abs(trades[mask])
    valid = np.isfinite(selected_adv) & (selected_adv > 0)
    caps[valid] = max_participation * selected_adv[valid] / selected_trade[valid]
    return float(np.quantile(caps, q))  # lookahead: ok 全交易样本容量评估统计


def summary(
    r: pd.Series,
    turnover: pd.Series | None = None,
    *,
    rf: float | pd.Series = 0,
    gross_pnl: pd.Series | None = None,
    cost: pd.Series | None = None,
    trade_w: pd.DataFrame | None = None,
    adv: pd.DataFrame | None = None,
    max_participation: float = 0.1,
    q: float = 0.05,
) -> dict[str, float | int]:
    result: dict[str, float | int] = {
        "total_return": total_return(r),
        "ann_return": ann_return(r),
        "ann_vol": ann_vol(r),
        "sharpe": sharpe(r, rf),
        "sortino": sortino(r, rf),
        "max_drawdown": max_drawdown(r),
        "max_drawdown_duration": max_drawdown_duration(r),
        "calmar": calmar(r),
        "win_rate": win_rate(r),
        "pl_ratio": pl_ratio(r),
    }
    if turnover is not None:
        result["ann_turnover"] = ann_turnover(turnover)
    if gross_pnl is not None and cost is not None:
        result["cost_share"] = cost_share(gross_pnl, cost)
    if trade_w is not None and adv is not None:
        result["capacity"] = capacity(trade_w, adv, max_participation, q)
    return result


def forward_returns(close: pd.DataFrame, h: int) -> pd.DataFrame:
    """Compute forward returns for IC analysis; never call from a strategy or factor."""
    # lookahead: ok 评估用的前瞻收益，只用于 IC 分析，不进策略
    return close.shift(-h) / close - 1.0


def ic_series(
    factor: pd.DataFrame, fwd: pd.DataFrame, method: str = "spearman", min_obs: int = 30
) -> pd.Series:
    if not factor.index.equals(fwd.index) or not factor.columns.equals(fwd.columns):
        raise ValueError("factor and fwd must have identical index and columns")
    if method not in {"spearman", "pearson"}:
        raise ValueError("method must be 'spearman' or 'pearson'")
    values = []
    for date in factor.index:
        pair = pd.concat([factor.loc[date], fwd.loc[date]], axis=1).dropna()
        if len(pair) < min_obs:
            values.append(np.nan)
            continue
        left, right = pair.iloc[:, 0], pair.iloc[:, 1]
        if method == "spearman":
            left, right = (
                left.rank(method="average"),  # lookahead: ok 截面 IC 排名
                right.rank(method="average"),  # lookahead: ok 截面 IC 排名
            )
        values.append(left.corr(right, method="pearson"))
    return pd.Series(values, index=factor.index, name="ic", dtype=float)


def ic_summary(ic: pd.Series) -> dict[str, float | int]:
    values = ic.dropna().to_numpy(dtype=float)
    n = len(values)
    mean = float(np.mean(values)) if n else float("nan")  # lookahead: ok IC 汇总评估统计
    deviation = float(np.std(values, ddof=1)) if n > 1 else float("nan")  # lookahead: ok IC 汇总评估统计
    return {
        "mean": mean,
        "std": deviation,
        "ir": mean / deviation if deviation and np.isfinite(deviation) else float("nan"),
        "t": mean / deviation * np.sqrt(n) if deviation and np.isfinite(deviation) else float("nan"),
        "pos_frac": float(np.count_nonzero(values > 0) / n) if n else float("nan"),
        "n": n,
    }
