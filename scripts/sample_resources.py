"""P3-10：模拟盘资源采样，打印一行 CSV（Mac 端每 5 分钟 ssh 调一次，攒成 24h 资源曲线）。只读，不改任何东西。

    python scripts/sample_resources.py --home D:\\quant6 [--header]

列：ts, sup_pid, gen, running, backoff, done, failed, restarts, procs, sum_wset_mb, max_wset_mb,
max_peak_wset_mb, max_private_mb, all_in_job, saves_mb, home_mb
- 进程 = 命令行含 q6.sim、--home / --saves 指向本 home 的 python 进程
  （supervisor / worker / 面板，含 venv 转发器）；
  peak_wset 是进程生命周期内的峰值工作集（Windows），其它平台退回 rss。
- all_in_job：Windows 上每个进程 IsProcessInJob 都为真才是 1（512MB 硬限由 supervisor 的 Job 施加）。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import psutil

COLS = ("ts", "sup_pid", "gen", "running", "backoff", "done", "failed", "restarts", "procs", "sum_wset_mb",
        "max_wset_mb", "max_peak_wset_mb", "max_private_mb", "all_in_job", "saves_mb", "home_mb")


def _tree_mb(p: Path) -> float:
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += os.stat(os.path.join(root, f)).st_size
            except OSError:  # 正在被原子替换 / janitor 删掉的文件
                pass
    return total / 2**20


def _in_job(pid: int) -> bool | None:
    if sys.platform != "win32":
        return None
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        r = ctypes.c_int(0)
        return bool(k32.IsProcessInJob(h, None, ctypes.byref(r))) and bool(r.value)
    finally:
        k32.CloseHandle(h)


def _belongs(argv: list[str], home: Path) -> bool:
    """进程的 --home / --saves 指向本 home（同机还有别的 home，例如 scratch 运行）。"""
    for flag, want in (("--home", home), ("--saves", home / "saves")):
        if flag in argv[:-1]:
            got = argv[argv.index(flag) + 1].strip('"')
            if os.path.normcase(os.path.normpath(got)) == os.path.normcase(os.path.normpath(str(want))):
                return True
    return False


def sample(home: Path) -> dict:
    saves = home / "saves"
    try:
        sup = json.loads((saves / "supervisor.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sup = {}
    states = [w.get("state") for w in sup.get("workers", [])]
    wset, peak, priv, injob = [], [], [], []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            argv = p.info["cmdline"] or []
            if "python" not in (p.info["name"] or "").lower() or "q6.sim" not in " ".join(argv):
                continue
            if not _belongs(argv, home):
                continue
            mi = p.memory_info()
            wset.append(mi.rss / 2**20)
            peak.append(getattr(mi, "peak_wset", mi.rss) / 2**20)
            priv.append(getattr(mi, "private", getattr(mi, "vms", 0)) / 2**20)
            injob.append(_in_job(p.pid))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return dict(
        ts=datetime.now().isoformat(timespec="seconds"), sup_pid=sup.get("pid"), gen=sup.get("generation"),
        running=states.count("running"), backoff=states.count("backoff"), done=len(sup.get("done", [])),
        failed=states.count("failed"), restarts=sum(w.get("restarts", 0) for w in sup.get("workers", [])),
        procs=len(wset), sum_wset_mb=round(sum(wset), 1), max_wset_mb=round(max(wset, default=0), 1),
        max_peak_wset_mb=round(max(peak, default=0), 1), max_private_mb=round(max(priv, default=0), 1),
        all_in_job=int(all(x is True for x in injob)) if injob and None not in injob else "",
        saves_mb=round(_tree_mb(saves), 1), home_mb=round(_tree_mb(home), 1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--header", action="store_true")
    a = ap.parse_args()
    row = sample(Path(a.home))
    if a.header:
        print(",".join(COLS))
    print(",".join("" if row[c] is None else str(row[c]) for c in COLS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
