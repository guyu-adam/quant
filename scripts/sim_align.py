"""P3：同一份策略代码在 Mac 回测与 Win 模拟盘下的结果对齐（"能测量化代码"的硬证据）。

    # 1) Mac：用 P2 的回测入口 EventEngine.run 跑配置里的每个任务（不限速），存参照
    uv run python scripts/sim_align.py ref --config config/sim/default.toml --out out/sim_align/ref -j 3
    # 2) 把 Win 模拟盘跑完的库拷回来（scp win:D:/quant6/saves/runs/*-g1.sqlite out/sim_align/win/）
    # 3) 逐任务比对
    uv run python scripts/sim_align.py compare --config config/sim/default.toml --ref out/sim_align/ref \
        --runs out/sim_align/win --gen 1

compare 对每个任务输出：交易日数、日记录 9 列逐位相等与否、权益最大绝对 / 相对差、首个不一致日期、
成交笔数与成交明细（代码 / 方向 / 数量 / 价格 / 费用）逐位相等与否。没有对应库的任务标 MISSING，不跳过。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import pandas as pd

from q6.data import lockbox
from q6.sim.supervisor import load_config

DAILY = ("equity", "cash", "market_value", "n_pos", "buy_value", "sell_value", "fees", "cost_slip_imp")
FILLS = ("ts", "order_id", "symbol", "side", "qty", "price", "commission", "stamp_tax", "transfer_fee",
         "other_fees", "slippage_cost", "impact_cost")


def _ref_one(job: dict, sim: dict, out: str) -> dict:
    import psutil

    from q6.engine.event import EventEngine
    from q6.engine.feed import SnapshotFeed
    from q6.sim.worker import Min5, build_strategy, engine_config

    t0 = time.time()
    s = build_strategy(job["strategy"], job["params"])
    feed = SnapshotFeed(sim["snapshot_root"], sim["snapshot_id"], extra_fields=s.spec.fields)
    intraday = Min5(job["min5_root"]) if job.get("min5_root") else None  # 5 分钟执行模式（P3-13）
    res = EventEngine(engine_config(job["engine"])).run(s, feed, job["start"], job["end"], intraday=intraday)
    d = res.daily.reset_index()
    d["date"] = d["date"].dt.strftime("%Y-%m-%d")
    d.to_parquet(Path(out) / f"{job['name']}.daily.parquet")
    f = pd.DataFrame([(x.ts.isoformat(), x.order_id, x.symbol, x.side.name, x.qty, x.price, x.commission,
                       x.stamp_tax, x.transfer_fee, x.other_fees, x.slippage_cost, x.impact_cost)
                      for x in res.fills], columns=list(FILLS))
    f.to_parquet(Path(out) / f"{job['name']}.fills.parquet")
    mi = psutil.Process().memory_info()
    if sys.platform == "darwin":
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20  # macOS 单位是字节
    else:
        peak = getattr(mi, "peak_wset", mi.rss) / 2**20
    return dict(name=job["name"], days=len(d), fills=len(f), final_equity=float(d["equity"].iloc[-1]),
                seconds=round(time.time() - t0, 1), peak_mb=round(peak, 1),
                min5_missing=res.reasons.get("MIN5_MISSING", 0))


def cmd_ref(a) -> None:
    sim, jobs = load_config(Path(a.config))
    jobs = [j for j in jobs if not a.only or any(j["name"].startswith(p) for p in a.only)]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MallocMediumZone", "0")
    rows = []
    with ProcessPoolExecutor(a.j, mp_context=get_context("spawn"), max_tasks_per_child=1) as ex:
        for r in ex.map(_ref_one, jobs, [sim] * len(jobs), [str(out)] * len(jobs)):
            print(json.dumps(r, ensure_ascii=False), flush=True)
            rows.append(r)
    pd.DataFrame(rows).to_csv(out / "_ref_summary.csv", index=False)


def _first_diff(a: pd.DataFrame, b: pd.DataFrame) -> str:
    for i in range(min(len(a), len(b))):
        if not a.iloc[i].equals(b.iloc[i]):
            return str(a.index[i]) if not isinstance(a.index, pd.RangeIndex) else f"row {i}"
    return "" if len(a) == len(b) else f"row {min(len(a), len(b))}"


def cmd_compare(a) -> int:
    sim, jobs = load_config(Path(a.config))
    jobs = [j for j in jobs if not a.only or j["name"] in a.only]
    ref, runs = Path(a.ref), Path(a.runs)
    rows, bad = [], 0
    for j in jobs:
        name = j["name"]
        db = runs / f"{name}-g{a.gen}.sqlite"
        rd = ref / f"{name}.daily.parquet"
        if not db.exists() or not rd.exists():
            rows.append(dict(job=name, status="MISSING", note=f"db={db.exists()} ref={rd.exists()}"))
            bad += 1
            continue
        with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as c:
            status = c.execute("SELECT value FROM meta WHERE key='status'").fetchone()[0]
            wd = pd.read_sql_query(f"SELECT date, {', '.join(DAILY)} FROM daily ORDER BY date", c)
            wf = pd.read_sql_query(f"SELECT {', '.join(FILLS)} FROM fills ORDER BY seq", c)
            resumes = c.execute("SELECT COUNT(*) FROM events WHERE kind='resume'").fetchone()[0]
        md = pd.read_parquet(rd)[["date", *DAILY]]
        lockbox.check_dates(md["date"], "sim_align ref")
        mf = pd.read_parquet(ref / f"{name}.fills.parquet")[list(FILLS)]
        wd, md = wd.set_index("date").astype(float), md.set_index("date").astype(float)
        same_days = list(wd.index) == list(md.index)
        daily_eq = same_days and np.array_equal(wd.to_numpy(), md.to_numpy())
        fills_eq = len(wf) == len(mf) and wf.equals(mf.astype(wf.dtypes.to_dict()))
        common = wd.index.intersection(md.index)
        diff = (wd.loc[common, "equity"] - md.loc[common, "equity"]).abs()
        rel = diff / md.loc[common, "equity"]
        ok = status == "done" and daily_eq and fills_eq
        bad += not ok
        rows.append(dict(
            job=name, status=status, days_win=len(wd), days_mac=len(md), daily_bitwise=daily_eq,
            fills_win=len(wf), fills_mac=len(mf), fills_bitwise=fills_eq,
            max_abs_equity_diff=float(diff.max()) if len(diff) else np.nan,
            max_rel_equity_diff=float(rel.max()) if len(rel) else np.nan,
            final_equity_win=float(wd["equity"].iloc[-1]) if len(wd) else np.nan,
            final_equity_mac=float(md["equity"].iloc[-1]) if len(md) else np.nan,
            first_diff="" if daily_eq else _first_diff(wd, md), resumes=resumes,
            verdict="BITWISE" if ok else "DIFF"))
    df = pd.DataFrame(rows)
    with pd.option_context("display.width", 250, "display.max_columns", 30, "display.max_rows", 100):
        print(df.to_string(index=False))
    n_ok = int((df.get("verdict") == "BITWISE").sum()) if "verdict" in df else 0
    print(f"\nALIGN: {n_ok}/{len(jobs)} jobs bitwise identical (Mac EventEngine.run vs Win sim worker)")
    if a.out:
        df.to_csv(a.out, index=False)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("ref")
    r.add_argument("--config", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("-j", type=int, default=3)
    r.add_argument("--only", nargs="*")
    c = sub.add_parser("compare")
    c.add_argument("--config", required=True)
    c.add_argument("--ref", required=True)
    c.add_argument("--runs", required=True)
    c.add_argument("--gen", type=int, default=1)
    c.add_argument("--out")
    c.add_argument("--only", nargs="*", help="只比对这些任务名")
    a = ap.parse_args()
    if a.cmd == "ref":
        cmd_ref(a)
        return 0
    return cmd_compare(a)


if __name__ == "__main__":
    sys.exit(main())
