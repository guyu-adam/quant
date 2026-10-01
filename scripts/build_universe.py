#!/usr/bin/env python3
"""构建并续跑中证 800 月度历史成分文件。"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from q6.data.sources.baostock import BaostockClient
from q6.data.universe import collect_month, empty_universe, month_ends, normalize_universe
from q6.market.calendar import TradingCalendar


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2005-01")
    parser.add_argument("--end", default="2026-09")
    parser.add_argument("--out", default="data/universe/universe_monthly.parquet")
    args = parser.parse_args()

    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/trade_dates.csv"
    calendar = TradingCalendar.from_csv(fixture)
    dates = month_ends(args.start, args.end, calendar)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame = normalize_universe(pd.read_parquet(output)) if output.exists() else empty_universe()
    done = set(pd.to_datetime(frame["month_end"]).dt.normalize())
    earliest_complete: dict[str, str] = {}
    with BaostockClient() as client:
        for d in dates:
            if d not in done:
                batch = collect_month(client, d)
                frame = normalize_universe(pd.concat([frame, batch], ignore_index=True))
                frame.to_parquet(output, index=False)
            else:
                batch = frame[frame["month_end"] == d]
            sizes = batch.groupby("index")["code"].nunique().to_dict() if not batch.empty else {}
            if sizes.get("hs300", 0) == 300:
                earliest_complete.setdefault("hs300", d.strftime("%Y-%m"))
            if sizes.get("zz500", 0) == 500:
                earliest_complete.setdefault("zz500", d.strftime("%Y-%m"))

    if not output.exists():
        frame.to_parquet(output, index=False)
    hs_month = earliest_complete.get("hs300", "未找到")
    zz_month = earliest_complete.get("zz500", "未找到")
    print(f"最早完整月份: hs300={hs_month} zz500={zz_month}")
    if frame.empty:
        print("无成分数据")
        return
    monthly = frame.groupby([frame["month_end"].dt.year, "month_end"])["code"].nunique()
    print("每年平均月度成分数:")
    print(monthly.groupby(level=0).mean().round(2).to_string())
    print(f"并集只数: {frame['code'].nunique()}")
    print(f"月末数: {frame['month_end'].nunique()}")
    print(f"输出: {output.resolve()}")


if __name__ == "__main__":
    main()
