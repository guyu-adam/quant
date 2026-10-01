"""基准指数收益计算；当前来源是价格指数，不含分红，会低估基准收益。"""

from __future__ import annotations

import pandas as pd


def index_returns(bench_df: pd.DataFrame, code: str) -> pd.Series:
    """用 Baostock 指数 preclose 计算收盘价格指数日收益（不含分红）。"""
    rows = bench_df.loc[bench_df["code"].eq(code)].sort_values("date")
    returns = rows["close"].astype(float).div(rows["preclose"].astype(float)).sub(1.0)
    return pd.Series(returns.to_numpy(), index=pd.to_datetime(rows["date"]), name=code)


def buy_and_hold(close_wide: pd.DataFrame, weights0: pd.Series) -> pd.Series:
    """期初按权重买入并持有、不再平衡的组合日收益；不含交易成本。"""
    if not close_wide.columns.equals(weights0.index):
        raise ValueError("close_wide columns must match weights0 index")
    if weights0.sum() == 0:
        raise ValueError("weights0 sum must be nonzero")
    holdings = close_wide.astype(float).div(close_wide.iloc[0].astype(float), axis="columns").mul(
        weights0.astype(float), axis="columns"
    )
    portfolio_value = holdings.sum(axis="columns")
    result = portfolio_value.pct_change(fill_method=None)
    result.iloc[0] = 0.0
    result.name = "buy_and_hold"
    return result
