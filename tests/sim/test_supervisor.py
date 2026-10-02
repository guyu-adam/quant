"""P3-04 / P3-05：supervisor 的拉起、重启、指数退避、心跳超时、并发上限、loop；Job Object 内存硬限。"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from q6.sim import layout, limits_win
from q6.sim.supervisor import Supervisor, load_config

FAKE = Path(__file__).with_name("fake_worker.py")


def _config(tmp_path, jobs: dict[str, dict], **sim) -> Path:
    base = dict(snapshot_id="x", max_workers=4, backoff_base=0.2, backoff_max=1.0, heartbeat_timeout=2.0,
                startup_grace=2.0, web=False, loop=False, janitor_every=1e9, status_every=0.0)
    base.update(sim)
    lines = ["[sim]"] + [f"{k} = {json.dumps(v)}" for k, v in base.items()]
    for name, params in jobs.items():
        lines += ["[[jobs]]", f'name = "{name}"', 'strategy = "lowvol"', 'start = "2020-01-01"',
                  'end = "2020-12-31"', "params = { " + ", ".join(f"{k} = {json.dumps(v)}"
                                                                for k, v in params.items()) + " }"]
    p = tmp_path / "sim.toml"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _sup(tmp_path, jobs, **sim) -> Supervisor:
    cfg = _config(tmp_path, jobs, **sim)

    def argv(slot):
        p = tmp_path / f"{slot.run_id}.spec.json"
        p.write_text(json.dumps(slot.spec), encoding="utf-8")
        return [sys.executable, str(FAKE), "--saves", str(layout.saves(tmp_path)), "--spec-json", f"@{p}"]

    return Supervisor(tmp_path, cfg, worker_argv=argv)


def _run_until(sup, cond, timeout=30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        sup.tick()
        if cond():
            return time.time() - t0
        time.sleep(0.05)
    sup.shutdown()
    raise AssertionError(f"timeout; states={ {k: s.state for k, s in sup.slots.items()} }")


def test_done_crash_backoff_soft_and_failed(tmp_path):
    jobs = {"ok": dict(mode="ok"), "c1": dict(mode="crash_once"), "soft": dict(mode="soft_once"),
            "bad": dict(mode="crash")}
    sup = _sup(tmp_path, jobs, max_failures=3)
    _run_until(sup, lambda: all(s.state in ("done", "failed") for s in sup.slots.values()))
    st = {k.split("-g")[0]: s for k, s in sup.slots.items()}
    assert st["ok"].state == "done" and st["ok"].restarts == 0
    assert st["c1"].state == "done" and st["c1"].failures == 1
    assert st["soft"].state == "done" and st["soft"].failures == 0 and st["soft"].restarts == 1
    assert st["bad"].state == "failed" and st["bad"].failures == 3 and st["bad"].last_exit == 3
    reasons = [(r["run_id"].split("-g")[0], r["reason"]) for r in sup.restarts_log]
    assert ("soft", "soft_memory") in reasons and ("c1", "exit") in reasons
    sup.write_status()
    s = layout.read_json(layout.saves(tmp_path) / "supervisor.json")
    assert s["pid"] and any(w["state"] == "failed" for w in s["workers"]) and len(s["done"]) == 3


def test_backoff_is_exponential(tmp_path):
    sup = _sup(tmp_path, {"bad": dict(mode="crash")}, max_failures=4, backoff_base=0.2, backoff_max=10.0)
    starts = []
    orig = sup._start

    def rec(slot, now):
        starts.append(now)
        orig(slot, now)

    sup._start = rec
    _run_until(sup, lambda: all(s.state == "failed" for s in sup.slots.values()))
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    assert len(starts) == 4
    assert gaps[0] >= 0.2 and gaps[1] >= 0.4 and gaps[2] >= 0.8  # 0.2 · 2^(k-1)


def test_heartbeat_timeout_kills_and_restarts(tmp_path):
    sup = _sup(tmp_path, {"hang": dict(mode="hang"), "stall": dict(mode="stall")}, max_failures=2,
               heartbeat_timeout=1.0, startup_grace=1.5)
    _run_until(sup, lambda: all(s.state == "failed" for s in sup.slots.values()))
    assert {r["reason"] for r in sup.restarts_log} == {"heartbeat"}


def test_max_workers_respected(tmp_path):
    sup = _sup(tmp_path, {f"j{i}": dict(mode="stall") for i in range(5)}, max_workers=2, heartbeat_timeout=60)
    for _ in range(5):
        sup.tick()
        time.sleep(0.05)
    assert sum(s.state == "running" for s in sup.slots.values()) == 2
    sup.shutdown()


def test_backoff_keeps_its_slot(tmp_path):
    """被杀 / 崩溃的任务退避期间不能被排队任务抢走槽位（Win 24h 实测：抢走后要等别人跑完才续跑）。"""
    sup = _sup(tmp_path, {"c1": dict(mode="crash_once"), "q": dict(mode="stall")}, max_workers=1,
               heartbeat_timeout=60)
    _run_until(sup, lambda: sup.slots["c1-g1"].state == "done", timeout=10.0)
    assert sup.slots["c1-g1"].failures == 1
    sup.shutdown()


def test_loop_starts_next_generation(tmp_path):
    sup = _sup(tmp_path, {"ok": dict(mode="ok")}, loop=True)
    _run_until(sup, lambda: sup.gen == 3)
    assert list(sup.slots) == ["ok-g3"]
    assert layout.read_json(layout.saves(tmp_path) / "supervisor-state.json")["gen"] == 3
    sup.shutdown()


def test_config_grid_expansion_and_validation(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(textwrap.dedent('''
        [sim]
        snapshot_id = "abc"
        [[jobs]]
        name = "lv"
        strategy = "lowvol"
        start = "2010-01-01"
        end = "2011-01-01"
        grid = { n = [10, 30], lookback = [20, 60, 120] }
    '''), encoding="utf-8")
    sim, jobs = load_config(p)
    assert len(jobs) == 6 and jobs[0]["name"] == "lv-lookback20-n10"
    assert jobs[0]["params"] == {"lookback": 20, "n": 10}
    assert sim["max_workers"] == 12 and Path(sim["snapshot_root"]).is_absolute()
    p.write_text('[sim]\nsnapshot_id = "a"\nbogus = 1\n', encoding="utf-8")
    with pytest.raises(ValueError, match="未知配置"):
        load_config(p)


def test_single_instance_lock(tmp_path):
    a = layout.SingleInstance(tmp_path / "x.lock")
    b = layout.SingleInstance(tmp_path / "x.lock")
    assert a.ok and not b.ok
    a.release()
    c = layout.SingleInstance(tmp_path / "x.lock")
    assert c.ok
    c.release()


_ALLOC = textwrap.dedent('''
    import subprocess, sys
    from q6.sim import limits_win
    h = limits_win.confine_self(512)
    def alloc(mb):
        try:
            b = bytearray(mb * 2**20); b[::4096] = b"x" * len(b[::4096]); return "ok"
        except MemoryError:
            return "MemoryError"
    if h is None:  # 没有硬限的平台不去真分配 600MB（会被一键验证的 512MB RSS 守卫判失败）
        print(False, "-", "-", "-", None)
    else:
        small, big = alloc(300), alloc(600)
        child = subprocess.run([sys.executable, "-c",
            "b = bytearray(600 * 2**20); b[::4096] = b'x' * len(b[::4096])"], capture_output=True)
        print(True, small, big, child.returncode, limits_win.job_limits())
''')


def test_job_object_hard_limit(tmp_path):
    """Windows：自己进 Job 后 300MB 能分配、600MB 被系统拒绝；子进程从出生起继承同一限制。
    其它平台：confine_self 返回 None，什么都不限（Mac 不是部署目标）。"""
    out = subprocess.run([sys.executable, "-c", _ALLOC], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    confined, small, big, child_rc, info = out.stdout.strip().split(" ", 4)
    if sys.platform == "win32":
        assert confined == "True" and small == "ok" and big == "MemoryError"
        assert child_rc != "0"  # 子进程分配 600MB 失败退出
        assert "'process_limit_mb': 512.0" in info
    else:
        assert confined == "False" and info == "None"
        assert limits_win.job_limits() is None


def test_transient_heartbeat_read_failure_does_not_kill(tmp_path, monkeypatch):
    """Win 24h 实跑发现：读心跳赶上 worker 原子替换时读失败，被当成"从没心跳"，过了宽限期就误杀。"""
    sup = _sup(tmp_path, {"s": dict(mode="stall")}, heartbeat_timeout=60.0, startup_grace=0.5)
    _run_until(sup, lambda: any(s.hb for s in sup.slots.values()))
    real = layout.read_json
    monkeypatch.setattr(layout, "read_json",
                        lambda p, default=None: default if p.name.endswith("-g1.json") else real(p, default))
    time.sleep(0.8)  # 超过启动宽限期
    for _ in range(5):
        sup.tick()
    assert [s.state for s in sup.slots.values()] == ["running"] and not sup.restarts_log
    sup.shutdown()
