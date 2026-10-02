"""策略④ ML 排序（P2-18，Cen）：LightGBM lambdarank 对截面排序，取前 n 只等权。

训练与推理用**同一个特征函数** `feature_matrix(view, universe_mask, history)`：
- 截面 = 当日可选股范围（universe ∩ 交易中 ∩ 非 ST ∩ 非新股 ∩ 后复权收盘价有限）。只在这些列上算特征，
  所以截面类特征（Alpha101 里的 cs_rank 等）不受面板里多带的列（引擎持仓 keep 进来的代码）影响。
- 输入口径逐项复刻 `alpha.inputs.build_inputs`（停牌 / bad 行置 NaN、后复权成交量、vwap、流通市值近似），
  单测逐元素比对。
- 只读 `history` 行窗口：最长回看是 mom_12_1 的 252 日，history=260 足够。

训练集：`build_feature_cache` 一次性按每 `sample_every` 个交易日抽样算特征，写到磁盘缓存（特征只用 ≤t 的数据，
P2-18b 截断测试已验证）。`load_feature_cache` 读出后用 `lockbox.check_dates` 过一遍——在 walk-forward 的
`research_horizon(train_end)` 里读到训练窗口之后的行会直接抛 HorizonError，缓存不构成绕过。
标签（`ml.labels.forward_rank_label`，t+1 收盘到 t+1+h 收盘的截面排名）在 `fit_window` 里**在研究视界内**
现读收盘价计算：标签区间越过 train_end 的样本拿不到数据，自然是 NaN 并被丢掉（视界内的 purge）。

模型参数在 `RankerConfig` 里写死，P2 不调参
（规定动作：调参必须走 walk-forward 内的 CV 并计入登记簿的试验数）。

macOS（Intel）上 pip 版 lightgbm 依赖 libomp（`@rpath/libomp.dylib`），本机没有 Homebrew 的 libomp。
`load_lightgbm` 在导入失败时预加载一个现成的 libomp（`Q6_LIBOMP` 环境变量或 Anaconda 自带的那份，
install name 相同），不改系统、不装软件；仍失败则抛 ImportError（测试不跳过）。
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from q6.data import lockbox
from q6.ml.features import FEATURE_NAMES, build_features
from q6.ml.labels import forward_rank_label
from q6.strategy.base import BarContext, StrategyBase, StrategySpec

# 特征输入（build_inputs 的列）+ 选股范围需要的列
FIELDS = ("open_hfq", "high_hfq", "low_hfq", "close_hfq", "ret", "amount", "turn", "volume", "adj_factor",
          "tradestatus", "bad", "is_st", "is_new")
HISTORY = 260

_LIBOMP_CANDIDATES = (
    "~/opt/anaconda3/lib/libomp.dylib",
    "~/anaconda3/lib/libomp.dylib",
    "~/miniconda3/lib/libomp.dylib",
    "/usr/local/opt/libomp/lib/libomp.dylib",
    "/opt/homebrew/opt/libomp/lib/libomp.dylib",
)


def load_lightgbm():
    try:
        import lightgbm
        return lightgbm
    except OSError as first:
        if sys.platform != "darwin":
            raise
        paths = [os.environ["Q6_LIBOMP"]] if os.environ.get("Q6_LIBOMP") else []
        paths += [str(Path(p).expanduser()) for p in _LIBOMP_CANDIDATES]
        for p in paths:
            if Path(p).is_file():
                ctypes.CDLL(p, mode=ctypes.RTLD_GLOBAL)
                import lightgbm
                return lightgbm
        raise ImportError(f"lightgbm 加载失败且找不到可用的 libomp（试过 {paths}）；"
                          "设 Q6_LIBOMP 指向 libomp.dylib") from first


@dataclass(frozen=True)
class RankerConfig:
    horizon: int = 20  # 标签：t+1 收盘到 t+1+20 收盘
    n_bins: int = 5  # lambdarank 的相关度等级：截面百分位等分 5 档（0..4）
    n_estimators: int = 200
    learning_rate: float = 0.05
    num_leaves: int = 31
    min_data_in_leaf: int = 200
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    bagging_freq: int = 1
    lambda_l2: float = 1.0
    seed: int = 42
    num_threads: int = 2  # 资源上限：项目 ≤4G，训练只占两个核

    def lgb_params(self) -> dict[str, Any]:
        return dict(objective="lambdarank", learning_rate=self.learning_rate, num_leaves=self.num_leaves,
                    min_data_in_leaf=self.min_data_in_leaf, feature_fraction=self.feature_fraction,
                    bagging_fraction=self.bagging_fraction, bagging_freq=self.bagging_freq,
                    lambda_l2=self.lambda_l2, seed=self.seed, deterministic=True, force_row_wise=True,
                    num_threads=self.num_threads, verbose=-1)


# ---------------------------------------------------------------- 特征（训练与推理共用）
def eligible_mask(view, universe_mask: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return (np.asarray(universe_mask, dtype=bool)
                & (view.latest("tradestatus") == 1)
                & (view.latest("is_st") != 1)
                & (view.latest("is_new") != 1)
                & np.isfinite(view.latest("close_hfq")))


def feature_matrix(view, universe_mask: np.ndarray, history: int = HISTORY) -> tuple[list[str], np.ndarray]:
    """（代码列表, X float32 形状 (n, 75)）：view 当日可选股票的特征，列序 = FEATURE_NAMES。"""
    elig = eligible_mask(view, universe_mask)
    codes = [view.symbols[j] for j in np.flatnonzero(elig)]
    if not codes:
        return [], np.empty((0, len(FEATURE_NAMES)), dtype=np.float32)
    dates = view.dates(history)
    w = {f: view.window(f, history, symbols=codes) for f in
         ("open_hfq", "high_hfq", "low_hfq", "close_hfq", "ret", "amount", "turn", "volume", "adj_factor",
          "tradestatus", "bad")}
    with np.errstate(divide="ignore", invalid="ignore"):
        # 同 build_inputs：停牌（tradestatus==0）或 bad 的行整行置 NaN；
        # bad 缺失按 bad 处理（astype(bool) 口径）
        bad = np.where(np.isnan(w["bad"]), True, w["bad"] != 0)
        valid = (w["tradestatus"] != 0) & ~bad
        volume_hfq = w["volume"] / np.where(w["adj_factor"] != 0, w["adj_factor"], np.nan)
        vwap = w["amount"] / np.where(volume_hfq != 0, volume_hfq, np.nan)
        float_cap = w["amount"] / np.where(w["turn"] > 0, w["turn"] / 100, np.nan)
    raw = {"open": w["open_hfq"], "high": w["high_hfq"], "low": w["low_hfq"], "close": w["close_hfq"],
           "ret": w["ret"], "amount": w["amount"], "turn": w["turn"], "volume": volume_hfq, "vwap": vwap,
           "float_cap": float_cap}
    inputs = {k: pd.DataFrame(np.where(valid, v, np.nan), index=dates, columns=codes) for k, v in raw.items()}
    feats = build_features(inputs)
    x = np.column_stack([feats[name].iloc[-1].to_numpy(dtype=np.float64) for name in FEATURE_NAMES])
    if np.isinf(x).any():
        bad_cols = [FEATURE_NAMES[k] for k in np.flatnonzero(np.isinf(x).any(axis=0))]
        raise ValueError(f"{view.now.date()} 特征含 inf：{bad_cols}")
    return codes, x.astype(np.float32)


# ---------------------------------------------------------------- 特征缓存
def _source_hash() -> str:
    """缓存键的一部分：特征相关源码改了，旧缓存自动失效。"""
    root = Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    for rel in ("ml/lgbm_ranker.py", "ml/features.py", "alpha/library.py", "alpha/ops.py"):
        h.update((root / rel).read_bytes())
    return h.hexdigest()[:12]


def cache_dir(base: str | Path, snapshot_id: str, start, end, sample_every: int,
              history: int = HISTORY) -> Path:
    key = f"{snapshot_id}_{pd.Timestamp(start).date()}_{pd.Timestamp(end).date()}_e{sample_every}_h{history}"
    return Path(base) / f"{key}_{_source_hash()}"


def build_feature_cache(feed, start, end, out: str | Path, *, sample_every: int = 10,
                        history: int = HISTORY) -> Path:
    """每 sample_every 个交易日（从 start 起第 0、sample_every、…个）算一次特征，按年写 parquet。
    view 不足 history 行的日期跳过（和引擎"满 warmup 才调用策略"一致）。已存在且完整的缓存直接返回。"""
    out = Path(out)
    meta_path = out / "meta.json"
    if meta_path.exists():
        return out
    out.mkdir(parents=True, exist_ok=True)
    k = 0
    for seg in feed.segments(start, end, history):
        p = seg.panel
        rows: list[pd.DataFrame] = []
        for i in range(seg.first, len(p)):
            take = k % sample_every == 0
            k += 1
            if not take or i + 1 < history:
                continue
            view = p.view(i)
            codes, x = feature_matrix(view, seg.universe[i], history)
            df = pd.DataFrame(x, columns=list(FEATURE_NAMES))
            df.insert(0, "code", codes)
            df.insert(0, "date", view.now)
            rows.append(df)
        if rows:
            year = pd.Timestamp(p.date_at(seg.first)).year
            pd.concat(rows, ignore_index=True).to_parquet(out / f"year={year}.parquet", index=False)
        seg = p = rows = None  # noqa: F841
    meta = dict(snapshot_id=getattr(feed, "snapshot_id", None), start=str(pd.Timestamp(start).date()),
                end=str(pd.Timestamp(end).date()), sample_every=sample_every, history=history,
                features=list(FEATURE_NAMES), source_hash=_source_hash())
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return out


def load_feature_cache(path: str | Path, lo, hi) -> pd.DataFrame:
    """[lo, hi] 内的缓存行（date, code, 特征…），按 (date, code) 排序。受锁箱期 / 研究视界约束。"""
    path = Path(path)
    if not (path / "meta.json").exists():
        raise FileNotFoundError(f"{path} 不是完整的特征缓存（缺 meta.json）")
    lo, hi = pd.Timestamp(lo), pd.Timestamp(hi)
    parts = []
    for y in range(lo.year, hi.year + 1):
        f = path / f"year={y}.parquet"
        if f.exists():
            df = pd.read_parquet(f)
            parts.append(df[(df["date"] >= lo) & (df["date"] <= hi)])
    if not parts:
        return pd.DataFrame(columns=["date", "code", *FEATURE_NAMES])
    df = pd.concat(parts, ignore_index=True).sort_values(["date", "code"], kind="mergesort")
    lockbox.check_dates(df["date"], "特征缓存")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------- 训练
def load_close_hfq(feed, lo, hi) -> pd.DataFrame:
    """[lo, hi] 的后复权收盘价宽表（经 SnapshotFeed 读取，受锁箱期 / 研究视界约束）。"""
    parts = []
    for seg in feed.segments(lo, hi, 1):
        p = seg.panel
        idx = pd.DatetimeIndex([p.date_at(i) for i in range(seg.first, len(p))])
        block = p.block("close_hfq", seg.first, len(p))
        parts.append(pd.DataFrame(block, index=idx, columns=list(p.symbols)))
    return pd.concat(parts, axis=0, join="outer").sort_index()


def training_set(feats: pd.DataFrame, close_hfq: pd.DataFrame, horizon: int, n_bins: int
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray, pd.DataFrame]:
    """（X, 相关度等级 y, 每个日期的样本数 group, 保留下来的 (date, code, label)）。
    标签的截面排名只在当天进训练集的股票（feats 里那一天的行）之间做。"""
    codes = sorted(set(feats["code"]) & set(close_hfq.columns))
    close = close_hfq.reindex(columns=codes)
    f = feats[feats["code"].isin(codes) & feats["date"].isin(close.index)]
    mask = np.zeros(close.shape, dtype=bool)
    mask[close.index.get_indexer(f["date"]), close.columns.get_indexer(f["code"])] = True
    elig = pd.DataFrame(mask, index=close.index, columns=codes)
    rank, _ = forward_rank_label(close, horizon, eligible=elig)
    lab = rank.values[rank.index.get_indexer(f["date"]), rank.columns.get_indexer(f["code"])]
    keep = np.isfinite(lab)
    f = f.loc[keep]
    lab = lab[keep]
    y = np.minimum(np.floor(lab * n_bins), n_bins - 1).astype(np.int32)  # pct ∈ (0,1] → 0..n_bins-1
    x = f[list(FEATURE_NAMES)].to_numpy(dtype=np.float32)
    group = f.groupby("date", sort=True).size().to_numpy()
    kept = f[["date", "code"]].assign(label=lab)
    return x, y, group, kept.reset_index(drop=True)


def fit_ranker(x: np.ndarray, y: np.ndarray, group: np.ndarray, cfg: RankerConfig):
    lgb = load_lightgbm()
    ds = lgb.Dataset(x, y, group=group, feature_name=list(FEATURE_NAMES), free_raw_data=True)
    return lgb.train(cfg.lgb_params(), ds, num_boost_round=cfg.n_estimators)


def fit_window(feed, cache: str | Path, train_start, train_end, cfg: RankerConfig | None = None):
    """walk-forward 的 fit：只用 [train_start, train_end] 的特征和收盘价。
    应在 research_horizon(train_end) 内调用。"""
    cfg = cfg or RankerConfig()
    feats = load_feature_cache(cache, train_start, train_end)
    close = load_close_hfq(feed, train_start, train_end)
    x, y, group, _ = training_set(feats, close, cfg.horizon, cfg.n_bins)
    if not len(y):
        raise ValueError(f"[{train_start}, {train_end}] 没有可用训练样本")
    return fit_ranker(x, y, group, cfg)


# ---------------------------------------------------------------- 策略
class LGBMRankerStrategy(StrategyBase):
    """每 every 个 bar：对可选股票算特征 → 模型打分 → 取前 n 只等权，合计 gross。

    例外说明：构造参数 `model`（训练好的 Booster，或任何有 predict(X) 的对象）不是标量——它是 walk-forward 在
    训练窗口内拟合的结果，不含任何测试期数据（fit 在 research_horizon 内执行）。"""

    name = "lgbm_ranker"

    def __init__(self, model, n: int = 50, every: int = 20, gross: float = 0.95,
                 history: int = HISTORY) -> None:
        if n <= 0 or every <= 0 or history <= 0 or not 0 < gross <= 0.95:
            raise ValueError("n/every/history 必须为正，gross ∈ (0, 0.95]")
        self.model, self.n, self.every, self.gross, self.history = model, n, every, float(gross), history
        self._calls = 0

    @property
    def spec(self) -> StrategySpec:
        return StrategySpec(fields=FIELDS, warmup=self.history,
                            params={"n": self.n, "every": self.every, "gross": self.gross,
                                    "history": self.history})

    def on_bar(self, ctx: BarContext) -> dict[str, float] | None:
        self._calls += 1
        if (self._calls - 1) % self.every:
            return None
        codes, x = feature_matrix(ctx.view, ctx.universe_mask(), self.history)
        if not codes:
            return {}
        score = np.asarray(self.model.predict(x), dtype=np.float64)
        order = np.argsort(-score, kind="stable")[: self.n]
        w = self.gross / len(order)
        return {codes[j]: w for j in order}


def config_dict(cfg: RankerConfig) -> dict[str, Any]:
    return asdict(cfg)
