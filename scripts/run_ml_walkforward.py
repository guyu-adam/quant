"""P2-18 策略④ ML 排序：真实快照 walk-forward（训练 3 年 / 测试 6 个月 / 步长 6 个月），样本外连续回测。

两步，各自一个进程（macOS malloc 不归还页面，分开跑 RSS 才不会叠加）：

    /usr/bin/time -l uv run python scripts/run_ml_walkforward.py --build-cache
    /usr/bin/time -l uv run python scripts/run_ml_walkforward.py

模型参数是 RankerConfig 的默认值，没有调参。区间止于锁箱期前。
"""

# ruff: noqa: I001 -- q6 必须先于 pandas 导入，才能在 pyarrow 首次加载前选定 Arrow 内存池
from __future__ import annotations

import argparse
import os
import time
from dataclasses import asdict

import q6  # noqa: F401
import numpy as np
import pandas as pd

from q6.data.benchmarks import load_benchmarks
from q6.engine.feed import ENGINE_FIELDS, SnapshotFeed
from q6.ml import lgbm_ranker as lr
from q6.ml.features import FEATURE_NAMES
from q6.registry.runs import record_run
from q6.research.benchmark import index_returns
from q6.research.walkforward import make_windows, run_walkforward

ROOT = os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots")
SNAP = os.environ.get("Q6_SNAPSHOT", "6252e931a86bda15")
START, END = "2006-01-01", "2024-06-28"
WF_START = "2007-01-01"  # 缓存里第一个有满 260 行历史的样本在 2007 年初
SAMPLE_EVERY = 10
TRADING_DAYS = 244
CACHE = lr.cache_dir("out/ml_cache", SNAP, START, END, SAMPLE_EVERY)


def feed(extra=lr.FIELDS) -> SnapshotFeed:
    return SnapshotFeed(ROOT, SNAP, extra_fields=tuple(f for f in extra if f not in ENGINE_FIELDS))


def build_cache() -> None:
    t = time.perf_counter()
    lr.build_feature_cache(feed(), START, END, CACHE, sample_every=SAMPLE_EVERY)
    n = sum(len(pd.read_parquet(p, columns=["date"])) for p in sorted(CACHE.glob("year=*.parquet")))
    sample = pd.read_parquet(sorted(CACHE.glob("year=*.parquet"))[5])
    finite = np.isfinite(sample[list(FEATURE_NAMES)].to_numpy()).mean()
    print(f"cache={CACHE} rows={n} seconds={time.perf_counter() - t:.0f} "
          f"finite_share(year file #6)={finite:.3f}")


def rank_ic(pred: np.ndarray, kept: pd.DataFrame) -> pd.Series:
    df = kept.assign(pred=pred)
    return df.groupby("date").apply(lambda g: g["pred"].rank().corr(g["label"].rank()), include_groups=False)


def main() -> None:
    cfg = lr.RankerConfig()
    data_feed, close_feed = feed(), feed(("close_hfq",))
    windows = make_windows(data_feed.calendar, WF_START, END, train_years=3, test_months=6, step_months=6,
                           purge_days=cfg.horizon + 1)
    t = time.perf_counter()
    def fit(w):
        return lr.fit_window(close_feed, CACHE, w.train_start, w.train_end, cfg)

    res = run_walkforward(windows, fit,
                          lr.LGBMRankerStrategy, data_feed)
    secs = time.perf_counter() - t

    # 每个窗口的样本外 rank IC：模型在测试期抽样日上的打分 vs 实际 t+1..t+1+h 收益排名（评估用，不进任何 fit）
    print(f"{'k':>2} {'train':>23} {'test':>23} {'ic_mean':>8} {'ic_t':>6} {'dates':>5}")
    ics = []
    for w, model in zip(res.windows, res.params, strict=True):
        feats = lr.load_feature_cache(CACHE, w.test_start, w.test_end)
        close = lr.load_close_hfq(close_feed, w.test_start, min(w.test_end + pd.Timedelta(days=60),
                                                                 pd.Timestamp(END)))
        x, _, _, kept = lr.training_set(feats, close, cfg.horizon, cfg.n_bins)
        ic = rank_ic(model.predict(x), kept) if len(kept) else pd.Series(dtype=float)
        ics.append(ic)
        tstat = ic.mean() / ic.std(ddof=1) * np.sqrt(len(ic)) if len(ic) > 1 else np.nan
        print(f"{w.k:>2} {w.train_start.date()}..{w.train_end.date()} "
              f"{w.test_start.date()}..{w.test_end.date()} "
              f"{ic.mean():>8.4f} {tstat:>6.2f} {len(ic):>5}")
    all_ic = pd.concat(ics)
    print(f"pooled OOS rank IC mean={all_ic.mean():.4f} std={all_ic.std():.4f} n={len(all_ic)} "
          f"t={all_ic.mean() / all_ic.std() * np.sqrt(len(all_ic)):.2f} (样本日间隔 {SAMPLE_EVERY} 天，标签 "
          f"{cfg.horizon} 天，相邻样本标签重叠，t 值偏高)")

    oos = res.oos
    d, r = oos.daily, oos.returns
    years = len(d) / TRADING_DAYS
    cagr = (d["equity"].iloc[-1] / oos.initial_cash) ** (1 / years) - 1
    vol = r.std(ddof=1) * np.sqrt(TRADING_DAYS)
    dd = (d["equity"] / d["equity"].cummax().clip(lower=oos.initial_cash) - 1).min()
    fees, slip = d["fees"].sum(), d["cost_slip_imp"].sum()
    turn = (d["buy_value"].sum() + d["sell_value"].sum()) / 2 / d["equity"].mean() / years
    print(f"\nOOS {d.index[0].date()}..{d.index[-1].date()} days={len(d)} windows={len(windows)} "
          f"run_seconds={secs:.0f}")
    print(f"final_equity={d['equity'].iloc[-1]:,.0f} CAGR={cagr:.2%} vol={vol:.2%} "
          f"sharpe(rf=0)={r.mean() / r.std() * np.sqrt(TRADING_DAYS):.2f} maxDD={dd:.2%}")
    print(f"fills={len(oos.fills)} fees={fees:,.0f} slip+impact={slip:,.0f} "
          f"turnover(annual, one-way)={turn:.2f}")
    print(f"reasons: {dict(sorted(oos.reasons.items()))}  delistings={len(oos.delistings)}")

    hs = index_returns(load_benchmarks(), "sh.000300").loc[d.index[0]:d.index[-1]]
    yearly = pd.DataFrame({"lgbm_ranker": (1 + r).groupby(r.index.year).prod() - 1,
                           "hs300_price": (1 + hs).groupby(hs.index.year).prod() - 1})
    print("\n" + yearly.to_string(float_format=lambda v: f"{v:+.2%}"))

    record_run(kind="walkforward", strategy="lgbm_ranker", cfg=None, snapshot_id=SNAP,
               params={"ranker": asdict(cfg), "strategy": lr.LGBMRankerStrategy(None).spec.params,
                       "windows": len(windows), "train_years": 3, "test_months": 6, "step_months": 6,
                       "purge_days": cfg.horizon + 1, "sample_every": SAMPLE_EVERY,
                       "oos": [str(d.index[0].date()), str(d.index[-1].date())]},
               metrics={"cagr": cagr, "vol": vol, "max_dd": dd, "fees": fees, "slip_impact": slip,
                        "fills": len(oos.fills), "oos_rank_ic": all_ic.mean()},
               notes="P2-18 首次 walk-forward，默认参数，未调参")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-cache", action="store_true")
    if ap.parse_args().build_cache:
        build_cache()
    else:
        main()
