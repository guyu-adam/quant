#!/usr/bin/env python
"""Probe baostock five-minute bars or fetch the pre-lockbox CSI 800 history."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
from pathlib import Path

import baostock as bs
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path("/Users/guyu/Desktop/guyu-adam/quant/data/snapshots/6252e931a86bda15")
FIELDS = "date,time,code,open,high,low,close,volume,amount"
START, END = "2023-01-01", "2024-06-28"


def query(code: str, start: str, end: str) -> tuple[list[dict], float]:
    started = time.monotonic()
    try:
        rs = bs.query_history_k_data_plus(
            code, FIELDS, start_date=start, end_date=end, frequency="5", adjustflag="3"
        )
        if rs.error_code != "0":
            raise RuntimeError(f"{code} {start}..{end}: {rs.error_code} {rs.error_msg}")
        rows = []
        while rs.next():
            values = dict(zip(FIELDS.split(","), rs.get_row_data(), strict=True))
            stamp = values["time"]
            values["time"] = stamp[8:14]
            rows.append(values)
        return rows, time.monotonic() - started
    except TimeoutError:
        from baostock.common import context

        sock = getattr(context, "default_socket", None)
        if sock is not None:
            sock.close()
        context.default_socket = None
        login()
        raise


def login() -> None:
    from baostock.common import context
    from baostock.util import socketutil

    # baostock creates its socket inside login(), so set the timeout at the
    # send/receive boundary; setting it after login cannot bound a stalled login.
    if not getattr(socketutil, "_q6_timeout_installed", False):
        send_msg = socketutil.send_msg

        def send_msg_with_timeout(message: str):
            sock = getattr(context, "default_socket", None)
            if sock is not None:
                sock.settimeout(25)
            return send_msg(message)

        socketutil.send_msg = send_msg_with_timeout
        socketutil._q6_timeout_installed = True
    last_error = ""
    for attempt in range(1, 4):
        result = bs.login()
        if result.error_code == "0":
            context.default_socket.settimeout(25)
            return
        last_error = f"{result.error_code} {result.error_msg}"
        sock = getattr(context, "default_socket", None)
        if sock is not None:
            sock.close()
        context.default_socket = None
        if attempt < 3:
            time.sleep(attempt)
    raise RuntimeError(f"baostock login failed after 3 attempts: {last_error}")


def probe() -> None:
    report: dict = {
        "provider": "baostock",
        "frequency": "5",
        "adjustflag": "3",
        "probes": [],
        "volume_amount_checks": [],
    }
    login()
    try:
        windows = [
            ("2010-01-01", "2010-01-31"),
            ("2015-01-01", "2015-01-31"),
            ("2019-01-01", "2019-01-31"),
            ("2020-01-01", "2020-01-31"),
        ]
        for code in ("sh.600000", "sz.000001", "sz.300750", "sh.688981"):
            for start, end in windows:
                try:
                    rows, elapsed = query(code, start, end)
                    report["probes"].append(
                        {
                            "code": code,
                            "window": [start, end],
                            "rows": len(rows),
                            "first_date": rows[0]["date"] if rows else None,
                            "first_time": rows[0]["time"] if rows else None,
                            "last_time": rows[-1]["time"] if rows else None,
                            "seconds": elapsed,
                            "status": "ok",
                        }
                    )
                except Exception as exc:
                    report["probes"].append(
                        {"code": code, "window": [start, end], "status": "failed", "error": str(exc)}
                    )
        members_frame = pq.read_table(SNAPSHOT / "universe_monthly.parquet").to_pandas()
        members_frame["month_end"] = pd.to_datetime(members_frame["month_end"])
        eligible = members_frame[
            members_frame["month_end"].between("2023-01-01", "2023-01-31")
            & members_frame["index"].isin(["hs300", "zz500"])
        ]
        days = ["2023-01-03", "2023-01-04", "2023-01-05", "2023-01-06", "2023-01-09"]
        daily = pq.read_table(SNAPSHOT / "daily/year=2023.parquet").to_pandas()
        daily["date"] = pd.to_datetime(daily["date"]).dt.strftime("%Y-%m-%d")
        for code in eligible["code"].drop_duplicates().head(10):
            try:
                rows, elapsed = query(code, days[0], days[-1])
                minute = pd.DataFrame(rows)
                if not minute.empty:
                    minute["volume"] = pd.to_numeric(minute["volume"])
                    minute["amount"] = pd.to_numeric(minute["amount"])
                    totals = minute.groupby("date")[["volume", "amount"]].sum()
                else:
                    totals = pd.DataFrame(columns=["volume", "amount"])
                for day in days:
                    bars = totals.loc[day].to_dict() if day in totals.index else {"volume": 0, "amount": 0}
                    rows_daily = daily[(daily["code"] == code) & (daily["date"] == day)]
                    if len(rows_daily):
                        d = rows_daily.iloc[0]
                        report["volume_amount_checks"].append(
                            {
                                "code": code,
                                "date": day,
                                "minute_volume": int(bars["volume"]),
                                "daily_volume": int(d["volume"]),
                                "volume_abs_diff": int(bars["volume"] - d["volume"]),
                                "minute_amount": float(bars["amount"]),
                                "daily_amount": float(d["amount"]),
                                "amount_abs_diff": float(bars["amount"] - d["amount"]),
                                "request_seconds": elapsed,
                            }
                        )
            except Exception as exc:
                report["volume_amount_checks"].append({"code": code, "status": "failed", "error": str(exc)})
        sample_code = "sh.600000"
        sample_rows, month_seconds = query(sample_code, "2023-01-01", "2023-01-31")
        sample_frame = pd.DataFrame(sample_rows)
        sample_frame["date"] = pd.to_datetime(sample_frame["date"]).dt.date
        for name in ("open", "high", "low", "close", "amount"):
            sample_frame[name] = pd.to_numeric(sample_frame[name], errors="raise").astype("float64")
        if not sample_frame.empty:
            sample_frame["volume"] = pd.to_numeric(sample_frame["volume"], errors="raise").astype("int64")
            sink = io.BytesIO()
            pq.write_table(
                pa.Table.from_pandas(sample_frame[FIELDS.split(",")], preserve_index=False),
                sink,
                compression="zstd",
            )
            code_month_bytes = sink.tell()
            code_count = len(members())
            report["size_estimate"] = {
                "sample_code": sample_code,
                "sample_month": "2023-01",
                "sample_rows": len(sample_frame),
                "sample_zstd_parquet_bytes": code_month_bytes,
                "csi800_unique_codes": code_count,
                "months": 18,
                "estimated_bytes": code_month_bytes * code_count * 18,
                "estimated_mib": round(code_month_bytes * code_count * 18 / 1024**2, 2),
            }
        report["monthly_single_code_request_seconds"] = month_seconds
        checks = report["volume_amount_checks"]
        compared = [row for row in checks if "volume_abs_diff" in row]
        report["volume_amount_summary"] = {
            "comparison_count": len(compared),
            "max_abs_volume_difference": max((abs(row["volume_abs_diff"]) for row in compared), default=None),
            "max_abs_amount_difference": max((abs(row["amount_abs_diff"]) for row in compared), default=None),
            "failed_codes": [row["code"] for row in checks if row.get("status") == "failed"],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        bs.logout()


def members() -> list[str]:
    table = pq.read_table(SNAPSHOT / "universe_monthly.parquet")
    frame = table.to_pandas()
    frame["month_end"] = pd.to_datetime(frame["month_end"])
    frame = frame[frame["month_end"].between("2023-01-01", "2024-06-30")]
    return sorted(frame.loc[frame["index"].isin(["hs300", "zz500"]), "code"].unique().tolist())


def write_manifest() -> dict:
    root = ROOT / "data/raw/min5"
    output = ROOT / "data/min5"
    entries = []
    all_codes: set[str] = set()
    for path in sorted(output.glob("*/*.parquet")):
        table = pq.read_table(path)
        dates = table.column("date").to_pylist()
        file_codes = set(table.column("code").to_pylist())
        all_codes.update(file_codes)
        entries.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "rows": table.num_rows,
                "codes": len(file_codes),
                "date_min": str(min(dates)) if dates else None,
                "date_max": str(max(dates)) if dates else None,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    expected = {f"{p.year}/{p.month:02}.parquet" for p in pd.period_range(START, END, freq="M")}
    present = {Path(entry["path"]).relative_to("data/min5").as_posix() for entry in entries}
    missing = sorted(expected - present)
    all_failures = [
        row
        for path in sorted((root / "failures").glob("*.json"))
        for row in json.loads(path.read_text(encoding="utf-8"))
    ]
    manifest = {
        "created_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "start": START,
        "end": END,
        "rows": sum(entry["rows"] for entry in entries),
        "codes": len(all_codes),
        "requested_codes": len(members()),
        "files": entries,
        "failures": all_failures,
        "missing_months": [path.removesuffix(".parquet").replace("/", "-") for path in missing],
        "complete": not missing and not all_failures,
    }
    (ROOT / "manifest_min5.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (root / "failures.json").write_text(json.dumps(all_failures, ensure_ascii=False, indent=2) + "\n")
    return manifest


def fetch(month_filter: set[str] | None = None, *, emit_manifest: bool = True) -> None:
    start_time = time.monotonic()
    codes = members()
    failures: list[dict] = []
    root = ROOT / "data/raw/min5"
    output = ROOT / "data/min5"
    root.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    months = pd.period_range(START, END, freq="M")
    if month_filter is not None:
        months = [month for month in months if str(month) in month_filter]
    login()
    try:
        for month in months:
            month_failures: list[dict] = []
            month_name = f"{month.year}-{month.month:02}"
            raw_month = root / month_name
            raw_month.mkdir(exist_ok=True)
            legacy = root / f"{month_name}.jsonl"
            if legacy.exists():
                # Resume data fetched before raw files were split by code. Old writes were
                # emitted in code-sized blocks, so stream one code at a time to avoid a
                # second full-month copy in memory.
                for stale in raw_month.glob("*.jsonl"):
                    stale.unlink()
                current_code = None
                stream = None
                with legacy.open(encoding="utf-8") as source:
                    for line in source:
                        if not line.strip():
                            continue
                        row = json.loads(line)
                        code = row["code"]
                        if code != current_code:
                            if stream is not None:
                                stream.close()
                            stream = (raw_month / f"{code}.jsonl").open("w", encoding="utf-8")
                            current_code = code
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                if stream is not None:
                    stream.close()
                legacy.unlink()
            existing = {path.name.removesuffix(".jsonl") for path in raw_month.glob("*.jsonl")}
            part = [
                json.loads(line)
                for path in sorted(raw_month.glob("*.jsonl"))
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            for code in codes:
                if code in existing:
                    continue
                begin = str(month.start_time.date())
                finish = min(str(month.end_time.date()), END)
                for attempt in range(1, 4):
                    try:
                        rows, _ = query(code, begin, finish)
                        part.extend(rows)
                        raw_file = raw_month / f"{code}.jsonl"
                        temporary = raw_file.with_suffix(".jsonl.tmp")
                        with temporary.open("w", encoding="utf-8") as stream:
                            for row in rows:
                                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                        temporary.replace(raw_file)
                        break
                    except Exception as exc:
                        if attempt == 3:
                            failure = {"code": code, "month": str(month), "error": str(exc)}
                            failures.append(failure)
                            month_failures.append(failure)
                        else:
                            time.sleep(attempt)
            frame = pd.DataFrame(part)
            if not frame.empty:
                frame = frame.drop_duplicates(["code", "date", "time"]).sort_values(["code", "date", "time"])
                frame["date"] = pd.to_datetime(frame["date"]).dt.date
                frame["time"] = frame["time"].astype("string")
                for name in ("open", "high", "low", "close", "amount"):
                    frame[name] = pd.to_numeric(frame[name], errors="raise").astype("float64")
                frame["volume"] = pd.to_numeric(frame["volume"], errors="raise").astype("int64")
                dest = output / f"{month.year}/{month.month:02}.parquet"
                dest.parent.mkdir(parents=True, exist_ok=True)
                pq.write_table(
                    pa.Table.from_pandas(frame[FIELDS.split(",")], preserve_index=False),
                    dest,
                    compression="zstd",
                )
            print(
                f"{month}: rows={len(part)} codes={len({r['code'] for r in part})} failures={len(failures)}",
                flush=True,
            )
            failure_dir = root / "failures"
            failure_dir.mkdir(exist_ok=True)
            (failure_dir / f"{month}.json").write_text(
                json.dumps(month_failures, ensure_ascii=False, indent=2) + "\n"
            )
    finally:
        bs.logout()
    if not emit_manifest:
        return
    manifest = write_manifest()
    manifest["elapsed_seconds"] = round(time.monotonic() - start_time, 3)
    (ROOT / "manifest_min5.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--months", help="comma-separated YYYY-MM partitions for parallel/resumed fetches")
    parser.add_argument(
        "--manifest-only", action="store_true", help="rebuild manifest from local parquet without fetching"
    )
    args = parser.parse_args()
    if args.probe:
        probe()
    elif args.manifest_only:
        print(json.dumps(write_manifest(), ensure_ascii=False, indent=2))
    elif args.months:
        fetch(set(args.months.split(",")), emit_manifest=False)
    else:
        fetch()


if __name__ == "__main__":
    main()
