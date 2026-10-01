"""固定市场情形区间及其收益统计。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from q6.research.metrics import summary


@dataclass(frozen=True)
class Regime:
    name: str
    start: date
    end: date
    note: str


REGIMES: tuple[Regime, ...] = (
    Regime("gfc_2008", date(2008, 1, 1), date(2008, 11, 30), "全球金融危机"),
    Regime("rebound_2009", date(2008, 12, 1), date(2009, 12, 31), "危机后反弹"),
    Regime("bull_2014", date(2014, 7, 1), date(2015, 6, 12), "牛市"),
    Regime("crash_2015", date(2015, 6, 15), date(2016, 1, 31), "股灾"),
    Regime("bear_2018", date(2018, 1, 1), date(2018, 12, 31), "熊市"),
    Regime("rebound_2019", date(2019, 1, 1), date(2019, 12, 31), "反弹"),
    Regime("covid_2020", date(2020, 1, 1), date(2020, 3, 31), "疫情冲击"),
    Regime("structural_2021", date(2021, 1, 1), date(2021, 12, 31), "结构性行情"),
    Regime("bear_2022", date(2022, 1, 1), date(2022, 12, 31), "熊市"),
    Regime("smallcap_2024", date(2024, 1, 1), date(2024, 2, 29), "小盘股波动"),
)


def slice_regime(r: pd.Series, regime: Regime) -> pd.Series:
    """按日期闭区间筛选收益序列。"""
    index = pd.to_datetime(r.index)
    mask = (index >= pd.Timestamp(regime.start)) & (index <= pd.Timestamp(regime.end))
    return r.loc[mask]


def regime_table(r: pd.Series) -> pd.DataFrame:
    """逐情形调用 metrics.summary，返回情形名索引的指标表。"""
    rows = {regime.name: {"note": regime.note, **summary(slice_regime(r, regime))} for regime in REGIMES}
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("regime")
