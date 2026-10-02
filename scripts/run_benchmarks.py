"""Run event-engine portfolio benchmarks and price-index comparisons before the lockbox."""

# ruff: noqa: I001 -- q6 must load first to choose Arrow's memory pool before pandas does.

from __future__ import annotations

import os
import time

# Select q6's configured Arrow pool before pandas imports pyarrow.
import q6  # noqa: F401, I001
import numpy as np
import pandas as pd

from q6.data.benchmarks import load_benchmarks
from q6.engine.event import EngineConfig, EventEngine
from q6.engine.feed import SnapshotFeed
from q6.research.benchmark import index_returns
from q6.strategy.benchmarks import BuyAndHold, UniverseEqualWeight

START, END = "2006-01-01", "2024-06-28"
TRADING_DAYS = 244
# 组合基准的初始资金。宇宙约 800 只等权：100 万时每只约 1,190 元，低于引擎的 min_trade_value=2,000，
# 单独跑 2015 年实测 0 笔成交、全程持现金（Cen 审查 P2-24 时发现）。5,000 万时每只约 5.9 万元，
# 300 元的股票也能买一手。策略本身仍按 100 万跑；两者的冲击成本口径因此不同（基准规模大、冲击更高，偏保守）。
BENCH_CASH = 50_000_000.0


def summarize_engine(name: str, result) -> tuple[dict, pd.Series]:
    daily = result.daily
    returns = result.returns
    equity = daily["equity"]
    years = len(daily) / TRADING_DAYS
    cagr = (equity.iloc[-1] / result.initial_cash) ** (1 / years) - 1
    vol = float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS))
    max_dd = float((equity / equity.cummax().clip(lower=result.initial_cash) - 1).min())
    costs = float(daily["fees"].sum() + daily["cost_slip_imp"].sum())
    # 单边年化换手，口径同 scripts/backtest_example.py
    turnover = float((daily["buy_value"].sum() + daily["sell_value"].sum()) / 2 / equity.mean() / years)
    summary = {
        "name": name, "start": daily.index[0].date(), "CAGR": cagr, "ann_vol": vol, "maxDD": max_dd,
        "fees+slip": costs / result.initial_cash, "turnover": turnover,
        "cash_ratio": float((daily["cash"] / equity).mean()),
    }
    yearly = (1 + returns).groupby(returns.index.year).prod() - 1
    return summary, yearly


def summarize_index(name: str, returns: pd.Series) -> tuple[dict, pd.Series]:
    returns = returns.loc[pd.Timestamp(START):pd.Timestamp(END)]
    returns = returns.replace([np.inf, -np.inf], np.nan).dropna()
    years = len(returns) / TRADING_DAYS
    nav = (1 + returns).cumprod()
    cagr = float(nav.iloc[-1] ** (1 / years) - 1)
    vol = float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS))
    max_dd = float((nav / nav.cummax().clip(lower=1.0) - 1).min())
    return {
        "name": name, "start": returns.index[0].date(), "CAGR": cagr, "ann_vol": vol, "maxDD": max_dd,
        "fees+slip": None, "turnover": None, "cash_ratio": None,
    }, (1 + returns).groupby(returns.index.year).prod() - 1


def main() -> None:
    root = os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots")
    snapshot = os.environ.get("Q6_SNAPSHOT", "6252e931a86bda15")
    summaries, yearly = [], {}
    for strategy in (UniverseEqualWeight(), BuyAndHold()):
        feed = SnapshotFeed(root, snapshot, extra_fields=strategy.spec.fields)
        started = time.perf_counter()
        result = EventEngine(EngineConfig(initial_cash=BENCH_CASH)).run(strategy, feed, START, END)
        elapsed = time.perf_counter() - started
        summary, annual = summarize_engine(strategy.name, result)
        summaries.append(summary)
        yearly[strategy.name] = annual
        print(f"run={strategy.name} days={len(result.daily)} fills={len(result.fills)} "
              f"elapsed_seconds={elapsed:.1f} snapshot={snapshot}")
        print(f"reasons[{strategy.name}]={dict(sorted(result.reasons.items()))}")

    benchmark = load_benchmarks()
    for name, code in (("hs300_price", "sh.000300"), ("zz800_price", "sh.000906")):
        summary, annual = summarize_index(name, index_returns(benchmark, code))
        summaries.append(summary)
        yearly[name] = annual

    print(f"\nsummary (portfolio benchmarks initial_cash={BENCH_CASH:,.0f}; "
          "costs = fees+slip+impact / initial cash; turnover = one-way annual; "
          "CAGR over each row's own start)")
    print(f"{'name':<16} {'start':>10} {'CAGR':>9} {'ann_vol':>9} {'maxDD':>9} {'costs':>9} {'turnover':>9} "
          f"{'cash%':>7}")
    for row in summaries:
        cost = "n/a" if row["fees+slip"] is None else f"{row['fees+slip']:.2%}"
        turn = "n/a" if row["turnover"] is None else f"{row['turnover']:.3f}"
        cash = "n/a" if row["cash_ratio"] is None else f"{row['cash_ratio']:.1%}"
        print(f"{row['name']:<16} {row['start']!s:>10} {row['CAGR']:>8.2%} {row['ann_vol']:>8.2%} "
              f"{row['maxDD']:>8.2%} {cost:>9} {turn:>9} {cash:>7}")

    print("\nyear  " + "  ".join(f"{row['name']:>14}" for row in summaries))
    all_years = sorted(set().union(*(s.index for s in yearly.values())))
    for year in all_years:
        vals = [yearly[row["name"]].get(year, np.nan) for row in summaries]
        print(f"{year}  " + "  ".join(f"{value:>14.2%}" if np.isfinite(value) else f"{'n/a':>14}"
                                       for value in vals))


if __name__ == "__main__":
    main()
