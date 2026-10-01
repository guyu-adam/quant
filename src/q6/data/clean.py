"""日线清洗（P1-10）：原始长表 → 带标记的干净长表 + 隔离表。

原则：**只标记，不静默修正。** 异常 bar 进隔离表并标为不可交易，价格不改；停牌行保留；
上市不满 N 个交易日的新股打标记，由宇宙 / 策略层决定是否剔除。

输入列（baostock 日线，adjustflag=3 不复权，见 docs/research/datasource_probe.md）：
    date, code, open, high, low, close, preclose, volume, amount, tradestatus, isST  [, turn, pctChg]
输出追加列：
    tradable   当日能否成交（未停牌、成交量>0、不在隔离表）
    is_st      ST / *ST
    listed_days 自本数据第一行起的交易日序号（1 = 上市首日，仅当数据从上市日开始时成立）
    is_new     listed_days <= new_stock_days
    bad        进了隔离表
    adj_factor / *_hfq / ret   见 adjust.py
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from q6.data.adjust import add_back_adjusted, adjusted_return

REQUIRED = ("date", "code", "open", "high", "low", "close", "preclose",
            "volume", "amount", "tradestatus", "isST")

# 单日涨跌幅超过这个值且不是上市初期 / 复牌首日的 bar 视为可疑。A 股最宽涨跌幅为 ±30%（北交所），
# 主板新股首日 44%、注册制新股前 5 日不设限，故对上市初期和复牌日豁免。
_MAX_ABS_RET = 0.31


@dataclass(frozen=True)
class CleanResult:
    data: pd.DataFrame  # 全部行（含停牌、含隔离行，隔离行 bad=True / tradable=False）
    quarantine: pd.DataFrame  # 隔离明细：code, date, reason

    def summary(self) -> dict[str, int]:
        d = self.data
        return {
            "rows": len(d),
            "codes": int(d["code"].nunique()),
            "suspended_rows": int((d["tradestatus"] != 1).sum()),
            "quarantined_rows": int(d["bad"].sum()),
            "st_rows": int(d["is_st"].sum()),
            "new_stock_rows": int(d["is_new"].sum()),
        }


def clean_daily(raw: pd.DataFrame, *, new_stock_days: int = 60,
                trading_days: pd.DatetimeIndex | None = None) -> CleanResult:
    missing = [c for c in REQUIRED if c not in raw.columns]
    if missing:
        raise ValueError(f"原始数据缺列：{missing}")
    df = raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["code"] = df["code"].astype(str)
    for c in ("open", "high", "low", "close", "preclose", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(np.float64)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df["tradestatus"] = pd.to_numeric(df["tradestatus"], errors="coerce").fillna(0).astype(np.int8)
    df["isST"] = pd.to_numeric(df["isST"], errors="coerce").fillna(0).astype(np.int8)

    dup = df.duplicated(["code", "date"], keep=False)
    if dup.any():
        # 重复行无法判断哪条可信：整组隔离之前先报错，让抓取层修，不在这里猜
        sample = df.loc[dup, ["code", "date"]].head(5).to_dict("records")
        raise ValueError(f"原始数据有 {int(dup.sum())} 行重复 (code, date)，例如 {sample}")
    df = df.sort_values(["code", "date"], kind="mergesort").reset_index(drop=True)

    reasons: list[pd.DataFrame] = []

    def flag(mask: pd.Series, reason: str) -> None:
        if mask.any():
            reasons.append(df.loc[mask, ["code", "date"]].assign(reason=reason))

    trading = df["tradestatus"] == 1
    o, h, lo, c, pc = (df[k] for k in ("open", "high", "low", "close", "preclose"))
    flag(df[["open", "high", "low", "close", "preclose"]].isna().any(axis=1) & trading, "价格缺失")
    flag(trading & ((o <= 0) | (h <= 0) | (lo <= 0) | (c <= 0) | (pc <= 0)), "价格非正")
    flag(trading & (h < lo), "high<low")
    flag(trading & ((o > h) | (o < lo) | (c > h) | (c < lo)), "开收盘价超出高低价区间")
    flag(trading & (df["volume"].isna() | (df["volume"] < 0) | (df["amount"] < 0)), "量额非法")
    flag(trading & (df["volume"] > 0) & (df["amount"] <= 0), "有量无额")
    if trading_days is not None:
        flag(~df["date"].isin(trading_days), "非交易日")

    seq = df.groupby("code", sort=False).cumcount() + 1
    prev_status = df.groupby("code", sort=False)["tradestatus"].shift(1)
    resumed = trading & (prev_status == 0)  # 复牌首日：可能叠加除权或长期停牌后补涨跌，不设上限
    with np.errstate(divide="ignore", invalid="ignore"):
        r = c / pc - 1
    flag(trading & (seq > 5) & ~resumed & (r.abs() > _MAX_ABS_RET), f"单日涨跌幅超过 {_MAX_ABS_RET:.0%}")

    q = (pd.concat(reasons, ignore_index=True) if reasons
         else pd.DataFrame({"code": pd.Series(dtype=str), "date": pd.Series(dtype="datetime64[ns]"),
                            "reason": pd.Series(dtype=str)}))
    q = q.groupby(["code", "date"], as_index=False)["reason"].agg("; ".join)
    bad_key = pd.MultiIndex.from_frame(q[["code", "date"]])
    df["bad"] = pd.MultiIndex.from_frame(df[["code", "date"]]).isin(bad_key)

    df["is_st"] = df["isST"] == 1
    df["listed_days"] = seq.astype(np.int32)
    df["is_new"] = df["listed_days"] <= new_stock_days
    df["tradable"] = trading & (df["volume"] > 0) & ~df["bad"]

    # 复权只在价格可信的行上算：隔离行与停牌行的价格不参与因子推导之外的任何计算，
    # 但因子推导需要连续的 close/preclose 链，隔离行若价格缺失会被 event_ratio 视为无事件。
    df = add_back_adjusted(df)
    df["ret"] = adjusted_return(df).where(df["tradable"])
    return CleanResult(df, q.sort_values(["code", "date"]).reset_index(drop=True))
