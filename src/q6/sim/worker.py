"""模拟盘 worker（P3-03）：一个进程跑一个回放任务（策略 × 参数 × 区间），由 supervisor 拉起。

    python -m q6.sim.worker --saves D:\\quant6\\saves --spec-json <job 的 JSON>

逐交易日推进的就是 Mac 回测用的 `EngineRun`（engine/event.py）——撮合、费用、涨跌停、停牌、T+1、参与率、
风控全部是同一份代码，Win 侧没有另写。worker 只在两天之间加四件事：
1. 限速：`seconds_per_day = N` 表示 1 个交易日对应 N 秒真实时间（N=0 尽快跑）。按"第 k 天不早于
   t0 + k·N"排程，追不上就不睡（不会越跑越慢）。
2. 检查点：每 `checkpoint_every` 个交易日、限速等待时每 `checkpoint_secs` 秒、结束时各写一次
   （sim/checkpoint.py）。
3. 心跳：`hb/<run_id>.json`，至少每秒一次（限速睡眠被切成 ≤0.5 秒的片段，睡眠中也更新）。
4. 软内存限制：RSS 超过 `soft_rss_mb`（默认 450）先 gc；仍超就写检查点、以退出码 75 退出，
   supervisor 立即重启（不计失败、不退避）——进程重开是归还碎片内存最可靠的办法。
   硬上限是 Job Object（limits_win）。

退出码：0 完成；75 软内存重启；其它 = 失败（supervisor 记录并按指数退避重启）。
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import fields, is_dataclass, replace
from pathlib import Path

import pandas as pd

from q6.engine.event import EngineConfig, EngineRun, EventEngine
from q6.engine.feed import SnapshotFeed
from q6.engine.matching import MatchConfig
from q6.risk.monitor import RiskConfig
from q6.sim import checkpoint as ckpt
from q6.sim import layout
from q6.strategy.base import Strategy

EXIT_DONE, EXIT_SOFT_MEMORY = 0, 75

# 模拟盘可用的策略（与 Mac 回测是同一批类）。LGBM 排序策略需要按 walk-forward 窗口逐段训练模型，
# 不是"一个策略对象跑全程"的形态，P3 不接入（PROGRESS 记录）。
STRATEGIES: dict[str, str] = {
    "lowvol": "q6.strategy.examples:LowVolEqualWeight",
    "csmf": "q6.strategy.cs_multifactor:CSMultiFactor",
    "tsmom": "q6.strategy.ts_momentum:TSMomentum",
    "reversal": "q6.strategy.mean_reversion:ShortReversal",
    "pairs": "q6.strategy.pairs:PairsDistance",
    "eqw": "q6.strategy.benchmarks:UniverseEqualWeight",
    "buyhold": "q6.strategy.benchmarks:BuyAndHold",
}


def _import(path: str):
    import importlib

    mod, _, name = path.partition(":")
    return getattr(importlib.import_module(mod), name)


def build_strategy(name: str, params: dict | None = None) -> Strategy:
    """`combo` 的 params：{"members": [{"strategy": "lowvol", "params": {...}, "weight": 1.0}, ...], ...}。"""
    params = dict(params or {})
    if name == "combo":
        from q6.strategy.combo import Combo

        members = [(build_strategy(m["strategy"], m.get("params")), float(m.get("weight", 1.0)))
                   for m in params.pop("members")]
        return Combo(members, **params)
    if name not in STRATEGIES:
        raise ValueError(f"未知策略 {name!r}；可用：{sorted(STRATEGIES)} + combo")
    return _import(STRATEGIES[name])(**params)


def _apply(obj, over: dict):
    """把 JSON 覆盖项落到（嵌套）frozen dataclass 上；未知字段报错，不静默忽略。"""
    known = {f.name for f in fields(obj)}
    bad = set(over) - known
    if bad:
        raise ValueError(f"{type(obj).__name__} 没有字段 {sorted(bad)}")
    kv = {}
    for k, v in over.items():
        cur = getattr(obj, k)
        if is_dataclass(cur) and isinstance(v, dict):
            kv[k] = _apply(cur, v)
        elif k == "risk" and isinstance(v, dict):
            kv[k] = _apply(RiskConfig(), v)
        elif k == "exec_style" and isinstance(v, str):
            from q6.core.types import ExecStyle

            kv[k] = ExecStyle[v]
        else:
            kv[k] = v
    return replace(obj, **kv)


def engine_config(over: dict | None) -> EngineConfig:
    return _apply(EngineConfig(match=MatchConfig()), over or {})


def _rss_mb() -> float:
    import psutil

    return psutil.Process().memory_info().rss / 2**20


class Pacer:
    """第 k 个交易日（从本次进程开始计）不早于 t0 + k·N 秒完成。clock / sleep 可注入，单测不用真等。"""

    def __init__(self, seconds_per_day: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if seconds_per_day < 0:
            raise ValueError("seconds_per_day 不能为负")
        self.n, self.clock, self.sleep = float(seconds_per_day), clock, sleep
        self.t0, self.k = clock(), 0

    def wait(self, tick: Callable[[], None] | None = None, slice_s: float = 0.5) -> float:
        self.k += 1
        slept = 0.0
        while (left := self.t0 + self.k * self.n - self.clock()) > 0:
            d = min(left, slice_s)
            self.sleep(d)
            slept += d
            if tick is not None:
                tick()
        return slept


def run_worker(spec: dict, saves: Path, *, feed=None, strategy: Strategy | None = None,
               stop_after_days: int | None = None, clock=time.monotonic, sleep=time.sleep) -> int:
    """spec 必填：run_id, strategy, start, end；snapshot_root / snapshot_id（feed 未注入时）。
    可选：params, engine（EngineConfig 覆盖项）, seconds_per_day(0), checkpoint_every(20),
    checkpoint_secs(30), soft_rss_mb(450)。
    stop_after_days 仅测试用：处理完这么多天后不写检查点直接返回（等同被杀）。"""
    run_id = spec["run_id"]
    layout.ensure(saves)
    log = layout.setup_logging(f"worker-{run_id}", saves)
    hb_path = layout.hb_dir(saves) / f"{run_id}.json"
    conn = ckpt.connect(layout.run_db(saves, run_id))
    ckpt.init_meta(conn, run_id, spec)
    if ckpt.get_meta(conn, "status") == layout.STATUS_DONE:
        log.info("already done")
        return EXIT_DONE

    strategy = strategy if strategy is not None else build_strategy(spec["strategy"], spec.get("params"))
    if feed is None:
        feed = SnapshotFeed(spec["snapshot_root"], spec["snapshot_id"], extra_fields=strategy.spec.fields)
    engine = EventEngine(engine_config(spec.get("engine")))
    run = EngineRun(engine, strategy, feed, spec["start"], spec["end"])
    state = ckpt.load(conn)
    if state is not None:
        run.load_state_dict(state)
        ckpt.event(conn, "resume", f"pid={os.getpid()} from={run.last_day.date()} n_days={run.n_days}")
        log.info("resume from %s (n_days=%d)", run.last_day.date(), run.n_days)
    else:
        ckpt.event(conn, "start", f"pid={os.getpid()}")
        log.info("start %s", json.dumps(spec, ensure_ascii=False))
    cal = getattr(feed, "calendar", None)
    total = int(((cal >= pd.Timestamp(spec["start"])) & (cal <= pd.Timestamp(spec["end"]))).sum()) \
        if cal is not None else None
    every = int(spec.get("checkpoint_every", 20))
    every_s = float(spec.get("checkpoint_secs", 30))
    soft = float(spec.get("soft_rss_mb", 450))
    pacer = Pacer(float(spec.get("seconds_per_day", 0)), clock, sleep)
    last_hb = [0.0]
    last_ck = [clock(), run.n_days]

    def beat(status: str = layout.STATUS_RUNNING, force: bool = False) -> None:
        now = time.time()
        if not force and now - last_hb[0] < 0.5:
            return
        last_hb[0] = now
        eq = run.broker.state.equity if run.broker is not None else None
        layout.write_json_atomic(hb_path, dict(
            run_id=run_id, pid=os.getpid(), ts=now, status=status, n_days=run.n_days, total_days=total,
            day=None if run.last_day is None else str(run.last_day.date()), equity=eq,
            rss_mb=round(_rss_mb(), 1)))

    def save() -> None:
        ckpt.save(conn, run)
        last_ck[:] = [clock(), run.n_days]

    def tick() -> None:  # 限速睡眠中：心跳 + 按时间写检查点（慢速回放时被杀最多丢 checkpoint_secs 的进度）
        beat()
        if run.n_days > last_ck[1] and clock() - last_ck[0] >= every_s:
            save()

    beat(force=True)
    done_here = 0
    for _day in run.days():
        done_here += 1
        if stop_after_days is not None and done_here >= stop_after_days:
            conn.close()
            return -1
        if run.n_days % every == 0:
            save()
        pacer.wait(tick)
        beat()
        if run.n_days % 50 == 0 and _rss_mb() > soft:
            gc.collect()
            rss = _rss_mb()
            if rss > soft:
                save()
                ckpt.event(conn, "soft_memory", f"rss={rss:.0f}MB > {soft:.0f}MB，写检查点后重启")
                log.warning("soft memory limit: rss=%.0fMB, checkpoint and exit %d", rss, EXIT_SOFT_MEMORY)
                beat("restarting", force=True)
                conn.close()
                return EXIT_SOFT_MEMORY
    save()
    ckpt.set_meta(conn, status=layout.STATUS_DONE, finished=time.strftime("%Y-%m-%dT%H:%M:%S"))
    ckpt.event(conn, "done", f"n_days={run.n_days} reasons={json.dumps(run.reasons, sort_keys=True)}")
    beat(layout.STATUS_DONE, force=True)
    log.info("done n_days=%d", run.n_days)
    conn.close()
    return EXIT_DONE


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--saves", required=True)
    ap.add_argument("--spec-json", required=True, help="job 的 JSON 字符串，或 @文件路径")
    a = ap.parse_args(argv)
    raw = a.spec_json
    spec = json.loads(Path(raw[1:]).read_text(encoding="utf-8") if raw.startswith("@") else raw)
    try:
        return run_worker(spec, Path(a.saves))
    except Exception:
        log = layout.setup_logging(f"worker-{spec.get('run_id', 'unknown')}", Path(a.saves))
        log.exception("worker crashed")
        raise


if __name__ == "__main__":
    sys.exit(main())
