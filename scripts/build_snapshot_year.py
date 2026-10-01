#!/usr/bin/env python3
"""读取单年暂存数据并写入规范化快照分区。"""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path

import pandas as pd
import psutil
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq


def _sample_rss(process: psutil.Process, state: dict[str, int], stop: threading.Event) -> None:
    """每半秒记录工作进程的峰值 RSS。"""
    while not stop.is_set():
        state["peak"] = max(state["peak"], process.memory_info().rss)
        stop.wait(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="构建一个年度日线快照分区")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    process = psutil.Process()
    state = {"peak": process.memory_info().rss}
    stop = threading.Event()
    monitor = threading.Thread(target=_sample_rss, args=(process, state, stop), daemon=True)
    monitor.start()
    try:
        dataset = ds.dataset(args.source, format="parquet")
        scanner = dataset.scanner(
            filter=(ds.field("date") >= pd.Timestamp(f"{args.year}-01-01").to_pydatetime())
            & (ds.field("date") < pd.Timestamp(f"{args.year + 1}-01-01").to_pydatetime()),
            batch_size=8192,
            use_threads=False,
        )
        batches = list(scanner.to_batches())
        if not batches:
            parser.error(f"{args.source} 在 {args.year} 年没有日线行")
        table = pa.Table.from_batches(batches)
        del batches
        del scanner, dataset
        pa.default_memory_pool().release_unused()
        ordering = pc.sort_indices(
            table,
            sort_keys=[
                ("date", "ascending", "at_start"),
                ("code", "ascending", "at_start"),
            ],
        )
        sorted_table = pc.take(table, ordering)
        del ordering, table
        pa.default_memory_pool().release_unused()
        rows = sorted_table.num_rows
        stats = {
            "year": args.year,
            "rows": rows,
            "codes": int(pc.count_distinct(sorted_table["code"]).as_py()),
            "suspended_rows": int(
                pc.sum(pc.cast(pc.not_equal(sorted_table["tradestatus"], 1), pa.int64())).as_py()
            ),
            "quarantined_rows": int(pc.sum(pc.cast(sorted_table["bad"], pa.int64())).as_py()),
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            sorted_table,
            args.out,
            compression="zstd",
            compression_level=9,
            use_dictionary=True,
            write_statistics=True,
        )
        del sorted_table
    finally:
        stop.set()
        monitor.join()
    state["peak"] = max(state["peak"], process.memory_info().rss)
    stats["peak_rss_bytes"] = state["peak"]
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
