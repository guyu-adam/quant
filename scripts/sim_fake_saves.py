#!/usr/bin/env python3
"""Create deterministic, realistic-sized dashboard fixtures in a temporary saves tree."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from q6.sim import layout


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--saves", default="/tmp/q6-p3-07-saves")
    args = p.parse_args()
    s = Path(args.saves).resolve()
    for d in ("runs", "archive", "hb", "logs"):
        (s / d).mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    workers = []
    for index in range(3):
        run_id = f"demo-{index + 1}"
        db = s / "runs" / f"{run_id}.sqlite"
        db.unlink(missing_ok=True)
        with sqlite3.connect(db) as conn:
            conn.executescript(layout.SCHEMA)
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('status','running')")
            conn.execute(
                "INSERT OR REPLACE INTO meta VALUES ('spec',?)",
                (json.dumps({"strategy": "momentum", "initial_cash": 1_000_000, "fixture": True}),),
            )
            start = date(2015, 1, 1)
            daily = []
            for day in range(3000):
                equity = 1_000_000 + day * 115 + ((day % 37) - 18) * 950
                daily.append(
                    (
                        (start + timedelta(days=day)).isoformat(),
                        equity,
                        equity * 0.2,
                        equity * 0.8,
                        4,
                        25000,
                        22000,
                        25,
                        4,
                    )
                )
            conn.executemany("INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?)", daily)
            fills = [
                (
                    seq,
                    (start + timedelta(days=seq % 3000)).isoformat(),
                    f"order-{seq}",
                    f"600{seq % 10:03d}.SH",
                    "BUY" if seq % 2 else "SELL",
                    100,
                    10 + seq % 100 / 10,
                    5,
                    0,
                    0,
                    0,
                    1,
                    1,
                )
                for seq in range(2000)
            ]
            conn.executemany("INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", fills)
            conn.execute("INSERT OR REPLACE INTO positions VALUES ('600000.SH',1000,800,10.5,12.3)")
            conn.execute(
                "INSERT INTO events(ts,kind,msg) VALUES (?, 'fixture', 'dashboard sample event')", (now,)
            )
        (s / "logs" / f"{run_id}.log").write_text("fixture log line\n" * 12, encoding="utf-8")
        workers.append(
            {
                "run_id": run_id,
                "pid": 3000 + index,
                "state": "running",
                "restarts": 0,
                "last_exit": None,
                "started_at": now,
                "rss_mb": 90.0,
                "peak_mb": 110.0,
                "equity": 1_300_000,
                "hb_age_s": 0.4,
                "day": "2023-03-18",
                "n_days": 3000,
                "total_days": 4500,
            }
        )
        layout.write_json_atomic(
            s / "hb" / f"{run_id}.json",
            {
                "run_id": run_id,
                "pid": 3000 + index,
                "ts": datetime.now(UTC).timestamp(),
                "day": "2023-03-18",
                "n_days": 3000,
                "total_days": 4500,
                "equity": 1_300_000,
                "rss_mb": 90,
                "status": "running",
            },
        )
    layout.write_json_atomic(
        s / "supervisor.json",
        {
            "pid": 2999,
            "started_at": now,
            "updated_at": now,
            "config": "fixture.json",
            "max_workers": 12,
            "workers": workers,
            "queue": ["queued-demo"],
            "done": ["done-demo"],
            "janitor": {"removed": 0, "bytes": 0},
            "restarts_log": [],
        },
    )
    print(f"saves={s}")
    print("runs=3 daily_per_run=3000 fills_per_run=2000")


if __name__ == "__main__":
    main()
