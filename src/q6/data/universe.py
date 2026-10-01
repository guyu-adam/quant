"""中证 800 月度历史成分的采集与时点查询。"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from q6.data.sources.baostock import BaostockClient
from q6.market.calendar import TradingCalendar

UNIVERSE_COLUMNS = ["month_end", "code", "index", "update_date"]
INDEX_NAMES = ("hs300", "zz500")


def empty_universe() -> pd.DataFrame:
    """返回具有规定列和时间类型的空月度成分表。"""
    return pd.DataFrame(
        {
            "month_end": pd.Series(dtype="datetime64[ns]"),
            "code": pd.Series(dtype="string"),
            "index": pd.Series(dtype="string"),
            "update_date": pd.Series(dtype="datetime64[ns]"),
        }
    )


def month_ends(start: str, end: str, calendar: TradingCalendar) -> list[pd.Timestamp]:
    """使用交易日历生成区间内每个自然月最后一个交易日。"""
    first, last = pd.Period(start, freq="M"), pd.Period(end, freq="M")
    result = []
    for period in pd.period_range(first, last, freq="M"):
        days = calendar.range(period.start_time, period.end_time)
        if len(days):
            result.append(pd.Timestamp(days[-1]).normalize())
    return result


def collect_month(client: BaostockClient, month_end: str | pd.Timestamp) -> pd.DataFrame:
    """采集单个月末的两类指数成分并保留 Baostock 更新日期。"""
    end = pd.Timestamp(month_end).strftime("%Y-%m-%d")
    records: list[dict[str, object]] = []
    for index_name in INDEX_NAMES:
        frame = client.index_members(index_name, end)
        if frame.empty:
            continue
        if "code" not in frame or "updateDate" not in frame:
            raise ValueError(f"Baostock {index_name} 响应缺少 code/updateDate: {list(frame.columns)}")
        for row in frame[["code", "updateDate"]].dropna(subset=["code"]).itertuples(index=False):
            records.append(
                {
                    "month_end": pd.Timestamp(month_end).normalize(),
                    "code": str(row.code),
                    "index": index_name,
                    "update_date": pd.to_datetime(row.updateDate, errors="coerce"),
                }
            )
    return normalize_universe(pd.DataFrame(records, columns=UNIVERSE_COLUMNS))


def normalize_universe(frame: pd.DataFrame) -> pd.DataFrame:
    """规范成分表类型、去重并按规格排序。"""
    if frame.empty:
        return empty_universe()
    out = frame.loc[:, UNIVERSE_COLUMNS].copy()
    out["month_end"] = pd.to_datetime(out["month_end"]).astype("datetime64[ns]")
    out["code"] = out["code"].astype("string")
    out["index"] = out["index"].astype("string")
    out["update_date"] = pd.to_datetime(out["update_date"], errors="coerce").astype("datetime64[ns]")
    return out.drop_duplicates().sort_values(UNIVERSE_COLUMNS[:3], kind="stable").reset_index(drop=True)


def members_asof(universe_df: pd.DataFrame, date: str | pd.Timestamp) -> list[str]:
    """返回不晚于指定日期的最近月末中两指数成分代码并集。"""
    if universe_df.empty:
        return []
    cutoff = pd.Timestamp(date).normalize()
    eligible = universe_df[pd.to_datetime(universe_df["month_end"]) <= cutoff]
    if eligible.empty:
        return []
    latest = eligible["month_end"].max()  # lookahead: ok 已先按 date 截断，只取不晚于查询日的月末
    return sorted(eligible.loc[eligible["month_end"] == latest, "code"].dropna().astype(str).unique())


def collect_range(
    client: BaostockClient,
    dates: Iterable[pd.Timestamp],
    existing: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """采集尚未写入的月末，供断点续跑使用。"""
    if existing is not None and not existing.empty:
        accumulated = normalize_universe(existing.copy())
    else:
        accumulated = empty_universe()
    done = set(pd.to_datetime(accumulated["month_end"]).dt.normalize())
    chunks = [accumulated]
    for day in dates:
        normalized = pd.Timestamp(day).normalize()
        if normalized not in done:
            chunks.append(collect_month(client, normalized))
    return normalize_universe(pd.concat(chunks, ignore_index=True))
