"""沪深 A 股客户交易成本按日查表。

客户实际承担 = 佣金 + 印花税 + 过户费。按 2002 年证监会规定，佣金已含
经手费和证管费，因此 other_fees=0，不重复计收。只支持 sh. / sz. A 股。
沪市 2015-08-01 前按股数×1元计过户费；旧规每笔最低 1 元在此简化中不实现。
2012-06-01 起沪市费率按本模块指定的 0.000375（而非 2015 查证表的 0.0003）。
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from q6.core.types import Side


@dataclass(frozen=True, slots=True)
class _StampEvent:
    effective_from: date
    buy: float
    sell: float
    source: str
    secondary: bool = False


@dataclass(frozen=True, slots=True)
class _TransferEvent:
    effective_from: date
    sh_face: float
    sh_turnover: float
    sz_turnover: float
    source: str
    secondary: bool = False


@dataclass(frozen=True, slots=True)
class FeeRates:
    """某一天适用的费率。"""

    stamp_buy: float
    stamp_sell: float
    transfer_sh_face: float
    transfer_turnover_sh: float
    transfer_turnover_sz: float


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    commission: float
    stamp_tax: float
    transfer_fee: float

    @property
    def total(self) -> float:
        return self.commission + self.stamp_tax + self.transfer_fee


_STAMP_EVENTS = (
    _StampEvent(
        date(1900, 1, 1),
        0.002,
        0.002,
        "https://www.sse.com.cn/aboutus/publication/factbook/documents/c/10170574/files/977f9c4f930746c18732b7d063f5676a.pdf",
        True,
    ),
    _StampEvent(
        date(2005, 1, 24),
        0.001,
        0.001,
        "https://www.sse.com.cn/aboutus/publication/factbook/documents/c/10170574/files/977f9c4f930746c18732b7d063f5676a.pdf",
        True,
    ),
    _StampEvent(
        date(2007, 5, 30),
        0.003,
        0.003,
        "https://www.chinatax.gov.cn/chinatax/n810341/n810765/n812176/200705/c1194505/content.html",
    ),
    _StampEvent(
        date(2008, 4, 24),
        0.001,
        0.001,
        "https://www.mof.gov.cn/zhengwuxinxi/caizhengxinwen/200805/t20080519_29133.htm",
    ),
    _StampEvent(
        date(2008, 9, 19),
        0.0,
        0.001,
        "https://www.csrc.gov.cn/csrc/c100024/c1492163/1492163/files/9d50a8264f9847b8af24ff8f7afd1e53.pdf",
    ),
    _StampEvent(date(2023, 8, 28), 0.0, 0.0005, "https://m.mof.gov.cn/czxw/202308/t20230827_3904226.htm"),
)
_TRANSFER_EVENTS = (
    _TransferEvent(
        date(1900, 1, 1),
        0.0005,
        0.0,
        0.0000255,
        "https://www.csrc.gov.cn/csrc/c100028/c1002502/content.shtml",
    ),
    _TransferEvent(
        date(2012, 6, 1),
        0.000375,
        0.0,
        0.0000255,
        "https://www.csrc.gov.cn/csrc/c100028/c1002502/content.shtml",
    ),
    _TransferEvent(
        date(2015, 8, 1),
        0.0,
        0.00002,
        0.00002,
        "https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20150912_3988866.shtml",
    ),
    _TransferEvent(
        date(2022, 4, 29),
        0.0,
        0.00001,
        0.00001,
        "https://www.xinhuanet.com/2022-04/28/c_1128605983.htm",
        True,
    ),
)
_STAMP_DATES = tuple(e.effective_from for e in _STAMP_EVENTS)
_TRANSFER_DATES = tuple(e.effective_from for e in _TRANSFER_EVENTS)


def _as_date(d: date | datetime | pd.Timestamp | str) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, (pd.Timestamp, str)):
        try:
            return pd.Timestamp(d).date()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"非法日期: {d!r}") from exc
    raise ValueError(f"非法日期: {d!r}")


def rates_on(d: date | datetime | pd.Timestamp | str) -> FeeRates:
    day = _as_date(d)
    stamp = _STAMP_EVENTS[bisect.bisect_right(_STAMP_DATES, day) - 1]
    transfer = _TRANSFER_EVENTS[bisect.bisect_right(_TRANSFER_DATES, day) - 1]
    return FeeRates(stamp.buy, stamp.sell, transfer.sh_face, transfer.sh_turnover, transfer.sz_turnover)


def compute_fees(
    code: str,
    side: Side,
    d: date | datetime | pd.Timestamp | str,
    price: float,
    qty: int,
    *,
    commission_rate: float = 0.00025,
    commission_min: float = 5.0,
    cost_multiplier: float = 1.0,
) -> FeeBreakdown:
    if not isinstance(code, str) or not (code.startswith("sh.") or code.startswith("sz.")):
        raise ValueError(f"仅支持 sh./sz. A 股代码: {code!r}")
    if not isinstance(side, Side):
        raise ValueError("side 必须为 Side")
    try:
        price = float(price)
        commission_rate = float(commission_rate)
        commission_min = float(commission_min)
        cost_multiplier = float(cost_multiplier)
    except (TypeError, ValueError) as exc:
        raise ValueError("价格和费率参数必须为数值") from exc
    if qty < 0 or price <= 0 or not math.isfinite(price):
        raise ValueError("qty 不得为负，price 必须为正有限数")
    if (
        not math.isfinite(commission_rate)
        or commission_rate < 0
        or not math.isfinite(commission_min)
        or commission_min < 0
    ):
        raise ValueError("commission_rate/min 必须为非负有限数")
    if not math.isfinite(cost_multiplier) or cost_multiplier < 0:
        raise ValueError("cost_multiplier 必须为非负有限数")
    if qty == 0:
        return FeeBreakdown(0.0, 0.0, 0.0)
    day_rates = rates_on(d)
    notional = price * qty
    commission = max(notional * commission_rate, commission_min) if notional else 0.0
    stamp_rate = day_rates.stamp_buy if side is Side.BUY else day_rates.stamp_sell
    stamp_tax = notional * stamp_rate
    if code.startswith("sh."):
        transfer = qty * day_rates.transfer_sh_face + notional * day_rates.transfer_turnover_sh
    else:
        transfer = notional * day_rates.transfer_turnover_sz
    return FeeBreakdown(commission * cost_multiplier, stamp_tax * cost_multiplier, transfer * cost_multiplier)
