"""AkShare 日线备份数据源。"""

from __future__ import annotations

import time

import akshare as ak
import pandas as pd

_COLUMN_MAP = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
    "涨跌幅": "pctChg",
    "涨跌额": "change",
    "换手率": "turn",
}


def daily(code: str, start: str, end: str) -> pd.DataFrame:
    """读取未复权日线，成交量统一为股；最多尝试三次，每次限时 20 秒。"""
    symbol = code.split(".", maxsplit=1)[-1]
    start_date = pd.Timestamp(start).strftime("%Y%m%d")
    end_date = pd.Timestamp(end).strftime("%Y%m%d")
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            raw = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="",
                timeout=20,
            )
            out = raw.rename(columns=_COLUMN_MAP).copy()
            out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
            out["code"] = code
            for column in ("open", "high", "low", "close", "volume", "amount", "turn", "pctChg"):
                if column in out:
                    out[column] = pd.to_numeric(out[column], errors="coerce")
            if "volume" in out:
                out["volume"] = out["volume"] * 100
            wanted = ["date", "code", "open", "high", "low", "close", "volume", "amount", "turn", "pctChg"]
            return out.reindex(columns=wanted)
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1)
    assert last_error is not None
    raise last_error
