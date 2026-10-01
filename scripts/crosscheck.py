#!/usr/bin/env python3
"""对 Baostock 原始日线与 AkShare 未复权日线做抽样校验。"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from q6.data.sources.akshare_src import daily as ak_daily


def _reason(field: str, left: float, right: float) -> str:
    if field == "volume" and (
        np.isclose(left, right * 100, rtol=0.005)
        or np.isclose(right, left * 100, rtol=0.005)
    ):
        return "单位"
    if field == "close":
        return "复权口径"
    return "数据错误" if np.isfinite(left) and np.isfinite(right) else "未知"


def main() -> int:
    parser = argparse.ArgumentParser(description="抽样交叉校验 Baostock 与 AkShare 日线")
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    pieces = [pd.read_parquet(path) for path in sorted(args.raw_dir.rglob("*.parquet"))]
    if not pieces:
        parser.error(f"原始目录没有 parquet 文件：{args.raw_dir}")
    raw = pd.concat(pieces, ignore_index=True)
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    raw["tradestatus"] = pd.to_numeric(raw["tradestatus"], errors="coerce")
    eligible = raw.loc[raw["tradestatus"] == 1].dropna(subset=["date", "code"])
    n = min(args.n, len(eligible))
    points = eligible.sample(n=n, random_state=args.seed).reset_index(drop=True)
    ak_by_code: dict[str, pd.DataFrame | Exception] = {}
    for code, group in points.groupby("code", sort=True):
        dates = pd.to_datetime(group["date"])
        try:
            bars = ak_daily(str(code), dates.min().strftime("%Y-%m-%d"), dates.max().strftime("%Y-%m-%d"))
            ak_by_code[str(code)] = bars.set_index("date")
        except Exception as exc:
            ak_by_code[str(code)] = exc

    failures: list[tuple[str, str, str]] = []
    deviations: dict[str, list[tuple[str, str, float, float, float, str]]] = {"close": [], "volume": []}
    matched = 0
    for row in points.itertuples(index=False):
        code, day = str(row.code), str(row.date)
        result = ak_by_code[code]
        if isinstance(result, Exception):
            failures.append((code, day, f"请求失败：{type(result).__name__}: {result}"))
            continue
        if day not in result.index:
            failures.append((code, day, "AkShare 未返回该交易日"))
            continue
        other = result.loc[day]
        if isinstance(other, pd.DataFrame):
            other = other.iloc[-1]
        matched += 1
        for field in ("close", "volume"):
            left, right = float(getattr(row, field)), float(other[field])
            if not np.isfinite(left) or not np.isfinite(right) or right == 0:
                failures.append((code, day, f"{field} 缺失或为零"))
                continue
            deviation = abs(left / right - 1)
            deviations[field].append((code, day, left, right, deviation, _reason(field, left, right)))

    lines = ["# Baostock 与 AkShare 日线交叉校验", "", f"- 随机种子：{args.seed}", f"- 抽样点数：{n}",
             f"- 成功比对点数：{matched}", f"- 失败点数：{len(failures)}", "", "## 偏差分布", "",
             "| 字段 | 最大值 | P99 | 中位数 |", "|---|---:|---:|---:|"]
    for field, values in deviations.items():
        arr = np.array([item[4] for item in values], dtype=float)
        if len(arr):
            lines.append(
                f"| {field} | {arr.max():.6%} | {np.quantile(arr, .99):.6%} | "
                f"{np.median(arr):.6%} |"
            )
        else:
            lines.append(f"| {field} | 无数据 | 无数据 | 无数据 |")
    lines.extend([
        "",
        "## 偏差超过 0.5% 的明细",
        "",
        "| 字段 | 股票 | 日期 | Baostock | AkShare | 相对偏差 | 初步原因 |",
        "|---|---|---|---:|---:|---:|---|",
    ])
    for field, values in deviations.items():
        for code, day, left, right, deviation, reason in values:
            if deviation > .005:
                lines.append(
                    f"| {field} | {code} | {day} | {left:g} | {right:g} | "
                    f"{deviation:.6%} | {reason} |"
                )
    lines.extend(["", "## 失败点及原因", "", "| 股票 | 日期 | 原因 |", "|---|---|---|"])
    lines.extend(f"| {code} | {day} | {reason} |" for code, day, reason in failures)
    lines.append("")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"抽样 {n} 点，成功 {matched} 点，失败 {len(failures)} 点；报告写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
