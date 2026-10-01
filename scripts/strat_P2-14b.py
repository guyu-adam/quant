"""真实快照短跑：Combo 组合 LowVolEqualWeight 与 TSMomentum。"""

from __future__ import annotations

import os

from q6.engine.event import EventEngine
from q6.engine.feed import ENGINE_FIELDS, SnapshotFeed
from q6.strategy.combo import Combo
from q6.strategy.examples import LowVolEqualWeight
from q6.strategy.ts_momentum import TSMomentum


def main() -> None:
    root = os.environ.get("Q6_SNAPSHOT_ROOT", "/Users/guyu/Desktop/guyu-adam/quant/data/snapshots")
    snapshot_id = os.environ.get("Q6_SNAPSHOT", "6252e931a86bda15")
    strategy = Combo(
        [(LowVolEqualWeight(n=10, lookback=60, every=20), 1), (TSMomentum(), 1)],
    )
    extra = tuple(field for field in strategy.spec.fields if field not in ENGINE_FIELDS)
    feed = SnapshotFeed(root, snapshot_id, extra_fields=extra)
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
