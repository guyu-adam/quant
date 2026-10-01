#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging

from q6.data.ingest import fetch_daily_many


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Baostock daily bars")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--codes", help="Comma-separated Baostock codes")
    source.add_argument("--codes-file", help="File with one Baostock code per line")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--root", default="data/raw/baostock")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    codes = args.codes.split(",") if args.codes is not None else [
        line.strip() for line in open(args.codes_file, encoding="utf-8") if line.strip()
    ]
    return int(bool(fetch_daily_many(codes, args.start, args.end, args.root)))


if __name__ == "__main__":
    raise SystemExit(main())
