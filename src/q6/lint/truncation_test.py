"""截断 / 扰动测试（P1-14）：机械化检测未来函数。

原理：一个只用"过去"数据的函数 f，其第 r 行输出只取决于输入的前 r-lag 行。所以任取切点 c：
- 截断：把输入截到 [0, c]，重算，输出前 c+1 行必须与全量运行**逐位相等**；
- 扰动：把 c 之后的输入换成噪声（形状不变），重算，输出前 c+1+lag 行必须逐位相等。
不管未来函数藏在哪（shift(-1)、center=True、全样本标准化、bfill、当根 bar 成交），只要真的用到了
c 之后的数据，至少一种变换会让 ≤c 的输出变掉。

lag 的含义：输出第 r 行允许依赖的最后一个输入行是 r-lag。
- 信号 / 因子 / 目标权重（t 收盘后算出，t+1 才成交）：lag=0。
- "第 t 根 bar 期间实际持有的仓位"：只能依赖 ≤t-1 的数据，lag=1。用当根收盘价决定当根持仓就是在
  "用当日收盘价在当日成交"，扰动 c+1 行会改变第 c+1 行仓位，从而被检出。

nan_probe：在切点那一行随机挖掉一部分值（基线与变体都挖），让 bfill 类的"用后面填前面"一定能被触发。

被测函数约定：输入为 DataFrame / Series / ndarray，或它们组成的 dict（第 0 轴是时间，各叶子行数相同）；
输出同样，第 0 轴与输入时间对齐（行数相等）。结果必须确定（同输入两次运行逐位相等），否则先报不确定。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from q6.core.pit import Panel, PITView

Data = Any  # DataFrame | Series | ndarray | Mapping[str, ...]


class LookaheadError(AssertionError):
    pass


@dataclass(frozen=True)
class Failure:
    mode: str  # "truncate" | "perturb"
    cut: int
    key: str  # 输出中的哪个叶子
    first_bad_row: int
    detail: str


@dataclass
class LookaheadReport:
    name: str
    n_rows: int
    lag: int
    cuts: list[int]
    failures: list[Failure] = field(default_factory=list)
    nondeterministic: bool = False

    @property
    def passed(self) -> bool:
        return not self.failures and not self.nondeterministic

    def __str__(self) -> str:
        head = f"[{'PASS' if self.passed else 'FAIL'}] {self.name}: rows={self.n_rows} lag={self.lag} cuts={self.cuts}"
        if self.nondeterministic:
            return head + "\n  函数输出不确定（同一输入两次运行结果不同），无法做截断测试"
        lines = [head] + [f"  {f.mode} cut={f.cut} out[{f.key}] 首个不一致行={f.first_bad_row}: {f.detail}"
                          for f in self.failures[:10]]
        if len(self.failures) > 10:
            lines.append(f"  ……共 {len(self.failures)} 处")
        return "\n".join(lines)


# ---------------------------------------------------------------- 数据树操作

def _leaves(x: Data, prefix: str = "") -> dict[str, Any]:
    if isinstance(x, Mapping):
        out: dict[str, Any] = {}
        for k, v in x.items():
            out.update(_leaves(v, f"{prefix}{k}."))
        return out
    if isinstance(x, (tuple, list)):
        out = {}
        for i, v in enumerate(x):
            out.update(_leaves(v, f"{prefix}{i}."))
        return out
    return {prefix.rstrip(".") or "<root>": x}


def _map(x: Data, fn: Callable[[Any], Any]) -> Data:
    if isinstance(x, Mapping):
        return {k: _map(v, fn) for k, v in x.items()}
    if isinstance(x, (tuple, list)):
        return type(x)(_map(v, fn) for v in x)
    return fn(x)


def _n_rows(data: Data) -> int:
    ns = {len(v) for v in _leaves(data).values()}
    if len(ns) != 1:
        raise ValueError(f"输入各叶子的行数不一致：{ns}")
    return ns.pop()


def _head(x: Any, n: int) -> Any:
    return x.iloc[:n].copy() if isinstance(x, (pd.DataFrame, pd.Series)) else np.array(x[:n], copy=True)


def _as_array(x: Any) -> np.ndarray:
    if isinstance(x, (pd.DataFrame, pd.Series)):
        return x.to_numpy()
    return np.asarray(x)


def _replace_values(x: Any, arr: np.ndarray) -> Any:
    if isinstance(x, pd.DataFrame):
        return pd.DataFrame(arr, index=x.index, columns=x.columns)
    if isinstance(x, pd.Series):
        return pd.Series(arr, index=x.index, name=x.name)
    return arr


def _perturb_leaf(x: Any, cut: int, rng: np.random.Generator) -> Any:
    a = _as_array(x).copy()
    tail = a[cut + 1:]
    if tail.size == 0:
        return _replace_values(x, a)
    if a.dtype == bool:
        a[cut + 1:] = rng.random(tail.shape) < 0.5
    elif np.issubdtype(a.dtype, np.number):
        f = tail.astype(np.float64)
        scale = np.nanstd(a[: cut + 1].astype(np.float64)) if cut >= 1 else 1.0
        scale = scale if np.isfinite(scale) and scale > 0 else 1.0
        # 乘性 + 加性噪声：既改变量级也改变符号/排序，避免恰好保持单调性而漏检
        noisy = f * np.exp(rng.normal(0.0, 0.3, f.shape)) + rng.normal(0.0, 0.5 * scale, f.shape)
        if np.issubdtype(a.dtype, np.integer):
            noisy = np.rint(np.abs(noisy))
        a[cut + 1:] = noisy.astype(a.dtype)
    else:
        return x  # 非数值（字符串等）不扰动
    return _replace_values(x, a)


def _nan_probe_leaf(x: Any, row: int, rng: np.random.Generator) -> Any:
    a = _as_array(x)
    if not np.issubdtype(a.dtype, np.floating):
        return x
    a = a.copy()
    if a.ndim == 1:
        a[row] = np.nan
    else:
        mask = rng.random(a.shape[1:]) < 0.3
        if not mask.any():
            mask.flat[0] = True
        a[row][mask] = np.nan
    return _replace_values(x, a)


def _first_diff(a: np.ndarray, b: np.ndarray, rows: int, atol: float) -> tuple[int, str] | None:
    a, b = a[:rows], b[:rows]
    if a.shape != b.shape:
        return 0, f"形状不同 {a.shape} vs {b.shape}"
    if a.dtype.kind in "fc" or b.dtype.kind in "fc":
        af, bf = a.astype(np.float64), b.astype(np.float64)
        nan_a, nan_b = np.isnan(af), np.isnan(bf)
        with np.errstate(invalid="ignore"):
            diff = np.abs(af - bf)
        bad = (nan_a != nan_b) | (~nan_a & ~nan_b & ~(diff <= atol))
        # ±inf 相等时 diff 为 nan，要单独放行
        bad &= ~(np.isinf(af) & (af == bf))
    else:
        bad = a != b
    if not bad.any():
        return None
    rows_bad = np.nonzero(bad.reshape(len(bad), -1).any(axis=1))[0]
    r = int(rows_bad[0])
    return r, f"共 {len(rows_bad)} 行不一致；第 {r} 行 全量={a[r]!r} 变体={b[r]!r}"[:300]


def _out_leaves(out: Data, n_rows: int, label: str) -> dict[str, np.ndarray]:
    leaves = {k: _as_array(v) for k, v in _leaves(out).items()}
    for k, v in leaves.items():
        if v.ndim == 0 or len(v) != n_rows:
            raise TypeError(f"{label}: 输出 {k} 的第 0 轴长度 {v.shape} 与输入行数 {n_rows} 不一致；"
                            "截断测试要求输出按时间与输入逐行对齐")
    return leaves


# ---------------------------------------------------------------- 主入口

def check_lookahead(
    fn: Callable[[Data], Data],
    data: Data,
    *,
    name: str | None = None,
    lag: int = 0,
    n_cuts: int = 6,
    min_rows: int = 30,
    seed: int = 0,
    modes: tuple[str, ...] = ("truncate", "perturb"),
    nan_probe: bool = True,
    atol: float = 0.0,
) -> LookaheadReport:
    """对 fn 做截断 / 扰动测试。默认 atol=0：要求逐位相等（只用过去数据的代码天然逐位相等）。"""
    if lag < 0:
        raise ValueError("lag 必须 ≥0")
    T = _n_rows(data)
    hi = T - 2 - lag  # 扰动模式要比较到 cut+lag 行，且 cut 之后至少留一行可扰动
    if hi < min_rows:
        raise ValueError(f"数据太短：{T} 行，min_rows={min_rows}, lag={lag}")
    rng = np.random.default_rng(seed)
    pool = np.arange(min_rows, hi + 1)
    cuts = sorted(int(c) for c in rng.choice(pool, size=min(n_cuts, len(pool)), replace=False))
    rep = LookaheadReport(name or getattr(fn, "__name__", "fn"), T, lag, cuts)

    full_raw = fn(data)
    again = fn(data)
    a1 = _out_leaves(full_raw, T, "全量")
    a2 = _out_leaves(again, T, "全量重跑")
    if a1.keys() != a2.keys() or any(_first_diff(a1[k], a2[k], T, atol) for k in a1):
        rep.nondeterministic = True
        return rep

    for cut in cuts:
        crng = np.random.default_rng([seed, cut])
        base = _map(data, lambda x: _nan_probe_leaf(x, cut, crng)) if nan_probe else data
        ref = _out_leaves(fn(base), T, "基线") if nan_probe else a1
        if "truncate" in modes:
            trunc = fn(_map(base, lambda x: _head(x, cut + 1)))
            got = _out_leaves(trunc, cut + 1, f"截断@{cut}")
            for k, v in ref.items():
                d = _first_diff(v, got.get(k, np.empty(0)), cut + 1, atol)
                if d:
                    rep.failures.append(Failure("truncate", cut, k, d[0], d[1]))
        if "perturb" in modes:
            prng = np.random.default_rng([seed, cut, 1])
            pert = fn(_map(base, lambda x: _perturb_leaf(x, cut, prng)))
            got = _out_leaves(pert, T, f"扰动@{cut}")
            for k, v in ref.items():
                d = _first_diff(v, got.get(k, np.empty(0)), cut + 1 + lag, atol)
                if d:
                    rep.failures.append(Failure("perturb", cut, k, d[0], d[1]))
    return rep


def assert_no_lookahead(fn: Callable[[Data], Data], data: Data, **kw) -> LookaheadReport:
    rep = check_lookahead(fn, data, **kw)
    if not rep.passed:
        raise LookaheadError(str(rep))
    return rep


# ---------------------------------------------------------------- PITView 策略适配

def pit_runner(make_decider: Callable[[], Callable[[PITView], Any]], *, start: int = 0,
               field_lag: Mapping[str, int] | None = None) -> Callable[[Mapping[str, pd.DataFrame]], pd.DataFrame]:
    """把"逐 bar 看 PITView 做决定"的策略包装成截断测试可用的函数。

    make_decider 每次调用返回一个**全新**的决策函数（有状态策略必须每次重建，否则两次运行互相污染）。
    决策函数返回 (N,) 数组或 {代码: 值}；start 之前的行输出 NaN。
    """

    def run(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
        panel = Panel.from_frames(frames, field_lag)
        decide = make_decider()
        syms = panel.symbols
        out = np.full((len(panel), len(syms)), np.nan)
        for t in range(start, len(panel)):
            r = decide(PITView(panel, t))
            if isinstance(r, Mapping):
                idx = {s: i for i, s in enumerate(syms)}
                for s, v in r.items():
                    out[t, idx[s]] = v
            else:
                out[t] = np.asarray(r, dtype=np.float64)
        first = next(iter(frames.values()))
        return pd.DataFrame(out, index=first.index, columns=first.columns)

    run.__name__ = getattr(make_decider, "__name__", "pit_strategy")
    return run
