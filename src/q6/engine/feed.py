"""引擎的数据源：按年分段加载快照，每段带上策略声明的预热历史（P2-07）。

为什么分段：全区间 ~4700 天 × ~1600 只 × 每个字段 8 字节 ≈ 60MB / 字段，十几个字段就超过 512MB 上限。
每段 = [该年第一天之前 warmup 根 bar, 该年最后一天]，单段约 500 行，内存可控。账户状态由引擎跨段延续。

每段只读"本段内任一交易日的可投资范围里出现过的代码 ∪ 引擎要求保留的代码（持仓、在途订单）"，
在 Arrow 层按代码过滤。全市场 ~5000 只 → ~1000 只，P2-07 实测全区间峰值 RSS 从 1.05GB 降到见 PROGRESS。
策略只能在可投资范围内选股，所以范围外的股票不进面板不改变任何策略的输入。

段内所有数据都经 load_snapshot（锁箱期在加载层拒绝）和 Panel（构造时再查一次）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from q6.core.pit import Panel
from q6.data.snapshot import load_snapshot

# 撮合、除权、盯市、新股判断需要的列（不复权价 + 状态）
ENGINE_FIELDS = (
    "open", "high", "low", "close", "preclose", "volume", "amount",
    "tradestatus", "is_st", "is_new", "adj_factor", "ret", "bad",
)


@dataclass(frozen=True)
class Segment:
    panel: Panel
    first: int  # 本段要逐日推进的第一行（之前是预热历史）
    universe: np.ndarray  # (T, N) bool：该行日期的可投资范围（≤ 该日的最近月末成分）


def _universe_matrix(uni: pd.DataFrame, dates: pd.DatetimeIndex, symbols: tuple[str, ...]) -> np.ndarray:
    out = np.zeros((len(dates), len(symbols)), dtype=bool)
    if uni.empty:
        return out
    col = {s: j for j, s in enumerate(symbols)}
    month_ends = np.sort(pd.to_datetime(uni["month_end"]).unique())
    members = {m: [col[c] for c in g["code"] if c in col] for m, g in uni.groupby("month_end")}
    # 每个交易日用 ≤ 当日的最近一个月末成分（PIT）
    pos = np.searchsorted(month_ends, dates.values, side="right") - 1
    for i, p in enumerate(pos):
        if p >= 0:
            out[i, members[pd.Timestamp(month_ends[p])]] = True
    return out


class SnapshotFeed:
    def __init__(self, root: str | Path, snapshot_id: str, extra_fields: tuple[str, ...] = ()) -> None:
        self.root = Path(root)
        self.snapshot_id = snapshot_id
        self.fields = tuple(dict.fromkeys((*ENGINE_FIELDS, *extra_fields)))
        cal = load_snapshot(self.root, snapshot_id, ("calendar",))["calendar"]
        dcol = "date" if "date" in cal.columns else cal.columns[0]
        self.calendar = pd.DatetimeIndex(pd.to_datetime(cal[dcol])).sort_values()
        self.universe = load_snapshot(self.root, snapshot_id, ("universe_monthly",))["universe_monthly"]

    def _members(self, first: pd.Timestamp, last: pd.Timestamp) -> set[str]:
        """[first, last] 内任一交易日适用的月度成分（适用 = ≤ 当日的最近一个月末）。"""
        if self.universe.empty:
            return set()
        me = pd.to_datetime(self.universe["month_end"])
        earlier = me[me <= first]
        lo = earlier.max() if len(earlier) else me.min()  # lookahead: ok ≤段首日的最近月末，日期定位
        return set(self.universe.loc[(me >= lo) & (me <= last), "code"].astype(str))

    def _load_panel(self, lo: pd.Timestamp, hi: pd.Timestamp,
                    codes: Iterable[str] | None = None) -> tuple[pd.DatetimeIndex, Panel]:
        """[lo, hi] 的面板。逐年读、读完一年就写进预分配数组并释放，峰值只有"一年的读取开销 + 面板本身"。"""
        years = range(lo.year, hi.year + 1)

        def piece(y: int, cols: tuple[str, ...]) -> pd.DataFrame:
            a, b = max(lo, pd.Timestamp(y, 1, 1)), min(hi, pd.Timestamp(y, 12, 31))
            return load_snapshot(self.root, self.snapshot_id, ("daily",), years=(y, y), columns=cols,
                                 date_range=(str(a.date()), str(b.date())), codes=codes)["daily"]

        keys = [piece(y, ()) for y in years]  # 只读 date / code，先定面板的行列
        dates = pd.DatetimeIndex(sorted(set().union(*(set(k["date"]) for k in keys))))
        codes = pd.Index(sorted(set().union(*(set(k["code"].astype(str)) for k in keys))))
        del keys
        arrays = {f: np.full((len(dates), len(codes)), np.nan) for f in self.fields}
        for y in years:
            df = piece(y, self.fields)
            ri = dates.get_indexer(df["date"])
            ci = codes.get_indexer(df["code"].astype(str))
            for f in self.fields:
                arrays[f][ri, ci] = df[f].to_numpy(dtype=np.float64, na_value=np.nan)
            del df
        return dates, Panel(dates, codes, arrays, copy=False)

    def segments(self, start, end, warmup: int,
                 keep: Callable[[], Iterable[str]] | None = None) -> Iterator[Segment]:
        """keep：每段加载前调用一次，返回必须带上的代码（引擎传入持仓 + 在途订单）。生成器是惰性的，
        所以 keep 看到的是上一段跑完之后的账户。"""
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        cal = self.calendar[(self.calendar <= end)]
        run_days = cal[cal >= start]
        if not len(run_days):
            raise ValueError(f"{start.date()}–{end.date()} 没有交易日")
        for year in sorted(set(run_days.year)):
            days = run_days[run_days.year == year]
            i0 = int(cal.get_loc(days[0]))
            lo = cal[max(0, i0 - warmup)]
            codes = self._members(days[0], days[-1]) | set(keep() if keep is not None else ())
            dates, panel = self._load_panel(lo, days[-1], codes)
            first = int(dates.get_indexer([days[0]])[0])
            if first < 0:
                raise RuntimeError(f"{days[0].date()} 在日历里但快照没有任何行")
            yield Segment(panel, first, _universe_matrix(self.universe, dates, panel.symbols))
            del panel
