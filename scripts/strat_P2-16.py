from __future__ import annotations

import os

import numpy as np

from q6.engine.event import EventEngine
from q6.engine.feed import ENGINE_FIELDS, SnapshotFeed
from q6.strategy.cs_multifactor import CSMultiFactor

ROOT = os.environ.get("Q6_SNAPSHOT_ROOT", "/Users/guyu/Desktop/guyu-adam/quant/data/snapshots")
SNAPSHOT_ID = os.environ.get("Q6_SNAPSHOT", "6252e931a86bda15")


def main() -> None:
    strategy = CSMultiFactor()
    extra = tuple(field for field in strategy.spec.fields if field not in ENGINE_FIELDS)
    feed = SnapshotFeed(ROOT, SNAPSHOT_ID, extra_fields=extra)
    result = EventEngine().run(strategy, feed, "2015-01-01", "2015-12-31")
    equity = result.daily["equity"].to_numpy(dtype=float)
    total_years = len(equity) / 252
    annualized = (equity[-1] / result.initial_cash) ** (1 / total_years) - 1 if total_years else np.nan
    peak = np.maximum.accumulate(np.concatenate(([result.initial_cash], equity)))
    drawdown = np.concatenate(([result.initial_cash], equity)) / peak - 1
    print(f"annualized_return={annualized:.6f}")
    print(f"max_drawdown={drawdown.min():.6f}")
    print(f"fills={len(result.fills)}")
    print(f"reasons={result.reasons}")
    print(f"equity_finite={bool(np.isfinite(equity).all())}")


if __name__ == "__main__":
    main()
