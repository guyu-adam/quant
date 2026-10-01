"""A 股交易规则（P2-03）：板块、涨跌停价、手数、T+1。撮合和两个引擎都只从这里取规则。

涨跌停（沪深交易所交易规则）：涨跌停价 = 前收盘价 × (1 ± 比例)，四舍五入到 0.01 元。
**一律用不复权的交易所 preclose**（除权日的 preclose 已是除权参考价）。计算全程用整数分，避免浮点舍入误差
（例如 4.45×1.1 在浮点下是 4.8950000000000005，碰巧对；1.235 这类边界就不一定）。

| 板块 | 普通 | ST / *ST | 新股 |
|---|---|---|---|
| 沪深主板（含原中小板） | 10% | 5% | 首日无比例限制（注 1）；2023-04-10 注册制后新上市前 5 日无限制 |
| 创业板 | 2020-08-24 前 10%，起 20% | 2020-08-24 前 5%，起 20% | 2020-08-24 起新上市前 5 日无限制；之前首日 |
| 科创板（2019-07-22 开市） | 20% | 20% | 前 5 日无限制 |

注 1：2014 年起主板首日有 +44% / −36%（相对发行价）的约束，本模块不建模。

不在本模块内的特例（数量极少，引擎通过"上市未满 N 日不交易"、"隔离表"排除）：股改复牌首日、退市整理期、
暂停上市后恢复上市首日。北交所（bj.）不在研究范围内，传入直接报错。

**锁箱期之后的规则变化尚未查证**：日期 ≥ 2024-07-01 时 limit_pct 抛 NotImplementedError，P4 锁箱期运行前必须
查官方公告补齐（不读锁箱期数据，只查规则文本）。
"""

from __future__ import annotations

from datetime import date
from enum import Enum

import numpy as np
import pandas as pd

from q6.data.lockbox import LOCKBOX_START

CHINEXT_REFORM = date(2020, 8, 24)  # 创业板注册制首批上市，涨跌幅改为 20%
STAR_OPEN = date(2019, 7, 22)  # 科创板开市
MAIN_REGISTRATION = date(2023, 4, 10)  # 主板注册制首批上市
RULES_VERIFIED_UNTIL = LOCKBOX_START  # 本模块的规则表只查证到锁箱期起点之前
LOT = 100
STAR_MIN_BUY = 200


class Board(Enum):
    MAIN = "main"
    CHINEXT = "chinext"
    STAR = "star"


def board_of(code: str) -> Board:
    """按代码前缀判断板块。代码格式 sh.600000 / sz.000001。"""
    market, _, num = code.partition(".")
    if market == "sh" and num[:3] in {"600", "601", "603", "605"}:
        return Board.MAIN
    if market == "sh" and num[:3] in {"688", "689"}:
        return Board.STAR
    if market == "sz" and num[:3] in {"000", "001", "002", "003"}:
        return Board.MAIN
    if market == "sz" and num[:3] in {"300", "301"}:
        return Board.CHINEXT
    raise ValueError(f"不支持的代码 {code!r}（只处理沪深主板、创业板、科创板 A 股）")


def _day(d) -> date:
    return pd.Timestamp(d).date()


def _check_verified(day: date) -> None:
    if day >= RULES_VERIFIED_UNTIL:
        raise NotImplementedError(
            f"{day} 的涨跌停规则未查证（规则表只覆盖到 {RULES_VERIFIED_UNTIL} 之前），P4 前补齐"
        )


def no_limit_days(board: Board, listing_date) -> int:
    """上市后前几个交易日不设涨跌幅比例限制（1 = 只有首日）。"""
    listed = _day(listing_date)
    if board is Board.STAR:
        return 5
    if board is Board.CHINEXT:
        return 5 if listed >= CHINEXT_REFORM else 1
    return 5 if listed >= MAIN_REGISTRATION else 1


def limit_pct(board: Board, is_st: bool, d) -> int:
    """涨跌幅比例（整数百分比）。不处理新股前几日（见 no_limit_days）。"""
    day = _day(d)
    _check_verified(day)
    if board is Board.STAR:
        if day < STAR_OPEN:
            raise ValueError(f"科创板 {day} 尚未开市")
        return 20
    if board is Board.CHINEXT and day >= CHINEXT_REFORM:
        return 20
    return 5 if is_st else 10


def _to_cents(preclose: np.ndarray) -> np.ndarray:
    cents = np.rint(np.asarray(preclose, dtype=np.float64) * 100.0)
    if not np.all(np.isfinite(cents)) or np.any(cents <= 0):
        raise ValueError("preclose 必须是正的有限数")
    if np.any(np.abs(cents - np.asarray(preclose, dtype=np.float64) * 100.0) > 1e-6):
        raise ValueError("preclose 不是整分价格")
    return cents.astype(np.int64)


def limit_prices_cents(preclose_cents: np.ndarray, pct: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """整数分运算：返回 (涨停价分, 跌停价分)。四舍五入（half-up）到分。"""
    p = np.asarray(preclose_cents, dtype=np.int64)
    k = np.asarray(pct, dtype=np.int64)
    up = (p * (100 + k) + 50) // 100
    down = (p * (100 - k) + 50) // 100
    return up, down


def limit_prices(preclose, pct) -> tuple[np.ndarray, np.ndarray]:
    """向量化：preclose（元）与整数百分比 → (涨停价, 跌停价)（元）。"""
    up, down = limit_prices_cents(_to_cents(np.atleast_1d(preclose)), np.atleast_1d(pct))
    return up / 100.0, down / 100.0


def limit_price(code: str, d, preclose: float, *, is_st: bool) -> tuple[float, float]:
    """单只股票当日的 (涨停价, 跌停价)。"""
    up, down = limit_prices(preclose, limit_pct(board_of(code), is_st, d))
    return float(up[0]), float(down[0])


def limit_pct_array(codes, is_st, dates) -> np.ndarray:
    """向量化 limit_pct：codes / is_st / dates 同长度（逐行）。"""
    codes = np.asarray(codes, dtype=object)
    st = np.asarray(is_st, dtype=bool)
    days = pd.DatetimeIndex(pd.to_datetime(np.asarray(dates)))
    late = np.asarray(days >= pd.Timestamp(RULES_VERIFIED_UNTIL))
    if late.any():
        _check_verified(days[late][0].date())  # 逐行守卫，不是统计量
    boards = np.array([board_of(c).value for c in codes], dtype=object)
    reform = np.asarray(days >= pd.Timestamp(CHINEXT_REFORM))
    if np.any((boards == Board.STAR.value) & np.asarray(days < pd.Timestamp(STAR_OPEN))):
        raise ValueError("科创板代码出现在开市日之前")
    pct = np.where(st, 5, 10)
    pct = np.where((boards == Board.CHINEXT.value) & reform, 20, pct)
    pct = np.where(boards == Board.STAR.value, 20, pct)
    return pct.astype(np.int64)


def round_buy_qty(code: str, qty: int) -> int:
    """把想买的股数向下取整到合法数量；不足最小买入单位返回 0。

    主板 / 创业板：100 股整数倍。科创板：不少于 200 股，超过部分可按 1 股递增。
    """
    if qty < 0:
        raise ValueError("qty 不能为负")
    if board_of(code) is Board.STAR:
        return int(qty) if qty >= STAR_MIN_BUY else 0
    return int(qty) // LOT * LOT


def round_sell_qty(code: str, qty: int, sellable: int) -> int:
    """把想卖的股数调整为合法数量。不足一手的零股只能一次性全部卖出。"""
    if qty < 0 or sellable < 0:
        raise ValueError("数量不能为负")
    qty = min(int(qty), int(sellable))
    if qty == sellable:
        return qty  # 全部卖出（含零股）总是合法
    if board_of(code) is Board.STAR:
        # 科创板卖出：不少于 200 股（余额不足 200 股时须一次性卖出，已由上面的全部卖出覆盖）
        return qty if qty >= STAR_MIN_BUY else 0
    return qty // LOT * LOT

