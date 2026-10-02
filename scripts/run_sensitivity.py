"""Run full-snapshot robustness grids. Use: uv run --group report python scripts/run_sensitivity.py"""

from __future__ import annotations

import itertools
import os
from functools import partial
from pathlib import Path

import matplotlib.pyplot as plt

from q6.engine.event import EngineConfig
from q6.research.sensitivity import Variant, grid, run_grid
from q6.strategy.cs_multifactor import CSMultiFactor
from q6.strategy.examples import LowVolEqualWeight

START, END = "2006-01-01", "2024-06-28"
ROOT = Path(__file__).resolve().parents[1]
BATCH_SIZE = 8


def _default_row(frame, strategy_factory, grid_name):
    """Find the row matching declared engine and strategy defaults."""
    engine = EngineConfig()
    match = engine.match
    expected = {
        "cost_multiplier": match.cost_multiplier,
        "impact_coef": match.impact_coef,
        "slippage_bp": match.slippage_bp,
        "limit_fill_frac": match.limit_fill_frac,
        "max_participation": match.max_participation,
        "delist_recovery": engine.delist_recovery,
    }
    strategy = strategy_factory()
    expected.update(strategy.spec.params)
    if grid_name == "cost":
        columns = ("cost_multiplier", "impact_coef")
    elif grid_name == "matching":
        columns = ("limit_fill_frac", "max_participation")
    elif grid_name == "delist":
        columns = ("delist_recovery",)
    else:
        columns = tuple(strategy.spec.params)
    columns = tuple(dict.fromkeys((*columns, *strategy.spec.params)))
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"default grid columns missing: {sorted(missing)}")
    matches = frame
    for column in columns:
        matches = matches[matches[column] == expected[column]]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one default row for {grid_name}, found {len(matches)}")
    return int(matches.index[0])


def main():
    out = ROOT / "out" / "sensitivity"
    csv_out = ROOT / "docs" / "research" / "sensitivity"
    out.mkdir(parents=True, exist_ok=True)
    csv_out.mkdir(parents=True, exist_ok=True)
    snapshot_root = os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots")
    snapshot_id = os.environ.get("Q6_SNAPSHOT", "6252e931a86bda15")
    strategies = [("lowvol", LowVolEqualWeight), ("csmf", CSMultiFactor)]
    grids = {
        "cost": lambda base: grid(
            base, cost_multiplier=[0.5, 1, 1.5, 2, 3, 5], impact_coef=[0.05, 0.1, 0.2, 0.4]
        ),
        "matching": lambda base: grid(
            base, limit_fill_frac=[0, 0.25, 0.5], max_participation=[0.02, 0.05, 0.1, 0.2]
        ),
        "delist": lambda base: grid(base, delist_recovery=[1.0, 0.5, 0.0]),
    }
    reports = []
    for name, factory in strategies:
        for grid_name, make_cfgs in grids.items():
            cfgs = make_cfgs(EngineConfig())
            variants = [Variant(f"{grid_name}_{i}", factory, cfg) for i, cfg in enumerate(cfgs)]
            frame = run_grid(
                variants, snapshot_root, snapshot_id, START, END, workers=2, batch_size=BATCH_SIZE
            )
            frame.to_csv(csv_out / f"{name}_{grid_name}.csv", index=False)
            frame.to_csv(out / f"{name}_{grid_name}.csv", index=False)
            default_index = _default_row(frame, factory, grid_name)
            reports.append((name, grid_name, frame, default_index))
            if len(frame) > 1:
                value_col = (
                    "cost_multiplier"
                    if grid_name == "cost"
                    else ("limit_fill_frac" if grid_name == "matching" else "delist_recovery")
                )
                second = (
                    "impact_coef"
                    if grid_name == "cost"
                    else ("max_participation" if grid_name == "matching" else None)
                )
                for metric, title in (("cagr", "CAGR"), ("max_drawdown", "Maximum drawdown")):
                    fig, ax = plt.subplots()
                    if second:
                        table = frame.pivot(index=value_col, columns=second, values=metric)
                        image = ax.imshow(table.to_numpy(), aspect="auto", origin="lower")
                        ax.set_xticks(range(len(table.columns)), [str(x) for x in table.columns])
                        ax.set_yticks(range(len(table.index)), [str(x) for x in table.index])
                        ax.set_xlabel(second)
                        ax.set_ylabel(value_col)
                        fig.colorbar(image, ax=ax, label=metric)
                    else:
                        ax.bar(frame[value_col].astype(str), frame[metric])
                        ax.set_xlabel(value_col)
                    ax.set_title(title)
                    fig.tight_layout()
                    fig.savefig(out / f"{name}_{grid_name}_{metric}.png", dpi=140)
                    plt.close(fig)
        if name == "lowvol":
            variants = []
            for n, lookback in itertools.product([10, 30, 60], [20, 60, 120]):
                variants.append(
                    Variant(
                        f"params_{n}_{lookback}",
                        partial(LowVolEqualWeight, n=n, lookback=lookback),
                        EngineConfig(),
                    )
                )
            frame = run_grid(
                variants, snapshot_root, snapshot_id, START, END, workers=2, batch_size=BATCH_SIZE
            )
            frame.to_csv(csv_out / "lowvol_params.csv", index=False)
            frame.to_csv(out / "lowvol_params.csv", index=False)
            reports.append((name, "params", frame, _default_row(frame, LowVolEqualWeight, "params")))
            for metric in ("cagr", "max_drawdown"):
                table = frame.pivot(index="n", columns="lookback", values=metric)
                fig, ax = plt.subplots()
                image = ax.imshow(table.to_numpy(), aspect="auto", origin="lower")
                ax.set_xticks(range(len(table.columns)), [str(x) for x in table.columns])
                ax.set_yticks(range(len(table.index)), [str(x) for x in table.index])
                ax.set_xlabel("lookback")
                ax.set_ylabel("n")
                ax.set_title(metric)
                fig.colorbar(image, ax=ax, label=metric)
                fig.tight_layout()
                fig.savefig(out / f"lowvol_params_{metric}.png", dpi=140)
                plt.close(fig)
    for name, grid_name, frame, default_idx in reports:
        vals = frame.cagr
        order = int(vals.rank(ascending=False, method="min").iloc[default_idx])
        best_marker = (
            " **DEFAULT IS BEST IN PARAM GRID / OVERFIT SIGNAL**"
            if grid_name == "params" and order == 1
            else ""
        )
        assumption_note = ""
        if grid_name == "delist":
            recovery = frame["delist_recovery"]
            default_recovery = EngineConfig().delist_recovery
            if default_recovery == recovery.max():
                optimistic = float(frame.loc[recovery.idxmax(), "cagr"])
                conservative = float(frame.loc[recovery.idxmin(), "cagr"])
                assumption_note = (
                    " **DEFAULT ASSUMPTION IS THE MOST OPTIMISTIC IN THIS GRID**; "
                    f"recovery=1.0 CAGR over recovery=0.0 by "
                    f"{optimistic - conservative:.6f} ({(optimistic - conservative) * 100:.3f} pp)"
                )
        print(
            f"{name}/{grid_name}: CAGR min={vals.min():.6f} median={vals.median():.6f} max={vals.max():.6f}; "
            f"default={vals.iloc[default_idx]:.6f}, rank {order}/{len(vals)}{best_marker}{assumption_note}; "
            f"max child peak_rss_mb={frame.peak_rss_mb.max():.1f}"
        )
    print(f"All grids peak_rss_mb max={max(frame.peak_rss_mb.max() for _, _, frame, _ in reports):.1f}")


if __name__ == "__main__":
    main()
