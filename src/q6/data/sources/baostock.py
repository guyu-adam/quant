"""Baostock client with raw string responses and typed conversion helpers."""

from __future__ import annotations

import time
from typing import Literal

import baostock as bs
import pandas as pd

DAILY_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,"
    "tradestatus,pctChg,isST"
)


class BaostockError(RuntimeError):
    def __init__(self, code: str, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"Baostock error {code}: {msg}")


class BaostockClient:
    def __init__(self, min_interval: float = 0.2):
        self.min_interval = min_interval
        self._logged_in = False
        self._last_request: float | None = None

    def __enter__(self) -> BaostockClient:
        self._login()
        return self

    def __exit__(self, *_: object) -> None:
        self._logout()

    def _login(self) -> None:
        result = bs.login()
        if result.error_code != "0":
            raise BaostockError(result.error_code, result.error_msg)
        self._logged_in = True

    def _logout(self) -> None:
        if self._logged_in:
            bs.logout()
            self._logged_in = False

    def _wait_rate_limit(self) -> None:
        if self._last_request is not None:
            delay = self.min_interval - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)

    def query(self, fn_name: str, **kwargs: object) -> pd.DataFrame:
        fn = getattr(bs, fn_name)
        consecutive_failures = 0
        last_error: Exception | None = None
        # Initial attempt plus at most four retries, with 1/2/4/8 second backoff.
        for attempt in range(5):
            self._wait_rate_limit()
            self._last_request = time.monotonic()
            try:
                result = fn(**kwargs)
                if result.error_code != "0":
                    raise BaostockError(result.error_code, result.error_msg)
                fields = list(result.fields)
                rows: list[list[str]] = []
                while result.next():
                    rows.append(result.get_row_data())
                return pd.DataFrame(rows, columns=fields, dtype="string")
            except (BaostockError, OSError, ConnectionError, TimeoutError) as exc:
                last_error = exc
                consecutive_failures += 1
                if attempt == 4:
                    break
                if consecutive_failures == 2:
                    self._logout()
                    self._login()
                    consecutive_failures = 0
                time.sleep(2**attempt)
        assert last_error is not None
        raise last_error

    def daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        return self.query(
            "query_history_k_data_plus",
            code=code,
            fields=DAILY_FIELDS,
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="3",
        )

    def adjust_factor(self, code: str, start: str, end: str) -> pd.DataFrame:
        return self.query("query_adjust_factor", code=code, start_date=start, end_date=end)

    def all_stock(self, day: str) -> pd.DataFrame:
        return self.query("query_all_stock", day=day)

    def index_members(self, index: Literal["hs300", "zz500"], date: str) -> pd.DataFrame:
        fn = {"hs300": "query_hs300_stocks", "zz500": "query_zz500_stocks"}[index]
        return self.query(fn, date=date)

    def trade_dates(self, start: str, end: str) -> pd.DataFrame:
        return self.query("query_trade_dates", start_date=start, end_date=end)


def to_typed(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "date" in out:
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
    if "code" in out:
        out["code"] = out["code"].astype("string")
    for col in ("open", "high", "low", "close", "preclose", "amount", "turn", "pctChg"):
        if col in out:
            out[col] = pd.to_numeric(out[col].replace("", pd.NA), errors="coerce").astype("float64")
    if "volume" in out:
        out["volume"] = pd.to_numeric(out["volume"].replace("", pd.NA), errors="coerce").astype("Int64")
    for col in ("tradestatus", "isST"):
        if col in out:
            out[col] = pd.to_numeric(out[col].replace("", pd.NA), errors="coerce").fillna(0).astype("int8")
    return out
