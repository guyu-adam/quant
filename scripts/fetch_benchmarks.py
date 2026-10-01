"""抓取沪深指数价格日线并生成可校验清单。

Baostock 提供的是价格指数，不含分红，会低估基准收益。
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from q6.data.sources.baostock import BaostockClient, to_typed  # noqa: E402

START = "2005-01-01"
END = "2026-09-30"
INDEX_CODES = {
    "sh.000300": "沪深300",
    "sh.000906": "中证800",
    "sh.000905": "中证500",
    "sh.000001": "上证综指",
}
COLUMNS = ["date", "code", "open", "high", "low", "close", "preclose", "volume", "amount"]


def main() -> None:
    root = REPO_ROOT / "data/benchmarks"
    root.mkdir(parents=True, exist_ok=True)
    frames = []
    with BaostockClient() as client:
        for code in INDEX_CODES:
            frame = to_typed(client.daily(code, START, END))
            missing = set(COLUMNS) - set(frame.columns)
            if missing:
                raise ValueError(f"{code} 缺少字段: {sorted(missing)}")
            frames.append(frame[COLUMNS])
    data = pd.concat(frames, ignore_index=True).sort_values(["date", "code"]).reset_index(drop=True)
    if data.empty:
        raise RuntimeError("Baostock 未返回任何指数日线")
    path = root / "index_daily.parquet"
    data.to_parquet(path, index=False, compression="zstd")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "source": "Baostock query_history_k_data_plus; frequency=d; adjustflag=3",
        "note": "价格指数，不含分红，会低估基准收益。",
        "requested_start": START,
        "requested_end": END,
        "fetched_at": datetime.now(UTC).isoformat(),
        "file": "index_daily.parquet",
        "sha256": digest,
        "rows": len(data),
        "indices": {
            code: {
                "name": INDEX_CODES[code],
                "rows": int(len(part)),
                "start": part.date.min().strftime("%Y-%m-%d"),
                "end": part.date.max().strftime("%Y-%m-%d"),
            }
            for code, part in data.groupby("code", sort=True)
        },
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for code, stats in manifest["indices"].items():
        print(f"{code} {stats['name']}: rows={stats['rows']} start={stats['start']} end={stats['end']}")
    print(f"total rows={len(data)} sha256={digest} bytes={path.stat().st_size}")


if __name__ == "__main__":
    main()
