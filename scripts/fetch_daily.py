#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging

from q6.data.ingest import fetch_adjust_factor_many, fetch_daily_many, stable_shard


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Baostock daily bars")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--codes", help="Comma-separated Baostock codes")
    source.add_argument("--codes-file", help="File with one Baostock code per line")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--root", default="data/raw/baostock")
    parser.add_argument("--shard", help="稳定分片编号 i/n，例如 0/4")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    codes = (
        args.codes.split(",")
        if args.codes is not None
        else [line.strip() for line in open(args.codes_file, encoding="utf-8") if line.strip()]
    )
    shard_index = shard_count = None
    if args.shard:
        try:
            index_text, count_text = args.shard.split("/", maxsplit=1)
            shard_index, shard_count = int(index_text), int(count_text)
        except ValueError as exc:
            parser.error(f"无效分片格式：{args.shard}，应为 i/n")
            raise exc
        if shard_count < 1 or not 0 <= shard_index < shard_count:
            parser.error(f"分片编号越界：{args.shard}")
        codes = [code for code in codes if stable_shard(code, shard_count) == shard_index]
    suffix = f"shard{shard_index}of{shard_count}" if shard_index is not None else None
    daily_errors = fetch_daily_many(
        codes,
        args.start,
        args.end,
        args.root,
        progress_name=f"_progress.{suffix}.json" if suffix else "_progress.json",
        failures_name=f"_failures.{suffix}.json" if suffix else "_failures.json",
    )
    factor_errors = fetch_adjust_factor_many(
        codes,
        args.start,
        args.end,
        args.root,
        progress_name=f"_adjfactor_progress.{suffix}.json" if suffix else "_adjfactor_progress.json",
        failures_name=f"_adjfactor_failures.{suffix}.json" if suffix else "_adjfactor_failures.json",
    )
    return int(bool(daily_errors or factor_errors))


if __name__ == "__main__":
    raise SystemExit(main())
