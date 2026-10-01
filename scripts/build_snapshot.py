#!/usr/bin/env python3
"""逐股清洗并按年流式构建快照和统计报告。"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path

import numpy as np
import pandas as pd
import psutil

from q6.data.adjust import compare_vendor_factor
from q6.data.clean import clean_daily
from q6.data.snapshot import write_snapshot


def _sample_rss(process: psutil.Process, state: dict[str, int], stop: threading.Event) -> None:
    """每半秒记录一次当前进程峰值 RSS。"""
    while not stop.is_set():
        state["peak"] = max(state["peak"], process.memory_info().rss)
        stop.wait(0.5)


def _missing_report(
    universe: pd.DataFrame, calendar: pd.DatetimeIndex, clean_dir: Path
) -> tuple[dict[int, dict], list[tuple]]:
    """按月末成分区间逐股统计缺行，并列出缺失最多的股票。"""
    months = sorted(pd.to_datetime(universe["month_end"]).drop_duplicates())
    month_code_counts = (
        universe.assign(month_end=pd.to_datetime(universe["month_end"]))
        .groupby("month_end")["code"]
        .nunique()
    )
    dates = pd.DatetimeIndex(calendar).normalize().to_numpy(dtype="datetime64[D]")
    annual: Counter[int] = Counter()
    missing_by_code: Counter[str] = Counter()
    examples: dict[str, list[pd.Timestamp]] = {}
    missing_bounds: dict[str, tuple[np.datetime64, np.datetime64]] = {}
    for code, memberships in universe.groupby("code", sort=False):
        path = clean_dir / f"{code}.parquet"
        actual = np.array([], dtype="datetime64[D]")
        if path.exists():
            actual = pd.to_datetime(pd.read_parquet(path, columns=["date"])["date"]).to_numpy(
                dtype="datetime64[D]"
            )
        rows = memberships.sort_values("month_end")
        for month_end in pd.to_datetime(rows["month_end"]).drop_duplicates():
            later = [m for m in months if m > month_end]
            next_month = later[0] if later else dates.max()
            month_day = np.datetime64(month_end, "D")
            next_day = np.datetime64(next_month, "D")
            expected = dates[(dates > month_day) & (dates <= next_day)]
            if len(actual):
                positions = np.searchsorted(actual, expected)
                found = (positions < len(actual)) & (
                    actual[np.minimum(positions, len(actual) - 1)] == expected
                )
            else:
                found = np.zeros(len(expected), dtype=bool)
            missing_dates = expected[~found]
            if len(missing_dates):
                years, counts = np.unique(pd.DatetimeIndex(missing_dates).year, return_counts=True)
                for year, count in zip(years, counts, strict=True):
                    annual[int(year)] += int(count)
                missing_by_code[str(code)] += len(missing_dates)
                examples.setdefault(str(code), []).extend(pd.Timestamp(d) for d in missing_dates[:3])
                key = str(code)
                bounds = (missing_dates[0], missing_dates[-1])
                if key in missing_bounds:
                    bounds = (
                        min(missing_bounds[key][0], bounds[0]),
                        max(missing_bounds[key][1], bounds[1]),
                    )
                missing_bounds[key] = bounds
    totals: Counter[int] = Counter()
    for month_end in months:
        later = [m for m in months if m > month_end]
        next_month = later[0] if later else dates.max()
        interval = dates[(dates > np.datetime64(month_end, "D")) & (dates <= np.datetime64(next_month, "D"))]
        for date in interval:
            totals[pd.Timestamp(date).year] += int(month_code_counts.loc[month_end])
    annual_report = {
        year: {
            "expected": totals[year],
            "missing": annual[year],
            "rate": annual[year] / totals[year] if totals[year] else 0.0,
        }
        for year in sorted(totals)
    }
    top = []
    for code, count in missing_by_code.most_common(10):
        sample = examples.get(code, [])
        path = clean_dir / f"{code}.parquet"
        dates_for_code = pd.read_parquet(path, columns=["date"])["date"] if path.exists() else None
        start = pd.Timestamp(dates_for_code.min()) if dates_for_code is not None else None
        end = pd.Timestamp(dates_for_code.max()) if dates_for_code is not None else None
        first_missing, last_missing = missing_bounds[code]
        cause = (
            "数据源缺"
            if start is None
            else "上市前"
            if first_missing < np.datetime64(start, "D")
            else "退市后"
            if end and last_missing > np.datetime64(end, "D")
            else "数据源缺"
        )
        top.append((code, count, cause, ", ".join(str(d.date()) for d in sample[:3])))
    return annual_report, top


def main() -> None:
    parser = argparse.ArgumentParser(description="逐股清洗并流式构建内容寻址快照")
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--calendar", type=Path, default=Path("tests/fixtures/trade_dates.csv"))
    parser.add_argument("--asof", default="2026-09-30")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--clean-dir", type=Path)
    parser.add_argument("--report", type=Path, default=Path("docs/research/snapshot_stats.md"))
    args = parser.parse_args()
    clean_dir = args.clean_dir or args.raw_dir.parent.parent.parent / "cache" / "clean"
    clean_dir.mkdir(parents=True, exist_ok=True)
    yearly_clean_dir = clean_dir.parent / "clean_by_year"
    yearly_clean_dir.mkdir(parents=True, exist_ok=True)
    for old_partition in yearly_clean_dir.iterdir():
        if old_partition.is_dir():
            shutil.rmtree(old_partition)
        else:
            old_partition.unlink()
    raw_files = sorted(args.raw_dir.glob("*.parquet"))
    if not raw_files:
        parser.error(f"原始目录没有 parquet 文件：{args.raw_dir}")
    universe = pd.read_parquet(args.universe)
    cal_raw = pd.read_csv(args.calendar)
    cal_col = "date" if "date" in cal_raw else cal_raw.columns[0]
    calendar = pd.to_datetime(cal_raw[cal_col])
    if "is_trading_day" in cal_raw:
        calendar = calendar[pd.to_numeric(cal_raw["is_trading_day"], errors="coerce").eq(1)]
    calendar = calendar[(calendar >= "2005-01-01") & (calendar <= args.asof)]
    quarantines = []
    clean_files = []
    process, state, stop = psutil.Process(), {"peak": psutil.Process().memory_info().rss}, threading.Event()
    monitor = threading.Thread(target=_sample_rss, args=(process, state, stop), daemon=True)
    monitor.start()
    build_started_at = datetime.now(UTC)
    progress_times: list[datetime] = []
    for progress_path in args.raw_dir.parent.glob("_*progress.shard*of*.json"):
        progress_payload = json.loads(progress_path.read_text(encoding="utf-8"))
        for record in progress_payload.values():
            fetched_at = record.get("fetched_at")
            if fetched_at:
                progress_times.append(datetime.fromisoformat(fetched_at))
    try:
        for source in raw_files:
            code = source.stem
            target = clean_dir / f"{code}.parquet"
            raw = pd.read_parquet(source)
            result = clean_daily(raw)
            result.data.to_parquet(target, index=False, row_group_size=250)
            if not result.quarantine.empty:
                quarantines.append(result.quarantine)
            clean_files.append(target)
            for year_value, yearly_frame in result.data.groupby(result.data["date"].dt.year, sort=True):
                year = int(year_value)
                year_dir = yearly_clean_dir / f"year={year}"
                year_dir.mkdir(parents=True, exist_ok=True)
                yearly_frame.to_parquet(year_dir / f"{code}.parquet", index=False, row_group_size=250)
                del yearly_frame
            del raw, result
        quarantine = (
            pd.concat(quarantines, ignore_index=True)
            if quarantines
            else pd.DataFrame(
                {
                    "code": pd.Series(dtype=str),
                    "date": pd.Series(dtype="datetime64[ns]"),
                    "reason": pd.Series(dtype=str),
                }
            )
        )
        years = sorted(calendar.dt.year.unique())
        snapshot_parts_dir = clean_dir.parent / "snapshot_daily"
        snapshot_parts_dir.mkdir(parents=True, exist_ok=True)
        year_parts: list[tuple[int, Path]] = []
        year_stats: dict[int, dict] = {}
        worker = Path(__file__).with_name("build_snapshot_year.py")
        for year_value in years:
            year = int(year_value)
            source = yearly_clean_dir / f"year={year}"
            destination = snapshot_parts_dir / f"year={year}.parquet"
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(worker),
                    "--source",
                    str(source),
                    "--year",
                    str(year),
                    "--out",
                    str(destination),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            child_process = psutil.Process(child.pid)
            while child.poll() is None:
                try:
                    state["peak"] = max(state["peak"], child_process.memory_info().rss)
                except psutil.NoSuchProcess:
                    pass
                time.sleep(0.5)
            child_stdout, child_stderr = child.communicate()
            if child.returncode:
                raise RuntimeError(f"{year} 年分区构建失败：{child_stderr}")
            year_stat = json.loads(child_stdout.strip().splitlines()[-1])
            state["peak"] = max(state["peak"], int(year_stat["peak_rss_bytes"]))
            year_stats[year] = year_stat
            year_parts.append((year, destination))
        tables = {
            "daily": iter(year_parts),
            "quarantine": quarantine,
            "calendar": pd.DataFrame({"date": calendar}),
            "universe_monthly": universe,
        }
        version = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        build_finished_at = datetime.now(UTC)

        sid = write_snapshot(
            tables,
            args.out,
            asof=args.asof,
            sources={
                "provider": "baostock",
                "baostock_version": package_version("baostock"),
                "start": "2005-01-01",
                "end": args.asof,
                "build_started_at": build_started_at.isoformat(),
                "build_finished_at": build_finished_at.isoformat(),
                "fetch_started_at": min(progress_times).isoformat() if progress_times else None,
                "fetch_finished_at": max(progress_times).isoformat() if progress_times else None,
            },
            code_version=version,
        )
    except BaseException:
        stop.set()
        monitor.join()
        raise
    manifest = json.loads((args.out / sid / "manifest.json").read_text(encoding="utf-8"))
    base = args.out / sid
    directory_size = subprocess.run(
        ["du", "-sh", str(base)], check=True, capture_output=True, text=True
    ).stdout.strip()
    lines = [
        "# 快照统计报告",
        "",
        f"- 快照 ID：`{sid}`",
        f"- 快照目录：`{base}`",
        f"- 目录大小（du -sh）：`{directory_size}`",
        f"- 目录总字节数：{sum(item['bytes'] for item in manifest['files'])}",
        f"- 峰值 RSS：{state['peak']} bytes ({state['peak'] / 1024**2:.1f} MiB)",
        "",
        "## 文件明细",
        "",
        "| 文件 | 行数 | 字节数 |",
        "|---|---:|---:|",
    ]
    lines.extend(f"| `{item['path']}` | {item['rows']} | {item['bytes']} |" for item in manifest["files"])
    lines += [
        "",
        "## 年度统计",
        "",
        "| 年份 | 行数 | 股票数 | 停牌行占比 | 隔离行数 |",
        "|---:|---:|---:|---:|---:|",
    ]
    annual_missing, top_missing = _missing_report(universe, pd.DatetimeIndex(calendar), clean_dir)
    for year_value in years:
        year = int(year_value)
        row = year_stats[year]
        suspended = row["suspended_rows"] / row["rows"] if row["rows"] else 0.0
        lines.append(
            f"| {year} | {row['rows']} | {row['codes']} | {suspended:.4%} | {row['quarantined_rows']} |"
        )
    lines += ["", "## 按年缺失率", "", "| 年份 | 应有行数 | 缺行 | 缺失率 |", "|---:|---:|---:|---:|"]
    lines.extend(
        f"| {year} | {row['expected']} | {row['missing']} | {row['rate']:.4%} |"
        for year, row in annual_missing.items()
    )
    lines += ["", "## 缺失最多的股票", "", "| 股票 | 缺行 | 初判原因 | 缺失日期示例 |", "|---|---:|---|---|"]
    lines.extend(f"| {code} | {count} | {cause} | {sample} |" for code, count, cause, sample in top_missing)
    reason_counts: Counter[str] = Counter()
    reason_examples: dict[str, list[str]] = {}
    for row in quarantine.to_dict("records"):
        for reason in str(row["reason"]).split("; "):
            reason_counts[reason] += 1
            reason_examples.setdefault(reason, [])
            if len(reason_examples[reason]) < 3:
                reason_examples[reason].append(f"{row['code']} {pd.Timestamp(row['date']).date()}")
    lines += ["", "## 隔离原因", ""]
    lines.extend(
        f"- {reason}：{count} 行；例：{', '.join(reason_examples[reason])}"
        for reason, count in reason_counts.items()
    )
    mismatches = []
    mismatch_codes: set[str] = set()
    missing_factor_codes: set[str] = set()
    factor_gaps: list[dict[str, str]] = []
    for path in clean_files:
        factor_path = args.raw_dir.parent / "adjfactor" / path.name
        if not factor_path.exists():
            factor_gaps.append({"code": path.stem, "reason": "复权因子文件缺失"})
            missing_factor_codes.add(path.stem)
            continue
        own = pd.read_parquet(path, columns=["code", "date", "adj_factor"])
        vendor = pd.read_parquet(factor_path)
        own["code"] = own["code"].astype(str)
        vendor_col = "backAdjustFactor" if "backAdjustFactor" in vendor else "adjustFactor"
        if vendor_col not in vendor or vendor.empty or vendor[vendor_col].isna().all():
            factor_gaps.append({"code": path.stem, "reason": f"缺少因子列 {vendor_col}"})
            missing_factor_codes.add(path.stem)
            continue
        vendor = vendor.rename(columns={"dividOperateDate": "date", vendor_col: "vendor_factor"})
        if "code" not in vendor:
            vendor["code"] = path.stem
        vendor["code"] = vendor["code"].astype(str)
        compared = compare_vendor_factor(own, vendor, rtol=1e-4)
        if not compared.empty:
            mismatch_codes.update(compared["code"].astype(str).unique())
            remaining = max(0, 20 - len(mismatches))
            mismatches.extend(compared.head(remaining).to_dict("records"))
    lines += [
        "",
        "## 复权因子交叉校验",
        "",
        f"- 因子不一致股票数：{len(mismatch_codes)}",
        f"- 缺失复权因子股票数：{len(missing_factor_codes)}",
        "- 明细前 20 条：",
        "",
        "```json",
        json.dumps(mismatches[:20], ensure_ascii=False, indent=2, default=str),
        "```",
        f"- 缺失因子文件明细：{json.dumps(factor_gaps[:20], ensure_ascii=False)}",
        "",
        "## 体积结论",
        "",
        (
            f"Parquet 文件合计 {sum(item['bytes'] for item in manifest['files'])} bytes；"
            f"是否不超过 300 MB：{sum(item['bytes'] for item in manifest['files']) <= 300 * 1024**2}。"
        ),
    ]
    stop.set()
    monitor.join()
    state["peak"] = max(state["peak"], process.memory_info().rss)
    peak_line = next(index for index, line in enumerate(lines) if line.startswith("- 峰值 RSS："))
    lines[peak_line] = f"- 峰值 RSS：{state['peak']} bytes ({state['peak'] / 1024**2:.1f} MiB)"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"snapshot_id: {sid}")
    print(f"total_bytes: {sum(item['bytes'] for item in manifest['files'])}")
    print(f"peak_rss_bytes: {state['peak']}")
    if state["peak"] > 512 * 1024 * 1024:
        raise MemoryError(f"峰值 RSS 超过 512 MB：{state['peak']}")


if __name__ == "__main__":
    main()
