"""Point-in-Time 视图（P1-13）：策略唯一能看到的数据入口。

语义：
- Panel 是一份完整的 (T × N) 面板，按日期升序。它只在引擎 / 研究层手里，不交给策略。
- PITView 绑定一个游标 t（行号）。"在 t 根 bar 收盘后做决定"——能看到 ≤t 的所有行。
  某个字段若有发布滞后（例如财务数据），用 field_lag 声明，视图对它只暴露 ≤ t-lag 的行。
- 每根 bar 由引擎新建一个 PITView。视图不可前移：策略即使把旧视图存下来，也拿不到之后的数据。
- 所有返回值都是**拷贝**。numpy 的切片视图可以通过 `.base` 拿到整块底层数组，那就等于把未来
  数据交出去了，所以这里不返回切片视图。

这一层只是"结构防线"：Python 没有真正的私有属性，故意去读 `_panel` 是挡不住的（由
lint/lookahead_ast 扫描 `._panel` / `._data` 访问）。真正的保证来自 lint/truncation_test。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np
import pandas as pd


class Panel:
    """只读面板。fields: 字段名 → (T, N) 数组；dates: (T,) datetime64[ns]，严格递增。"""

    __slots__ = ("_dates", "_symbols", "_fields", "_lags", "_sym_index")

    def __init__(
        self,
        dates: Iterable,
        symbols: Iterable[str],
        fields: Mapping[str, np.ndarray],
        field_lag: Mapping[str, int] | None = None,
    ) -> None:
        d = pd.DatetimeIndex(pd.to_datetime(list(dates) if not isinstance(dates, pd.Index) else dates))
        if d.hasnans:
            raise ValueError("dates 含 NaT")
        if len(d) > 1 and not (d[1:] > d[:-1]).all():
            raise ValueError("dates 必须严格递增（不允许重复或乱序）")
        syms = tuple(str(s) for s in symbols)
        if len(set(syms)) != len(syms):
            raise ValueError("symbols 有重复")
        T, N = len(d), len(syms)
        frozen: dict[str, np.ndarray] = {}
        for name, arr in fields.items():
            a = np.array(arr, copy=True)  # 自己持有一份，外部再改原数组不影响
            if a.shape != (T, N):
                raise ValueError(f"字段 {name} 形状 {a.shape} ≠ ({T}, {N})")
            a.setflags(write=False)
            frozen[name] = a
        lags = dict(field_lag or {})
        for name, k in lags.items():
            if name not in frozen:
                raise KeyError(f"field_lag 指定了不存在的字段 {name}")
            if int(k) != k or k < 0:
                raise ValueError(f"字段 {name} 的滞后必须是非负整数，得到 {k}")
        self._dates = d.values.astype("datetime64[ns]")
        self._dates.setflags(write=False)
        self._symbols = syms
        self._fields = frozen
        self._lags = {n: int(lags.get(n, 0)) for n in frozen}
        self._sym_index = {s: i for i, s in enumerate(syms)}

    @classmethod
    def from_frames(cls, frames: Mapping[str, pd.DataFrame],
                    field_lag: Mapping[str, int] | None = None) -> Panel:
        """frames: 字段名 → DataFrame(index=日期, columns=代码)。所有字段必须同索引同列。"""
        it = iter(frames.values())
        first = next(it)
        for name, df in frames.items():
            if not df.index.equals(first.index) or not df.columns.equals(first.columns):
                raise ValueError(f"字段 {name} 的索引/列与其它字段不一致")
        return cls(first.index, first.columns,
                   {k: v.to_numpy(dtype=np.float64) for k, v in frames.items()}, field_lag)

    @classmethod
    def from_long(
        cls,
        df: pd.DataFrame,
        fields: Iterable[str],
        *,
        date_col: str = "date",
        code_col: str = "code",
        dtype=np.float64,
        field_lag: Mapping[str, int] | None = None,
    ) -> Panel:
        """快照长表（每行一个 股票×日期）→ 面板。

        缺行（未上市 / 已退市 / 数据源缺）填 NaN；bool 字段转成 0/1 浮点，缺行同样是 NaN，
        由使用方决定 NaN 的含义（例如 tradable 为 NaN 视为不可交易）。
        """
        fields = list(fields)
        if df.duplicated([date_col, code_col]).any():
            raise ValueError("长表存在重复的 (date, code)")
        dates = pd.DatetimeIndex(pd.to_datetime(df[date_col]).unique()).sort_values()
        codes = pd.Index(df[code_col].astype(str).unique()).sort_values()
        ri = dates.get_indexer(pd.to_datetime(df[date_col]))
        ci = codes.get_indexer(df[code_col].astype(str))
        arrays = {}
        for f in fields:
            a = np.full((len(dates), len(codes)), np.nan, dtype=dtype)
            a[ri, ci] = df[f].to_numpy(dtype=dtype, na_value=np.nan)
            arrays[f] = a
        return cls(dates, codes, arrays, field_lag)

    def __len__(self) -> int:
        return len(self._dates)

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(self._fields)

    def date_at(self, t: int) -> np.datetime64:
        return self._dates[t]

    def index_of(self, date) -> int:
        """date 当天（或之前最近一个）bar 的行号；早于第一行抛 KeyError。"""
        i = int(np.searchsorted(self._dates, np.datetime64(pd.Timestamp(date), "ns"), side="right")) - 1
        if i < 0:
            raise KeyError(f"{date} 早于面板起点")
        return i

    def view(self, t: int) -> PITView:
        """仅供引擎 / 研究层调用。"""
        return PITView(self, t)


class PITView:
    """绑定在游标 t 上的只读视图。所有方法返回的数据行号 ≤ t（字段有滞后时 ≤ t-lag）。"""

    __slots__ = ("_panel", "_t")

    def __init__(self, panel: Panel, t: int) -> None:
        t = int(t)
        if not 0 <= t < len(panel):
            raise IndexError(f"游标 {t} 越界 [0, {len(panel)})")
        self._panel = panel
        self._t = t

    # ---- 元信息 ----
    @property
    def t(self) -> int:
        """当前游标（行号）。等于"已经走过的 bar 数 - 1"。"""
        return self._t

    @property
    def now(self) -> pd.Timestamp:
        return pd.Timestamp(self._panel._dates[self._t])

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._panel._symbols

    @property
    def field_names(self) -> tuple[str, ...]:
        return self._panel.field_names

    def n_available(self, name: str) -> int:
        """该字段当前可见的行数（考虑滞后后，可能为 0）。"""
        return max(0, self._end(name))

    # ---- 数据访问 ----
    def _end(self, name: str) -> int:
        """该字段可见区间的右端（开区间行号）。"""
        if name not in self._panel._fields:
            raise KeyError(f"未知字段 {name}；可用：{self.field_names}")
        return self._t + 1 - self._panel._lags[name]

    def _rows(self, name: str, lookback: int | None) -> slice:
        end = self._end(name)
        if end <= 0:
            return slice(0, 0)
        if lookback is None:
            return slice(0, end)
        if lookback <= 0:
            raise ValueError("lookback 必须为正整数")
        return slice(max(0, end - int(lookback)), end)

    def window(self, name: str, lookback: int | None = None,
               symbols: Iterable[str] | None = None) -> np.ndarray:
        """最近 lookback 行（不足则有多少给多少），形状 (L, N) 的拷贝。lookback=None 表示全部历史。"""
        rows = self._rows(name, lookback)
        arr = self._panel._fields[name]
        if symbols is None:
            return arr[rows].copy()
        cols = [self._panel._sym_index[s] for s in symbols]
        return arr[rows][:, cols].copy()

    def latest(self, name: str) -> np.ndarray:
        """最新一行可见数据 (N,) 的拷贝；无可见数据时返回全 NaN。"""
        end = self._end(name)
        if end <= 0:
            return np.full(len(self.symbols), np.nan)
        return self._panel._fields[name][end - 1].copy()

    def dates(self, lookback: int | None = None, name: str | None = None) -> pd.DatetimeIndex:
        """与 window(name, lookback) 行对齐的日期。name=None 时按无滞后计算。"""
        if name is None:
            end = self._t + 1
            rows = slice(0, end) if lookback is None else slice(max(0, end - int(lookback)), end)
        else:
            rows = self._rows(name, lookback)
        return pd.DatetimeIndex(self._panel._dates[rows].copy())

    def frame(self, name: str, lookback: int | None = None) -> pd.DataFrame:
        """window 的 DataFrame 版本（index=日期，columns=代码）。"""
        return pd.DataFrame(self.window(name, lookback), index=self.dates(lookback, name),
                            columns=list(self.symbols))

    def __repr__(self) -> str:
        return f"PITView(t={self._t}, now={self.now.date()}, N={len(self.symbols)})"


def iter_views(panel: Panel, start: int = 0, stop: int | None = None):
    """按时间顺序逐根产生视图（引擎 / 研究层用）。"""
    stop = len(panel) if stop is None else stop
    for t in range(start, stop):
        yield PITView(panel, t)
