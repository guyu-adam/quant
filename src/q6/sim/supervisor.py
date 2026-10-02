"""模拟盘 supervisor（P3-05）：任务队列、并发数、拉起 / 重启 / 指数退避、心跳超时判定、janitor、面板。

    python -m q6.sim.supervisor --home D:\\quant6 --config D:\\quant6\\repo\\config\\sim\\default.toml

- 单实例：saves/supervisor.lock 上的独占文件锁。计划任务每 5 分钟补拉一次，已在跑就立刻退出（退出码 0）。
- Windows 上先 `limits_win.confine_self(mem_limit_mb)`：自己和全部子进程都受 Job Object 每进程 512MB 硬限。
- 子进程：`CREATE_NO_WINDOW`（不在老板桌面上弹黑框）、低于正常优先级、BLAS / numba 单线程。
  supervisor 自己也在导入 numpy 之前设单线程：Job 里 OpenBLAS 按 20 核预分配线程缓冲区会超出提交限制，
  而且表现是 numpy 导入时**卡死**（blas_fpe_check）而不是报错——Win 实测。
- Windows 的 venv `python.exe` 是转发器，真正的解释器是它的子进程。所以一个 worker = 一棵进程树：
  心跳的 pid 属于树里任一进程即算本 worker，内存取树里各进程的最大值，杀的时候整棵树一起杀。
- worker 退出：0 → 完成；75 → 软内存重启（立即、不计失败）；其它 → 失败，`backoff_base · 2^(k-1)` 秒后重启
  （封顶 backoff_max，退避期间槽位保留给它）；
  连续失败 max_failures 次 → 标记 failed 不再拉起（supervisor.json 可见）。
- 心跳：hb 文件超过 heartbeat_timeout 秒没更新（或启动后 startup_grace 秒还没有心跳）→ 杀掉，按失败处理。
- loop=true：一轮任务全部完成后开新一轮（run_id 后缀 -g<N>），24 小时不停；上一轮的库交给 janitor 归档。
- 每 2 秒原子写 supervisor.json（格式见 Bob 卡 P3-COMMON / web 面板读它）。
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from q6.sim import layout, limits_win

EXIT_DONE, EXIT_SOFT_MEMORY, EXIT_HARD_MEMORY = 0, 75, 76
CREATE_NO_WINDOW = 0x08000000
_CHILD_ENV = dict(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMBA_NUM_THREADS="1",
                  PYTHONUTF8="1", MallocMediumZone="0")

DEFAULTS = dict(max_workers=12, seconds_per_day=1.0, checkpoint_every=20, checkpoint_secs=30, soft_rss_mb=450,
                heartbeat_timeout=120.0, startup_grace=180.0, backoff_base=5.0, backoff_max=600.0,
                max_failures=10, loop=True, mem_limit_mb=512, janitor_every=600.0, status_every=2.0,
                web=True, web_host="0.0.0.0", web_port=8790)


def _iso(t: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if t is None else t).isoformat(timespec="seconds")


def _abs(config: Path, p: str) -> str:
    q = Path(p)
    return str(q if q.is_absolute() else (config.parent / q).resolve())


def load_config(path: Path) -> tuple[dict, list[dict]]:
    """TOML：[sim] 全局设置；[[jobs]] 每项一个任务模板，`grid` 表里的列表做笛卡尔积展开成多个任务。"""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    sim = {**DEFAULTS, **raw.get("sim", {})}
    unknown = set(raw.get("sim", {})) - set(DEFAULTS) - {"snapshot_root", "snapshot_id"}
    if unknown:
        raise ValueError(f"[sim] 未知配置 {sorted(unknown)}")
    root = Path(sim.get("snapshot_root", "data/snapshots"))
    sim["snapshot_root"] = str(root if root.is_absolute() else (path.parent / root).resolve())
    jobs: list[dict] = []
    for t in raw.get("jobs", []):
        grid = t.get("grid", {})
        keys = sorted(grid)
        for combo in itertools.product(*(grid[k] for k in keys)) if keys else [()]:
            params = {**t.get("params", {}), **dict(zip(keys, combo, strict=True))}
            suffix = "".join(f"-{k}{v}" for k, v in zip(keys, combo, strict=True))
            jobs.append(dict(
                name=f"{t['name']}{suffix}", strategy=t["strategy"], params=params,
                start=t["start"], end=t["end"], engine=t.get("engine", {}),
                seconds_per_day=t.get("seconds_per_day", sim["seconds_per_day"]),
                min5_root=_abs(path, t["min5_root"]) if t.get("min5_root") else None))
    names = [j["name"] for j in jobs]
    if len(set(names)) != len(names):
        raise ValueError("任务名重复")
    return sim, jobs


@dataclass
class Slot:
    run_id: str
    spec: dict
    proc: subprocess.Popen | None = None
    state: str = "queued"  # queued / running / backoff / done / failed
    restarts: int = 0
    failures: int = 0  # 连续失败次数（成功跑完一段——心跳推进过——不清零，按次累计更保守）
    last_exit: int | None = None
    started_at: float | None = None
    retry_at: float = 0.0
    peak_mb: float = 0.0
    rss_mb: float = 0.0
    hb: dict = field(default_factory=dict)


class Supervisor:
    def __init__(self, home: Path, config: Path, *, worker_argv=None, clock=time.time) -> None:
        self.home, self.config_path = home, config
        self.saves = layout.ensure(layout.saves(home))
        (self.saves / "specs").mkdir(exist_ok=True)
        self.sim, self.jobs = load_config(config)
        self.log = layout.setup_logging("supervisor", self.saves)
        self.clock = clock
        self.worker_argv = worker_argv or self._default_argv
        self.started = clock()
        self.state_file = self.saves / "supervisor-state.json"
        self.gen = int(layout.read_json(self.state_file, {}).get("gen", 1))
        self.slots: dict[str, Slot] = {}
        self.restarts_log: list[dict] = []
        self.janitor_report: dict | None = None
        self.web: subprocess.Popen | None = None
        self._last_status = self._last_janitor = 0.0
        self._enqueue_generation()

    # ------------------------------------------------------------ 任务
    def _enqueue_generation(self) -> None:
        for j in self.jobs:
            rid = f"{j['name']}-g{self.gen}"
            spec = dict(run_id=rid, strategy=j["strategy"], params=j["params"], start=j["start"],
                        end=j["end"], engine=j["engine"], snapshot_root=self.sim["snapshot_root"],
                        snapshot_id=self.sim["snapshot_id"], seconds_per_day=j["seconds_per_day"],
                        checkpoint_every=self.sim["checkpoint_every"],
                        checkpoint_secs=self.sim["checkpoint_secs"], soft_rss_mb=self.sim["soft_rss_mb"])
            if j.get("min5_root"):
                spec["min5_root"] = j["min5_root"]
            slot = Slot(rid, spec)
            if self._db_status(rid) == layout.STATUS_DONE:
                slot.state = "done"
            self.slots[rid] = slot
        layout.write_json_atomic(self.state_file, {"gen": self.gen})
        self.log.info("generation %d: %d jobs", self.gen, len(self.jobs))

    def _db_status(self, run_id: str) -> str | None:
        db = layout.run_db(self.saves, run_id)
        if not db.exists():
            return None
        try:
            with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5) as c:
                row = c.execute("SELECT value FROM meta WHERE key='status'").fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    def _default_argv(self, slot: Slot) -> list[str]:
        p = self.saves / "specs" / f"{slot.run_id}.json"
        p.write_text(json.dumps(slot.spec, ensure_ascii=False), encoding="utf-8")
        return [sys.executable, "-m", "q6.sim.worker", "--saves", str(self.saves), "--spec-json", f"@{p}"]

    def _spawn(self, argv: list[str], errlog: Path) -> subprocess.Popen:
        if errlog.exists() and errlog.stat().st_size > 5 * 2**20:
            errlog.unlink()  # 原生崩溃输出才会进这里；防止崩溃循环把它写大
        env = {**os.environ, **_CHILD_ENV}
        kw: dict = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, env=env)
        if sys.platform == "win32":
            kw["creationflags"] = CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
        else:
            kw["preexec_fn"] = lambda: os.nice(10)
        with open(errlog, "ab") as err:
            return subprocess.Popen(argv, stderr=err, **kw)

    def _start(self, slot: Slot, now: float) -> None:
        err = layout.logs_dir(self.saves) / f"worker-{slot.run_id}.err"
        slot.proc = self._spawn(self.worker_argv(slot), err)
        slot.state, slot.started_at, slot.hb, slot.rss_mb = "running", now, {}, 0.0
        self.log.info("start %s pid=%d", slot.run_id, slot.proc.pid)

    def _record_restart(self, slot: Slot, code: int | None, reason: str, now: float) -> None:
        self.restarts_log.append(dict(ts=_iso(now), run_id=slot.run_id, exit=code, reason=reason))
        self.restarts_log = self.restarts_log[-50:]

    def _on_exit(self, slot: Slot, code: int, now: float, reason: str = "exit") -> None:
        slot.proc, slot.last_exit = None, code
        if code == EXIT_DONE and reason == "exit":
            slot.state = "done"
            self.log.info("done %s", slot.run_id)
            return
        slot.restarts += 1
        if code == EXIT_SOFT_MEMORY and reason == "exit":
            slot.state, slot.retry_at = "backoff", now
            self._record_restart(slot, code, "soft_memory", now)
            self.log.warning("%s soft-memory restart", slot.run_id)
            return
        slot.failures += 1
        why = "memory" if code == EXIT_HARD_MEMORY else reason
        self._record_restart(slot, code, why, now)
        if slot.failures >= self.sim["max_failures"]:
            slot.state = "failed"
            self.log.error("%s failed %d times, giving up (last exit %s, %s)",
                           slot.run_id, slot.failures, code, why)
            return
        delay = min(self.sim["backoff_max"], self.sim["backoff_base"] * 2 ** (slot.failures - 1))
        slot.state, slot.retry_at = "backoff", now + delay
        self.log.warning("%s exited %s (%s); retry in %.0fs", slot.run_id, code, why, delay)

    @staticmethod
    def _tree(p: subprocess.Popen) -> list:
        import psutil

        try:
            root = psutil.Process(p.pid)
            return [root, *root.children(recursive=True)]
        except psutil.Error:
            return []

    def _kill(self, p: subprocess.Popen) -> int:
        import psutil

        for q in reversed(self._tree(p)):  # 先杀子进程（真解释器），再杀转发器
            try:
                q.kill()
            except psutil.Error:
                pass
        p.kill()
        try:
            return p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return -9

    def _measure(self, slot: Slot) -> set[int]:
        import psutil

        pids, rss, peak = set(), 0.0, 0.0
        for q in self._tree(slot.proc):
            try:
                mi = q.memory_info()
            except psutil.Error:
                continue
            pids.add(q.pid)
            rss = max(rss, mi.rss / 2**20)
            peak = max(peak, getattr(mi, "peak_wset", mi.rss) / 2**20)
        slot.rss_mb = rss
        slot.peak_mb = max(slot.peak_mb, peak, rss)
        return pids

    # ------------------------------------------------------------ 主循环
    def tick(self) -> None:
        now = self.clock()
        timeout, grace = self.sim["heartbeat_timeout"], self.sim["startup_grace"]
        for slot in self.slots.values():
            if slot.state != "running":
                continue
            code = slot.proc.poll()
            if code is not None:
                self._on_exit(slot, code, now)
                continue
            pids = self._measure(slot) | {slot.proc.pid}
            hb = layout.read_json(layout.hb_dir(self.saves) / f"{slot.run_id}.json", None)
            if hb is not None:  # Win 上读到正在原子替换的文件会失败：沿用上次的
                slot.hb = hb
            fresh = slot.hb.get("pid") in pids
            age = now - slot.hb["ts"] if fresh else now - slot.started_at
            if age > (timeout if fresh else grace):
                self.log.error("%s heartbeat stale %.0fs, killing pid %d", slot.run_id, age, slot.proc.pid)
                self._on_exit(slot, self._kill(slot.proc), now, reason="heartbeat")
        # 退避中的任务继续占着自己的槽位：否则槽位会被排队的新任务抢走，
        # 被杀的任务要等别人跑完才能续跑（Win 实测）
        busy = sum(s.state in ("running", "backoff") for s in self.slots.values())
        for slot in self.slots.values():
            if slot.state == "backoff" and now >= slot.retry_at:
                self._start(slot, now)
            elif slot.state == "queued" and busy < self.sim["max_workers"]:
                self._start(slot, now)
                busy += 1
        finished = all(s.state in ("done", "failed") for s in self.slots.values())
        if self.sim["loop"] and self.slots and finished:
            self.gen += 1
            self.slots = {}
            self._enqueue_generation()
        self._web_tick()
        if now - self._last_janitor >= self.sim["janitor_every"]:
            self._last_janitor = now
            self._janitor()
        if now - self._last_status >= self.sim["status_every"]:
            self._last_status = now
            self.write_status(now)

    def _web_tick(self) -> None:
        if not self.sim["web"]:
            return
        if self.web is not None and self.web.poll() is None:
            return
        if self.web is not None:
            self.log.warning("web panel exited %s, restarting", self.web.returncode)
        argv = [sys.executable, "-m", "q6.sim.web", "--saves", str(self.saves),
                "--host", self.sim["web_host"], "--port", str(self.sim["web_port"])]
        self.web = self._spawn(argv, layout.logs_dir(self.saves) / "web.err")

    def _janitor(self) -> None:
        try:
            from q6.sim import janitor
        except ImportError:
            return
        try:
            self.janitor_report = janitor.run_once(self.saves)
            if self.janitor_report.get("over_budget"):
                self.log.warning("saves over budget: %s", self.janitor_report)
        except Exception:  # janitor 出错不能拖垮 supervisor
            self.log.exception("janitor failed")

    def write_status(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        workers = []
        for s in self.slots.values():
            if s.state in ("running", "backoff", "failed"):
                hb = s.hb
                workers.append(dict(
                    run_id=s.run_id, pid=s.proc.pid if s.proc else None, state=s.state, restarts=s.restarts,
                    last_exit=s.last_exit, started_at=_iso(s.started_at) if s.started_at else None,
                    worker_pid=hb.get("pid"), rss_mb=round(s.rss_mb, 1), peak_mb=round(s.peak_mb, 1),
                    hb_age_s=round(now - hb["ts"], 1) if hb.get("ts") else None, day=hb.get("day"),
                    n_days=hb.get("n_days"), total_days=hb.get("total_days"), equity=hb.get("equity")))
        layout.write_json_atomic(self.saves / "supervisor.json", dict(
            pid=os.getpid(), started_at=_iso(self.started), updated_at=_iso(now),
            config=str(self.config_path),
            max_workers=self.sim["max_workers"], generation=self.gen, workers=workers,
            queue=[s.run_id for s in self.slots.values() if s.state == "queued"],
            done=[s.run_id for s in self.slots.values() if s.state == "done"],
            janitor=self.janitor_report, restarts_log=self.restarts_log,
            job_limits=limits_win.job_limits(),
            web_pid=self.web.pid if self.web is not None and self.web.poll() is None else None))

    def shutdown(self) -> None:
        for s in self.slots.values():
            if s.proc is not None and s.proc.poll() is None:
                self._kill(s.proc)
        if self.web is not None and self.web.poll() is None:
            self._kill(self.web)
        self.log.info("shutdown")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=str(layout.home()))
    ap.add_argument("--config", required=True)
    ap.add_argument("--run-for", type=float, default=None, help="运行这么多秒后退出（测试 / 演示用）")
    a = ap.parse_args(argv)
    home = Path(a.home).resolve()
    saves = layout.ensure(layout.saves(home))
    lock = layout.SingleInstance(saves / "supervisor.lock")
    if not lock.ok:
        print("another supervisor is running", file=sys.stderr)
        return 0
    sim, _ = load_config(Path(a.config))
    os.environ.update(_CHILD_ENV)  # 必须在任何 numpy 导入之前（见模块说明）
    limits_win.confine_self(int(sim["mem_limit_mb"]))
    sup = Supervisor(home, Path(a.config).resolve())
    sup.log.info("supervisor pid=%d job=%s", os.getpid(), limits_win.job_limits())
    stop = []
    for sig in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            signal.signal(sig, lambda *_: stop.append(1))
    t_end = None if a.run_for is None else time.time() + a.run_for
    try:
        while not stop and (t_end is None or time.time() < t_end):
            sup.tick()
            time.sleep(1.0)
    finally:
        sup.write_status()
        sup.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
