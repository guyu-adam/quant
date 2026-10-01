"""Run P2-17 strategies on the pinned real snapshot for an evidence short run."""

from __future__ import annotations

import os

from q6.engine.event import EventEngine
from q6.engine.feed import SnapshotFeed
from q6.research.metrics import ann_return, max_drawdown
from q6.strategy.mean_reversion import ShortReversal
from q6.strategy.pairs import PairsDistance


def main() -> None:
    root = os.environ.get("Q6_SNAPSHOT_ROOT", "/Users/guyu/Desktop/guyu-adam/quant/data/snapshots")
    snapshot_id = os.environ.get("Q6_SNAPSHOT", "6252e931a86bda15")
    for strategy in (ShortReversal(), PairsDistance()):
        feed = SnapshotFeed(root, snapshot_id, extra_fields=strategy.spec.fields)
        result = EventEngine().run(strategy, feed, "2015-01-01", "2016-12-31")
        returns = result.returns
        print(f"{strategy.name}: annualized_return={ann_return(returns):.6f} "
              f"max_drawdown={max_drawdown(returns):.6f} trades={len(result.fills)} reasons={result.reasons}")


if __name__ == "__main__":
    main()
