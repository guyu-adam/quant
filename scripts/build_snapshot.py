#!/usr/bin/env python3
"""从原始日线 parquet 构建可校验的数据快照。"""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from pathlib import Path

import pandas as pd
import psutil

from q6.data.clean import clean_daily
from q6.data.snapshot import write_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="构建内容寻址的数据快照")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/baostock/daily"))
    parser.add_argument("--asof", default="2026-09-30")
    parser.add_argument("--out", type=Path, default=Path("data/snapshots"))
    args = parser.parse_args()
    raw_files = sorted(args.raw_dir.rglob("*.parquet"))
    if not raw_files:
        parser.error(f"原始目录没有 parquet 文件：{args.raw_dir}")

    process = psutil.Process()
    peak_rss = process.memory_info().rss
    stop_monitor = threading.Event()

    def monitor_rss() -> None:
        nonlocal peak_rss
        while not stop_monitor.is_set():
            peak_rss = max(peak_rss, process.memory_info().rss)
            time.sleep(0.05)

    monitor = threading.Thread(target=monitor_rss, daemon=True)
    monitor.start()
    raw_parts = []
    for path in raw_files:
        raw_parts.append(pd.read_parquet(path))
        peak_rss = max(peak_rss, process.memory_info().rss)
    raw = pd.concat(raw_parts, ignore_index=True)
    del raw_parts
    cleaned = clean_daily(raw)
    del raw
    daily = cleaned.data
    quarantine = cleaned.quarantine
    del cleaned
    peak_rss = max(peak_rss, process.memory_info().rss)
    tables = {
        "daily": daily,
        "quarantine": quarantine,
        "calendar": pd.DataFrame({"date": sorted(daily["date"].drop_duplicates())}),
    }
    universe = args.raw_dir.parent / "universe_monthly.parquet"
    if universe.exists():
        tables["universe_monthly"] = pd.read_parquet(universe)
    code_version = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    snapshot_id = write_snapshot(
        tables, args.out, asof=args.asof, sources={"raw_dir": str(args.raw_dir)}, code_version=code_version
    )
    stop_monitor.set()
    monitor.join()
    peak_rss = max(peak_rss, process.memory_info().rss)
    manifest = json.loads((args.out / snapshot_id / "manifest.json").read_text(encoding="utf-8"))
    print(f"snapshot_id: {snapshot_id}")
    for item in manifest["files"]:
        print(f"{item['path']}: rows={item['rows']} bytes={item['bytes']}")
    print(f"total_bytes: {sum(item['bytes'] for item in manifest['files'])}")
    print(f"peak_rss_bytes: {peak_rss}")
    if peak_rss > 512 * 1024 * 1024:
        raise MemoryError(f"峰值 RSS 超过 512 MB：{peak_rss}")


if __name__ == "__main__":
    main()
