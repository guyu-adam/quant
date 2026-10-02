"""Shared assertions for per-regime real-snapshot engine parity tests."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from q6.engine.event import EventEngine
from q6.engine.feed import ENGINE_FIELDS, SnapshotFeed
from q6.engine.vector import VectorEngine, strategy_weights
from q6.research.regimes import REGIMES
from q6.strategy.cs_multifactor import CSMultiFactor
from q6.strategy.examples import LowVolEqualWeight
from q6.strategy.ts_momentum import TSMomentum

SNAP = os.environ.get("Q6_SNAPSHOT")
REQUIRE = os.environ.get("Q6_REQUIRE_SNAPSHOT") == "1"
ROOT = Path(os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots"))
STRATEGIES = {
    "lowvol": LowVolEqualWeight,
    "cs_multifactor": CSMultiFactor,
    "ts_momentum": TSMomentum,
}
REGIME_BY_NAME = {r.name: r for r in REGIMES}


def compare(strategy_name: str, regime_name: str) -> None:
    if not SNAP:
        if REQUIRE:
            pytest.fail("Q6_REQUIRE_SNAPSHOT=1 但 Q6_SNAPSHOT 未设置：验收模式不允许跳过")
        pytest.skip("Q6_SNAPSHOT 未设置")
    regime = REGIME_BY_NAME[regime_name]
    event_strategy = STRATEGIES[strategy_name]()
    vector_strategy = STRATEGIES[strategy_name]()
    event_feed = SnapshotFeed(ROOT, SNAP, extra_fields=tuple(
        f for f in event_strategy.spec.fields if f not in ENGINE_FIELDS
    ))
    vector_feed = SnapshotFeed(ROOT, SNAP, extra_fields=tuple(
        f for f in vector_strategy.spec.fields if f not in ENGINE_FIELDS
    ))
    event = EventEngine().run(event_strategy, event_feed, regime.start, regime.end)
    vector = VectorEngine().run(
        strategy_weights(vector_strategy), vector_strategy.spec.warmup,
        vector_feed, regime.start, regime.end,
    )
    assert event.daily.index.equals(vector.daily.index)
    assert [(f.ts, f.symbol, f.side, f.qty, f.price) for f in event.fills] == [
        (f.ts, f.symbol, f.side, f.qty, f.price) for f in vector.fills
    ]
    assert event.reasons == vector.reasons
    assert event.delistings == vector.delistings
    e = event.daily["equity"].to_numpy(dtype=float)
    v = vector.daily["equity"].to_numpy(dtype=float)
    rel = np.abs(e - v) / np.maximum(np.abs(e), 1.0)
    assert np.isfinite(rel).all()
    assert rel.max(initial=0.0) <= 1e-12
