"""P2-07 验收：用事件引擎在真实快照上跑样板策略的完整回测，打印逐年收益、成本、未成交原因。

    uv run python scripts/backtest_example.py --snapshot 6252e931a86bda15 --start 2006-01-01 --end 2024-06-28

样板策略只用来证明引擎能跑通，不是研究结论。区间止于锁箱期之前。
"""

from __future__ import annotations

import argparse
import os
import time

import numpy as np

from q6.engine.event import EventEngine
from q6.engine.feed import SnapshotFeed
from q6.strategy.examples import LowVolEqualWeight


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default=os.environ.get("Q6_SNAPSHOT", "6252e931a86bda15"))
    ap.add_argument("--root", default=os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots"))
    ap.add_argument("--start", default="2006-01-01")
    ap.add_argument("--end", default="2024-06-28")
    a = ap.parse_args()

    s = LowVolEqualWeight()
    feed = SnapshotFeed(a.root, a.snapshot, extra_fields=s.spec.fields)
    t0 = time.time()
    res = EventEngine().run(s, feed, a.start, a.end)
    secs = time.time() - t0
    d, r = res.daily, res.returns
    eq = d["equity"]
    years = len(d) / 244
    cagr = (eq.iloc[-1] / res.initial_cash) ** (1 / years) - 1
    mdd = float((eq / eq.cummax() - 1).min())
    vol = float(r.std(ddof=1) * np.sqrt(244))
    print(f"strategy={s.name} params={dict(s.spec.params)} snapshot={a.snapshot}")
    print(f"days={len(d)} {d.index[0].date()}..{d.index[-1].date()}  run_seconds={secs:.1f}")
    turnover = d["buy_value"].sum() / eq.mean() / years
    sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(244))
    print(f"final_equity={eq.iloc[-1]:,.0f}  CAGR={cagr:.2%}  vol={vol:.2%}  "
          f"sharpe(rf=0)={sharpe:.2f}  maxDD={mdd:.2%}")
    print(f"fills={len(res.fills)}  fees={d['fees'].sum():,.0f}  "
          f"slip+impact={d['cost_slip_imp'].sum():,.0f}  turnover(annual, one-way)={turnover:.2f}")
    print("reasons:", dict(sorted(res.reasons.items())))
    rec = EventEngine().cfg.delist_recovery
    print(f"delistings={len(res.delistings)} (settled at last price x recovery={rec})")
    for x in res.delistings:
        print(f"  {x['date'].date()} {x['symbol']} qty={x['qty']} last={x['last_price']:.2f} "
              f"value={x['value']:,.0f} cost={x['cost']:,.0f}")
    print("year  return   maxDD")
    for y, g in eq.groupby(eq.index.year):
        prev = eq[eq.index.year < y]
        base = prev.iloc[-1] if len(prev) else res.initial_cash
        print(f"{y}  {g.iloc[-1] / base - 1:+7.2%}  {float((g / g.cummax() - 1).min()):+7.2%}")


if __name__ == "__main__":
    main()
