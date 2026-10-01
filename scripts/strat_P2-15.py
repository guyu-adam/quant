"""真实快照短跑：P2-15 时序动量。"""

from __future__ import annotations

import os

from q6.engine.event import EventEngine
from q6.engine.feed import ENGINE_FIELDS, SnapshotFeed
from q6.strategy.ts_momentum import TSMomentum

ROOT = os.environ.get("Q6_SNAPSHOT_ROOT", "/Users/guyu/Desktop/guyu-adam/quant/data/snapshots")
SNAPSHOT = os.environ.get("Q6_SNAPSHOT", "6252e931a86bda15")


def main() -> None:
    strategy = TSMomentum()
    extra = tuple(field for field in strategy.spec.fields if field not in ENGINE_FIELDS)
    feed = SnapshotFeed(ROOT, SNAPSHOT, extra_fields=extra)
    result = EventEngine().run(strategy, feed, "2015-01-01", "2015-12-31")
    equity = result.daily["equity"]
    annualized = (equity.iloc[-1] / equity.iloc[0]) ** (252 / max(1, len(equity) - 1)) - 1
    drawdown = equity / equity.cummax() - 1
    print(f"annualized_return={annualized:.6f}")
    print(f"max_drawdown={drawdown.min():.6f}")
    print(f"trades={len(result.fills)}")
    print(f"reasons={dict(result.reasons)}")


if __name__ == "__main__":
    main()
