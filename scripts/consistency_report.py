#!/usr/bin/env python3
"""Write daily equity comparisons for the real-snapshot engine parity suite."""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from q6.engine.event import EventEngine
from q6.engine.feed import ENGINE_FIELDS, SnapshotFeed
from q6.engine.vector import VectorEngine, strategy_weights
from q6.research.regimes import REGIMES
from q6.strategy.cs_multifactor import CSMultiFactor
from q6.strategy.examples import LowVolEqualWeight
from q6.strategy.ts_momentum import TSMomentum

SNAP = os.environ.get("Q6_SNAPSHOT")
ROOT = Path(os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots"))
OUT = Path("out/consistency")
STRATEGIES = {
    "lowvol": LowVolEqualWeight,
    "cs_multifactor": CSMultiFactor,
    "ts_momentum": TSMomentum,
}
REGIME_NAMES = ("gfc_2008", "bull_2014", "crash_2015")


def main() -> None:
    if not SNAP:
        raise SystemExit("Q6_SNAPSHOT is required")
    regimes = {r.name: r for r in REGIMES}
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for strategy_name, strategy_type in STRATEGIES.items():
        for regime_name in REGIME_NAMES:
            regime = regimes[regime_name]
            s_event, s_vector = strategy_type(), strategy_type()
            extra = tuple(f for f in s_event.spec.fields if f not in ENGINE_FIELDS)
            feed_event = SnapshotFeed(ROOT, SNAP, extra_fields=extra)
            extra_v = tuple(f for f in s_vector.spec.fields if f not in ENGINE_FIELDS)
            feed_vector = SnapshotFeed(ROOT, SNAP, extra_fields=extra_v)
            started = time.perf_counter()
            event = EventEngine().run(s_event, feed_event, regime.start, regime.end)
            event_seconds = time.perf_counter() - started
            started = time.perf_counter()
            vector = VectorEngine().run(strategy_weights(s_vector), s_vector.spec.warmup,
                                        feed_vector, regime.start, regime.end)
            vector_seconds = time.perf_counter() - started
            if not event.daily.index.equals(vector.daily.index):
                raise AssertionError(f"交易日不一致: {strategy_name}/{regime_name}")
            event_eq = event.daily["equity"].to_numpy(dtype=float)
            vector_eq = vector.daily["equity"].to_numpy(dtype=float)
            rel = np.abs(event_eq - vector_eq) / np.maximum(np.abs(event_eq), 1.0)
            if not np.isfinite(rel).all() or rel.max(initial=0.0) > 1e-12:
                raise AssertionError(f"权益偏差超限: {strategy_name}/{regime_name}: {rel.max()}")
            if [(f.ts, f.symbol, f.side, f.qty, f.price) for f in event.fills] != [
                (f.ts, f.symbol, f.side, f.qty, f.price) for f in vector.fills
            ]:
                raise AssertionError(f"成交不一致: {strategy_name}/{regime_name}")
            if event.reasons != vector.reasons or event.delistings != vector.delistings:
                raise AssertionError(f"原因或退市结算不一致: {strategy_name}/{regime_name}")
            frame = pd.DataFrame({
                "date": event.daily.index,
                "equity_event": event_eq,
                "equity_vector": vector_eq,
                "rel_diff": rel,
            })
            path = OUT / f"{strategy_name}_{regime_name}.csv"
            frame.to_csv(path, index=False, date_format="%Y-%m-%d")
            rows.append({
                "strategy": strategy_name, "regime": regime_name, "days": len(frame),
                "fills": len(event.fills), "max_rel_diff": rel.max(initial=0.0),
                "event_seconds": event_seconds, "vector_seconds": vector_seconds,
            })
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:.6g}"))


if __name__ == "__main__":
    main()
