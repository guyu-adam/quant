"""AkShare 日线备份数据源。

新浪 ``stock_zh_a_daily`` 和腾讯历史接口返回的成交量单位为股；东财历史接口
成交量单位为手，转换为股。
接口依次尝试新浪、腾讯、东财；每个接口先直连再使用配置的 HTTPS 代理。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import akshare as ak
import pandas as pd

_COLUMN_MAP = {
    "日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
    "最低": "low", "成交量": "volume", "成交额": "amount",
    "振幅": "amplitude", "涨跌幅": "pctChg", "涨跌额": "change", "换手率": "turn",
    "开盘价": "open", "收盘价": "close", "最高价": "high", "最低价": "low",
    "成交额(元)": "amount", "成交量(股)": "volume", "成交量(手)": "volume",
}
_FIELDS = ["date", "code", "open", "high", "low", "close", "volume", "amount", "turn", "pctChg"]


def _call_with_route(
    fn: Callable[..., pd.DataFrame], kwargs: dict[str, Any], proxy: str | None
) -> pd.DataFrame:
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            if proxy:
                os.environ[key] = proxy
            else:
                os.environ.pop(key, None)
        return fn(**kwargs)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _normalize(raw: pd.DataFrame, code: str, source: str, volume_is_lots: bool) -> pd.DataFrame:
    out = raw.rename(columns=_COLUMN_MAP).copy()
    if "date" not in out:
        raise ValueError(f"{source} 响应缺少日期列")
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out["code"] = code
    for column in ("open", "high", "low", "close", "volume", "amount", "turn", "pctChg"):
        if column in out:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    if "volume" in out and volume_is_lots:
        out["volume"] *= 100
    out = out.reindex(columns=_FIELDS)
    out.attrs["source"] = source
    return out


def daily(code: str, start: str, end: str) -> pd.DataFrame:
    """读取未复权日线，成交量统一为股。

    新浪（真实样例 2024-01-02 浦发银行：22,066,700 股）和腾讯成交量单位为股；
    东财单位为手并乘 100。每个源先直连、再走 ``HTTPS_PROXY``，全部失败时报出各源错误。
    """
    symbol = code.split(".", maxsplit=1)[-1]
    start_date = pd.Timestamp(start).strftime("%Y%m%d")
    end_date = pd.Timestamp(end).strftime("%Y%m%d")
    sources: list[tuple[str, Callable[..., pd.DataFrame], dict[str, Any], bool]] = [
        (
            "新浪", ak.stock_zh_a_daily,
            {"symbol": f"{code[:2]}{symbol}", "start_date": start_date,
             "end_date": end_date, "adjust": ""}, False,
        ),
        (
            "腾讯", ak.stock_zh_a_hist_tx,
            {"symbol": symbol, "start_date": start_date,
             "end_date": end_date, "adjust": ""}, False,
        ),
        (
            "东财", ak.stock_zh_a_hist,
            {"symbol": symbol, "period": "daily", "start_date": start_date,
             "end_date": end_date, "adjust": ""}, True,
        ),
    ]
    errors: list[str] = []
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    for source, fn, kwargs, lots in sources:
        for route, route_proxy in (("直连", None), ("代理", proxy)):
            if route == "代理" and not route_proxy:
                errors.append(f"{source}/{route}: 未配置 HTTPS_PROXY")
                continue
            try:
                raw = _call_with_route(fn, kwargs, route_proxy)
                if raw is None or raw.empty:
                    raise ValueError("接口返回空数据")
                return _normalize(raw, code, source, lots)
            except Exception as exc:
                errors.append(f"{source}/{route}: {type(exc).__name__}: {exc}")
    raise RuntimeError("所有 AkShare 数据源均失败：" + "；".join(errors))
