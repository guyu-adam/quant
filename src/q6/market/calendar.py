"""Trading day calendar backed by an immutable NumPy date array."""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _date(value: Any) -> np.datetime64:
    """Normalize supported date inputs to a day-resolution NumPy date."""
    if isinstance(value, (str, date, datetime, pd.Timestamp, np.datetime64)):
        return np.datetime64(pd.Timestamp(value).date(), "D")
    raise TypeError(f"unsupported date value: {type(value).__name__}")


class TradingCalendar:
    """A sorted, deduplicated, read-only collection of trading days."""

    def __init__(self, trading_days: Iterable[Any]) -> None:
        days = np.array(sorted({_date(day) for day in trading_days}), dtype="datetime64[D]")
        days.setflags(write=False)
        self._days = days

    @classmethod
    def from_csv(cls, path: str | Path) -> TradingCalendar:
        frame = pd.read_csv(path)
        if "calendar_date" not in frame:
            raise ValueError("CSV must contain calendar_date")
        if "is_trading_day" in frame:
            flag = frame["is_trading_day"].astype(str).str.lower()
            frame = frame[flag.isin(("1", "true"))]
        return cls(frame["calendar_date"])

    def to_parquet(self, path: str | Path) -> None:
        pd.DataFrame({"calendar_date": self._days}).to_parquet(path, index=False)

    @classmethod
    def from_parquet(cls, path: str | Path) -> TradingCalendar:
        frame = pd.read_parquet(path)
        if "calendar_date" not in frame:
            raise ValueError("Parquet file must contain calendar_date")
        return cls(frame["calendar_date"])

    @classmethod
    def from_baostock(cls, start: Any, end: Any) -> TradingCalendar:
        import baostock as bs

        start_date = str(pd.Timestamp(start).date())
        end_date = str(pd.Timestamp(end).date())
        last_error = "unknown error"
        for attempt in range(3):
            try:
                login = bs.login()
                if login.error_code != "0":
                    raise RuntimeError(f"baostock login failed: {login.error_code} {login.error_msg}")
                result = bs.query_trade_dates(start_date=start_date, end_date=end_date)
                if result.error_code != "0":
                    raise RuntimeError(f"baostock query failed: {result.error_code} {result.error_msg}")
                rows = []
                while result.next():
                    rows.append(result.get_row_data())
                return cls(row[0] for row in rows if row[1] == "1")
            except Exception as exc:
                last_error = str(exc)
                if attempt < 2:
                    time.sleep(0.25 * (attempt + 1))
            finally:
                try:
                    bs.logout()
                except Exception:
                    pass
        raise RuntimeError(f"baostock request failed after 3 attempts: {last_error}")

    def is_trading_day(self, d: Any) -> bool:
        day = _date(d)
        index = int(np.searchsorted(self._days, day))
        return index < len(self._days) and bool(self._days[index] == day)

    def next(self, d: Any, n: int = 1) -> pd.Timestamp:
        if n < 1:
            raise ValueError("n must be a positive integer")
        index = int(np.searchsorted(self._days, _date(d), side="right")) + n - 1
        return self._at(index)

    def prev(self, d: Any, n: int = 1) -> pd.Timestamp:
        if n < 1:
            raise ValueError("n must be a positive integer")
        index = int(np.searchsorted(self._days, _date(d), side="left")) - n
        return self._at(index)

    def range(self, start: Any, end: Any) -> pd.DatetimeIndex:
        left, right = _date(start), _date(end)
        if right < left:
            return pd.DatetimeIndex([])
        lo = int(np.searchsorted(self._days, left, side="left"))
        hi = int(np.searchsorted(self._days, right, side="right"))
        return pd.DatetimeIndex(self._days[lo:hi])

    def offset(self, d: Any, n: int) -> pd.Timestamp:
        day = _date(d)
        index = int(np.searchsorted(self._days, day))
        if index == len(self._days) or self._days[index] != day:
            raise ValueError(f"{pd.Timestamp(day).date()} is not a trading day")
        return self._at(index + n)

    @property
    def first(self) -> pd.Timestamp:
        return self._at(0)

    @property
    def last(self) -> pd.Timestamp:
        return self._at(len(self._days) - 1)

    def _at(self, index: int) -> pd.Timestamp:
        if index < 0 or index >= len(self._days):
            raise IndexError("trading calendar index out of range")
        return pd.Timestamp(self._days[index])
