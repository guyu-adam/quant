"""P3-05 端到端（真快照）：真 supervisor CLI + 真 worker。
运行中 kill worker 两次、kill supervisor 一次再重新拉起，要求 30 秒内恢复，
最终库里的日记录 / 成交与 Mac 回测入口 EventEngine.run 逐位一致。"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psutil
import pytest

from q6.engine.event import EventEngine
from q6.engine.feed import SnapshotFeed
from q6.sim import checkpoint as ckpt
from q6.sim import layout
from q6.strategy.examples import LowVolEqualWeight

SNAP = os.environ.get("Q6_SNAPSHOT")
REQUIRE = os.environ.get("Q6_REQUIRE_SNAPSHOT") == "1"
ROOT = Path(os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots")).resolve()
START, END = "2015-01-01", "2015-12-31"
RID = "lv-g1"


def _need_snapshot():
    if not SNAP:
        if REQUIRE:
            pytest.fail("Q6_REQUIRE_SNAPSHOT=1 但 Q6_SNAPSHOT 未设置：验收模式不允许跳过")
        pytest.skip("Q6_SNAPSHOT 未设置")


def _hb(saves):
    return layout.read_json(layout.hb_dir(saves) / f"{RID}.json", {}) or {}


def _wait(cond, timeout, what):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return time.time() - t0, v
        time.sleep(0.2)
    raise AssertionError(f"等待超时：{what}")


def _start_sup(home, cfg):
    return subprocess.Popen([sys.executable, "-m", "q6.sim.supervisor", "--home", str(home),
                             "--config", str(cfg), "--run-for", "600"])


def test_kill_worker_twice_and_supervisor_once_then_bitwise_equal(tmp_path):
    _need_snapshot()
    cfg = tmp_path / "sim.toml"
    cfg.write_text(f'''[sim]
snapshot_root = {json.dumps(str(ROOT))}
snapshot_id = {json.dumps(SNAP)}
max_workers = 1
seconds_per_day = 0.06
checkpoint_every = 10
backoff_base = 2.0
web = false
loop = false
[[jobs]]
name = "lv"
strategy = "lowvol"
start = "{START}"
end = "{END}"
''', encoding="utf-8")
    saves = layout.saves(tmp_path)
    sup = _start_sup(tmp_path, cfg)
    recover = []
    try:
        for target in (40, 90):  # 两次 kill worker
            _, hb = _wait(lambda t=target: (h := _hb(saves)).get("n_days", 0) >= t and h, 120,
                          f"n_days≥{target}")
            pid = hb["pid"]
            psutil.Process(pid).kill()
            t_kill = time.time()
            _wait(lambda p=pid: (h := _hb(saves)).get("pid") not in (None, p) and h.get("n_days", 0) > 0, 30,
                  "worker 被 kill 后 30 秒内恢复")
            recover.append(time.time() - t_kill)
        _wait(lambda: _hb(saves).get("n_days", 0) >= 140, 120, "n_days≥140")
        sup.kill()  # supervisor 自己被杀（Windows：Job 关闭，worker 随之被系统结束）
        sup.wait(10)
        if sys.platform == "win32":
            pid = _hb(saves)["pid"]
            _wait(lambda: not psutil.pid_exists(pid), 15, "supervisor 死后 worker 被 Job 一并结束")
        sup = _start_sup(tmp_path, cfg)  # 计划任务重新拉起
        _wait(lambda: _hb(saves).get("status") == "done", 300, "完成")
    finally:
        sup.kill()
        sup.wait(10)
    print(f"recover_seconds={[round(x, 1) for x in recover]}")
    assert all(x < 30 for x in recover)

    s = LowVolEqualWeight()
    ref = EventEngine().run(s, SnapshotFeed(ROOT, SNAP, extra_fields=s.spec.fields), START, END)
    db = layout.run_db(saves, RID)
    got = ckpt.read_daily(db)
    exp = ref.daily
    assert list(got.index) == list(pd.DatetimeIndex(exp.index))
    for c in exp.columns:
        np.testing.assert_array_equal(got[c].to_numpy(float), exp[c].to_numpy(float), err_msg=c)
    with sqlite3.connect(db) as c:
        rows = c.execute("SELECT order_id, symbol, side, qty, price, commission, stamp_tax FROM fills"
                         " ORDER BY seq").fetchall()
        kinds = [k for (k,) in c.execute("SELECT kind FROM events ORDER BY seq")]
    assert rows == [(f.order_id, f.symbol, f.side.name, f.qty, f.price, f.commission, f.stamp_tax)
                    for f in ref.fills]
    # Windows：supervisor 死时 Job 连带结束 worker，重新拉起后第 3 次续跑；Mac 没有 Job，孤儿 worker 自己跑完
    # （新 supervisor 拉起的 worker 拿不到该 run 的锁，退出码 77 退避），所以只有 2 次续跑。
    assert kinds.count("resume") >= (3 if sys.platform == "win32" else 2) and kinds[-1] == "done"
