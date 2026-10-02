"""Robustness grids for vector-engine strategies and execution assumptions."""

from __future__ import annotations

import itertools
import multiprocessing
import sys
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace

import pandas as pd
import psutil

from q6.engine.event import EngineConfig
from q6.engine.feed import SnapshotFeed
from q6.engine.vector import VectorEngine, strategy_weights
from q6.research import metrics
from q6.strategy.base import Strategy


@dataclass(frozen=True)
class Variant:
    label: str
    strategy_factory: Callable[[], Strategy]
    cfg: EngineConfig


def grid(base: EngineConfig, **axes) -> list[EngineConfig]:
    allowed_match = {"cost_multiplier", "impact_coef", "slippage_bp", "limit_fill_frac", "max_participation"}
    allowed_engine = {"delist_recovery"}
    unknown = set(axes) - allowed_match - allowed_engine
    if unknown:
        raise ValueError(f"unknown sensitivity axes: {sorted(unknown)}")
    keys = list(axes)
    configs = []
    for values in itertools.product(*(axes[k] for k in keys)):
        selected = dict(zip(keys, values, strict=True))
        match_changes = {k: v for k, v in selected.items() if k in allowed_match}
        engine_changes = {k: v for k, v in selected.items() if k in allowed_engine}
        configs.append(replace(base, match=replace(base.match, **match_changes), **engine_changes))
    return configs


def _rss_mb() -> float:
    info = psutil.Process().memory_info()
    if sys.platform == "win32":
        # resource 模块只有 POSIX 有；Windows 用 psutil 的峰值工作集（字节）
        peak_bytes = info.peak_wset
    else:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes; Linux reports KiB.
        peak_bytes = peak if sys.platform == "darwin" else peak * 1024
    return max(info.rss, peak_bytes) / (1024 * 1024)


def _run_group(payload):
    variants, snapshot_root, snapshot_id, start, end, feed_factory = payload
    strategies = [v.strategy_factory() for v in variants]
    fields = tuple(sorted({field for strategy in strategies for field in strategy.spec.fields}))
    feed = (
        feed_factory()
        if feed_factory is not None
        else SnapshotFeed(snapshot_root, snapshot_id, extra_fields=fields)
    )
    weight_fns = {}
    jobs = []
    for strategy, variant in zip(strategies, variants, strict=True):
        key = (variant.strategy_factory, tuple(sorted(strategy.spec.params.items())))
        if key not in weight_fns:
            raw_weights = strategy_weights(strategy)
            cache = {"segment": None, "weights": None}

            def shared_weights(seg, raw=raw_weights, state=cache):
                if state["segment"] is not seg:
                    state["weights"] = raw(seg)
                    state["segment"] = seg
                return state["weights"]

            weight_fns[key] = shared_weights
        jobs.append((weight_fns[key], strategy.spec.warmup, variant.cfg))
    results = VectorEngine().run_many(jobs, feed, start, end)
    peak = _rss_mb()
    rows = []
    for variant, strategy, result in zip(variants, strategies, results, strict=True):
        returns = result.returns
        equity = result.daily["equity"]
        years = len(equity) / 244
        cagr = (equity.iloc[-1] / result.initial_cash) ** (1 / years) - 1 if years else float("nan")
        turnover_fraction = result.daily["buy_value"].sum() / equity.mean()  # lookahead: ok 全区间评估统计
        annual_turnover = turnover_fraction / years if years else float("nan")
        rows.append(
            {
                "label": variant.label,
                **dict(strategy.spec.params),
                "cost_multiplier": variant.cfg.match.cost_multiplier,
                "impact_coef": variant.cfg.match.impact_coef,
                "slippage_bp": variant.cfg.match.slippage_bp,
                "limit_fill_frac": variant.cfg.match.limit_fill_frac,
                "max_participation": variant.cfg.match.max_participation,
                "delist_recovery": variant.cfg.delist_recovery,
                "cagr": cagr,
                "ann_vol": float(returns.std(ddof=1) * (244**0.5)),  # lookahead: ok 全区间评估统计
                "sharpe": metrics.sharpe(returns, rf=0),
                "max_drawdown": metrics.max_drawdown(returns),
                "fees": float(result.daily["fees"].sum()),
                "slippage_impact": float(result.daily["cost_slip_imp"].sum()),
                "annual_one_way_turnover": float(annual_turnover),
                "fills": len(result.fills),
                "delistings": len(result.delistings),
                "peak_rss_mb": peak,
            }
        )
    return rows


def run_grid(
    variants, snapshot_root, snapshot_id, start, end, workers=2, feed_factory=None, batch_size=8
) -> pd.DataFrame:
    if workers < 1 or workers > 3:
        raise ValueError("workers must be between 1 and 3")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    groups: dict[tuple[str, ...], list[tuple[int, Variant]]] = {}
    for i, variant in enumerate(variants):
        fields = tuple(sorted(variant.strategy_factory().spec.fields))
        groups.setdefault(fields, []).append((i, variant))
    # Keep field groups together, but cap the number of variants loaded in each
    # worker. The order inside each group is the original variant order.
    payloads = [
        group[offset : offset + batch_size]
        for group in groups.values()
        for offset in range(0, len(group), batch_size)
    ]
    if workers == 1:
        outputs = [
            _run_group(([v for _, v in entries], snapshot_root, snapshot_id, start, end, feed_factory))
            for entries in payloads
        ]
    else:
        with ProcessPoolExecutor(
            max_workers=workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            futures = [
                pool.submit(
                    _run_group,
                    ([v for _, v in entries], snapshot_root, snapshot_id, start, end, feed_factory),
                )
                for entries in payloads
            ]
            outputs = [future.result() for future in futures]
    rows = [None] * len(variants)
    for entries, output in zip(payloads, outputs, strict=True):
        for (index, _variant), row in zip(entries, output, strict=True):
            rows[index] = row
    return pd.DataFrame(rows)
