"""P2-08 测速 + 真实快照一致性：样板策略全区间，事件引擎 1 次 vs 向量化引擎 1 次 vs run_many 8 组成本配置。

    /usr/bin/time -l uv run python scripts/bench_vector.py
"""

from __future__ import annotations

import os
import time

import numpy as np

from q6.engine.event import EngineConfig, EventEngine
from q6.engine.feed import SnapshotFeed
from q6.engine.matching import MatchConfig
from q6.engine.vector import VectorEngine, strategy_weights
from q6.strategy.examples import LowVolEqualWeight

ROOT = os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots")
SNAP = os.environ.get("Q6_SNAPSHOT", "6252e931a86bda15")
START, END = "2006-01-01", "2024-06-28"


def feed(s):
    return SnapshotFeed(ROOT, SNAP, extra_fields=s.spec.fields)


def main() -> None:
    s = LowVolEqualWeight()
    t = time.perf_counter()
    ev = EventEngine().run(s, feed(s), START, END)
    t_ev = time.perf_counter() - t

    s = LowVolEqualWeight()
    t = time.perf_counter()
    ve = VectorEngine().run(strategy_weights(s), s.spec.warmup, feed(s), START, END)
    t_ve = time.perf_counter() - t

    a, b = ev.daily["equity"].to_numpy(), ve.daily["equity"].to_numpy()
    print(f"days={len(a)} event_fills={len(ev.fills)} vector_fills={len(ve.fills)} "
          f"reasons_equal={ev.reasons == ve.reasons} delistings={len(ev.delistings)}/{len(ve.delistings)}")
    print(f"equity max|rel diff|={np.max(np.abs(a / b - 1)):.3e} identical={np.array_equal(a, b)}")
    print(f"event_seconds={t_ev:.1f} vector_seconds={t_ve:.1f}")

    mults = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)
    jobs = []
    for m in mults:
        s = LowVolEqualWeight()
        jobs.append((strategy_weights(s), s.spec.warmup, EngineConfig(match=MatchConfig(cost_multiplier=m))))
    t = time.perf_counter()
    res = VectorEngine().run_many(jobs, feed(LowVolEqualWeight()), START, END)
    t_many = time.perf_counter() - t
    print(f"run_many configs={len(mults)} seconds={t_many:.1f} per_config={t_many / len(mults):.2f} "
          f"(vs event {t_ev:.1f} -> x{t_ev / (t_many / len(mults)):.1f})")
    for m, r in zip(mults, res, strict=True):
        eq = r.daily["equity"]
        years = len(eq) / 244  # 同 backtest_example.py（A 股每年约 244 个交易日）
        cagr = (eq.iloc[-1] / r.initial_cash) ** (1 / years) - 1
        print(f"  cost x{m:<3} final_equity={eq.iloc[-1]:>12,.0f} CAGR={cagr:+.2%}")
    same = np.array_equal(res[1].daily["equity"].to_numpy(), b)
    assert same, "run_many 里 cost x1.0 必须与单独运行逐位相同"


if __name__ == "__main__":
    main()
