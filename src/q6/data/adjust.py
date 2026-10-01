"""复权（P1-10）。

做法：用交易所公布的前收盘价 preclose 自行推导复权因子，不直接信任数据商的因子表。
交易所在除权除息日公布的 preclose 已经是"除权参考价"，所以
    除权日 t 的复权比例 = close_{t-1} / preclose_t
    后复权因子 f_t = ∏_{s≤t} close_{s-1} / preclose_s      （首行 f=1）
后复权价 = 不复权价 × f_t。f_t 只用到 ≤t 的数据 → **后复权是 PIT 安全的**，回测里的收益一律用它。

前复权价 = 不复权价 × f_t / f_anchor。anchor 之前的价格取决于 anchor 之前发生的所有除权事件，
如果 anchor 取"数据末尾"，t 时刻的前复权价就用到了 t 之后的信息（这正是很多回测的隐性未来函数）。
所以前复权只提供带显式 anchor 的版本，**仅供展示 / 与外部数据对账，不许进入信号计算**。

涨跌停、手数、成交金额一律用不复权价（rules_cn / matching 直接读原始列），复权价只用于收益。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PRICE_COLS = ("open", "high", "low", "close", "preclose")

# 相邻两日 close_{t-1} 与 preclose_t 的相对差在此以内视为"无除权事件"，比例强制取 1。
# 交易所 preclose 与上一日收盘价本应完全相等；数据源偶有分位舍入噪声，不能让噪声累积进因子。
_NO_EVENT_TOL = 1e-6


def event_ratio(close: np.ndarray, preclose: np.ndarray) -> np.ndarray:
    """单只股票按日期升序的 close / preclose → 每日除权比例 r_t = close_{t-1}/preclose_t（首行为 1）。

    停牌日 preclose 与 close 都沿用停牌前收盘价，比例为 1；除权事件落在停牌期间时，
    复牌日的 preclose 已是除权参考价，比例在复牌日体现，不会丢。
    """
    close = np.asarray(close, dtype=np.float64)
    preclose = np.asarray(preclose, dtype=np.float64)
    r = np.ones_like(close)
    if len(close) > 1:
        prev = close[:-1]
        cur = preclose[1:]
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = prev / cur
        ok = np.isfinite(ratio) & (ratio > 0)
        ratio = np.where(ok, ratio, 1.0)
        ratio = np.where(np.abs(ratio - 1.0) <= _NO_EVENT_TOL, 1.0, ratio)
        r[1:] = ratio
    return r


def back_factor(close: np.ndarray, preclose: np.ndarray) -> np.ndarray:
    """后复权累计因子（首行 = 1）。"""
    return np.cumprod(event_ratio(close, preclose))


def add_back_adjusted(df: pd.DataFrame) -> pd.DataFrame:
    """长表（code, date, open..preclose）→ 追加 adj_factor 和 *_hfq 列。要求已按 (code, date) 排序且去重。"""
    _check_sorted(df)
    out = df.copy()
    f = np.empty(len(df))
    for _, idx in df.groupby("code", sort=False).indices.items():
        f[idx] = back_factor(df["close"].to_numpy()[idx], df["preclose"].to_numpy()[idx])
    out["adj_factor"] = f
    for c in PRICE_COLS:
        if c in out:
            out[f"{c}_hfq"] = out[c].to_numpy(dtype=np.float64) * f
    return out


def adjusted_return(df: pd.DataFrame) -> pd.Series:
    """按复权口径的日收益 close_t / preclose_t - 1。

    preclose 已含除权，所以这就是含分红送转的真实持有收益；首行（上市首日）用 preclose=发行价。
    与 close_hfq.pct_change() 数学上相同，但不需要先算因子，也不受首行影响。
    """
    return df["close"] / df["preclose"] - 1.0


def forward_adjusted(df: pd.DataFrame, anchor: str | pd.Timestamp) -> pd.DataFrame:
    """前复权价（以 anchor 当日为基准 = 不复权价）。

    ⚠ 仅供展示 / 对账：anchor 之前的价格用到了 (t, anchor] 之间的除权信息。不许进信号计算。
    要求 df 已含 adj_factor（先调用 add_back_adjusted）。anchor 之后的行不受影响地按同一基准换算。
    """
    if "adj_factor" not in df:
        raise ValueError("先调用 add_back_adjusted")
    anchor = pd.Timestamp(anchor)
    out = df.copy()
    base = np.full(len(df), np.nan)
    for _, idx in df.groupby("code", sort=False).indices.items():
        d = pd.to_datetime(df["date"].to_numpy()[idx])
        k = np.searchsorted(d.values, anchor.to_datetime64(), side="right") - 1
        if k >= 0:
            base[idx] = df["adj_factor"].to_numpy()[idx][k]
    for c in PRICE_COLS:
        if c in out:
            out[f"{c}_qfq"] = out[c].to_numpy(dtype=np.float64) * out["adj_factor"].to_numpy() / base
    return out


def compare_vendor_factor(own: pd.DataFrame, vendor: pd.DataFrame, *, rtol: float = 1e-3) -> pd.DataFrame:
    """把自推因子与数据商的后复权因子做比对。

    vendor: 长表 (code, date, vendor_factor)，date 为因子生效日（除权日），之后沿用直到下一次。
    两者都只确定到一个常数倍，所以先各自归一到该股第一条可比行，再比比值。
    返回偏差超过 rtol 的行（空表 = 一致）。
    """
    v = vendor.sort_values(["code", "date"])
    m = pd.merge_asof(own.sort_values("date"), v.sort_values("date"), on="date", by="code",
                      direction="backward").sort_values(["code", "date"])
    m = m.dropna(subset=["vendor_factor"])
    first = m.groupby("code")[["adj_factor", "vendor_factor"]].transform("first")
    rel = (m["adj_factor"] / first["adj_factor"]) / (m["vendor_factor"] / first["vendor_factor"]) - 1
    m = m.assign(rel_diff=rel)
    return m.loc[rel.abs() > rtol, ["code", "date", "adj_factor", "vendor_factor", "rel_diff"]]


def _check_sorted(df: pd.DataFrame) -> None:
    key = df[["code", "date"]]
    if key.duplicated().any():
        raise ValueError("存在重复的 (code, date)")
    codes = df["code"].to_numpy()
    dates = pd.to_datetime(df["date"]).to_numpy()
    same = codes[1:] == codes[:-1]
    if (same & (dates[1:] <= dates[:-1])).any():
        raise ValueError("同一股票内日期未升序")
    # 同一 code 必须连续成段
    starts = pd.Series(codes).ne(pd.Series(codes).shift()).cumsum()
    if starts.groupby(codes).nunique().max() > 1:
        raise ValueError("同一股票的行不连续，请先按 (code, date) 排序")
