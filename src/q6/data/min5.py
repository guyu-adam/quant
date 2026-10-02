"""Read one month of five-minute bars for a single trading day.

The returned mapping is keyed by stock code. Each value is a dict of NumPy
arrays (time, open, high, low, close, volume, amount), in ascending time order.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.dataset as ds

from q6.data import lockbox


def load_min5(
    root: str | Path, day: str, codes: list[str] | tuple[str, ...]
) -> dict[str, dict[str, np.ndarray]]:
    """Return the requested day's bars by code, reading only its monthly parquet.

    Each mapping value contains string ``time`` values (HHMMSS) and float64
    prices/amount plus int64 volume arrays. Codes absent from that day are omitted.
    """
    lockbox.check_dates([day], "load_min5")
    date = pa.scalar(pd_day(day), type=pa.date32())
    path = Path(root) / day[:4] / f"{day[5:7]}.parquet"
    if not path.is_file() or not codes:
        return {}
    table = ds.dataset(path, format="parquet").to_table(
        filter=(ds.field("date") == date) & ds.field("code").isin(list(codes)),
        columns=["date", "time", "code", "open", "high", "low", "close", "volume", "amount"],
    )
    table = table.sort_by([("code", "ascending"), ("time", "ascending")])
    data = table.to_pydict()
    out: dict[str, dict[str, np.ndarray]] = {}
    for code in dict.fromkeys(data["code"]):
        indices = [i for i, value in enumerate(data["code"]) if value == code]
        out[code] = {
            "time": np.asarray([data["time"][i] for i in indices], dtype=str),
            **{
                name: np.asarray(
                    [data[name][i] for i in indices],
                    dtype=np.int64 if name == "volume" else np.float64,
                )
                for name in ("open", "high", "low", "close", "volume", "amount")
            },
        }
    return out


def pd_day(day: str):
    """Parse a date string to a Python date for Arrow date32 predicate pushdown."""
    from datetime import date

    return date.fromisoformat(day)
