"""P3-02：在随机位置"杀掉"再续跑，结果与不中断运行逐位一致；
Mac 回测入口 EventEngine.run 与模拟盘 worker 逐位一致。"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from q6.engine.event import EngineConfig, EngineRun, EventEngine
from q6.risk.monitor import RiskConfig
from q6.sim import checkpoint as ckpt
from q6.sim import layout, worker
from q6.sim.worker import Pacer, build_strategy, engine_config
from q6.strategy.examples import LowVolEqualWeight

from ._synth import SynthFeed

START, END = "2019-02-01", "2021-02-26"


def _spec(run_id, **kw):
    return dict(run_id=run_id, strategy="lowvol", params=dict(n=3, lookback=20, every=5),
                start=START, end=END, checkpoint_every=7, **kw)


def run_worker(spec, saves, **kw):
    return worker.run_worker(spec, saves, feed=SynthFeed(), **kw)


def _reference(engine_over=None):
    s = LowVolEqualWeight(n=3, lookback=20, every=5)
    return EventEngine(engine_config(engine_over)).run(s, SynthFeed(), START, END)


def _fills(db):
    with sqlite3.connect(db) as c:
        return pd.read_sql_query("SELECT ts, order_id, symbol, side, qty, price, commission, stamp_tax,"
                                 " transfer_fee, other_fees FROM fills ORDER BY seq", c)


def _ref_fills(res):
    return pd.DataFrame([(f.ts.isoformat(), f.order_id, f.symbol, f.side.name, f.qty, f.price, f.commission,
                          f.stamp_tax, f.transfer_fee, f.other_fees) for f in res.fills],
                        columns=["ts", "order_id", "symbol", "side", "qty", "price", "commission",
                                 "stamp_tax", "transfer_fee", "other_fees"])


def _assert_same(db, ref):
    got = ckpt.read_daily(db)
    exp = ref.daily.copy()
    exp.index = pd.DatetimeIndex(exp.index)
    assert list(got.index) == list(exp.index)
    for c in exp.columns:
        np.testing.assert_array_equal(got[c].to_numpy(dtype=float), exp[c].to_numpy(dtype=float), err_msg=c)
    pd.testing.assert_frame_equal(_fills(db), _ref_fills(ref), check_exact=True)


def test_uninterrupted_worker_equals_event_engine(tmp_path):
    ref = _reference()
    assert len(ref.fills) > 50 and ref.daily["n_pos"].max() >= 3  # 样例确实在交易
    assert run_worker(_spec("a"), tmp_path) == 0
    _assert_same(layout.run_db(tmp_path, "a"), ref)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_kills_resume_bitwise(tmp_path, seed):
    """每次在随机天数后'被杀'（不写检查点直接退出），反复续跑直到完成。"""
    ref = _reference()
    rng = np.random.default_rng(seed)
    kills = 0
    while True:
        k = int(rng.integers(1, 120))
        rc = run_worker(_spec("k"), tmp_path, stop_after_days=k)
        if rc == 0:
            break
        kills += 1
    assert kills >= 3
    _assert_same(layout.run_db(tmp_path, "k"), ref)


def test_resume_across_segment_boundary_uses_original_codes(tmp_path):
    """检查点落在年末最后一天之前：续跑重新加载该年的段时，代码集合必须与当初一致（不是续跑时的持仓）。"""
    feed = SynthFeed()
    s = LowVolEqualWeight(n=3, lookback=20, every=5)
    r = EngineRun(EventEngine(), s, feed, START, END)
    it = r.days()
    for d in it:
        if d >= pd.Timestamp("2020-06-15"):
            break
    state = ckpt_roundtrip(r.state_dict())
    feed2 = SynthFeed()
    r2 = EngineRun(EventEngine(), LowVolEqualWeight(), feed2, START, END)
    r2.load_state_dict(state)
    next(r2.days())
    assert feed2.loads[0] == next(x for x in feed.loads if x[0] == 2020)


def ckpt_roundtrip(state):
    import pickle

    return pickle.loads(pickle.dumps(state))


def test_resume_with_risk_monitor_and_costs(tmp_path):
    over = dict(risk=dict(max_drawdown=-0.08, daily_loss_limit=-0.02), match=dict(slippage_bp=8.0))
    ref = _reference(over)
    assert ref.risk_events  # 风控确实触发过
    spec = _spec("r", engine=over)
    for k in (37, 91, 13, 200):
        if run_worker(spec, tmp_path, stop_after_days=k) == 0:
            break
    assert run_worker(spec, tmp_path) == 0
    _assert_same(layout.run_db(tmp_path, "r"), ref)


def test_spec_change_refused_and_done_is_idempotent(tmp_path):
    assert run_worker(_spec("x"), tmp_path) == 0
    n = len(ckpt.read_daily(layout.run_db(tmp_path, "x")))
    assert run_worker(_spec("x"), tmp_path) == 0  # 已完成：不重复写
    assert len(ckpt.read_daily(layout.run_db(tmp_path, "x"))) == n
    with pytest.raises(ValueError, match="不同的 spec"):
        run_worker({**_spec("x"), "end": "2020-12-31"}, tmp_path)


def test_heartbeat_and_meta(tmp_path):
    run_worker(_spec("h"), tmp_path)
    hb = layout.read_json(layout.hb_dir(tmp_path) / "h.json")
    assert hb["status"] == "done" and hb["n_days"] == hb["total_days"] > 400
    with sqlite3.connect(layout.run_db(tmp_path, "h")) as c:
        kinds = [k for (k,) in c.execute("SELECT kind FROM events ORDER BY seq")]
    assert kinds[0] == "start" and kinds[-1] == "done"


def test_pacer_schedule_and_heartbeat_ticks():
    t = [0.0]
    ticks = []
    p = Pacer(2.0, clock=lambda: t[0], sleep=lambda d: t.__setitem__(0, t[0] + d))
    p.wait(lambda: ticks.append(t[0]))
    assert t[0] == pytest.approx(2.0) and len(ticks) == 4  # 0.5 秒一片
    t[0] += 5.0  # 处理一天花了 5 秒：落后了，下一次不睡
    assert p.wait() == 0.0
    t[0] = 5.5
    p.wait()
    assert t[0] == pytest.approx(6.0)  # 第 3 天不早于 t0 + 3N
    with pytest.raises(ValueError):
        Pacer(-1)


def test_paced_worker_uses_injected_clock(tmp_path):
    t = [0.0]
    spec = dict(_spec("p"), end="2019-03-29", seconds_per_day=3.0)
    run_worker(spec, tmp_path, clock=lambda: t[0], sleep=lambda d: t.__setitem__(0, t[0] + d))
    n = len(ckpt.read_daily(layout.run_db(tmp_path, "p")))
    assert t[0] == pytest.approx(3.0 * n)


def test_build_strategy_and_engine_config():
    assert build_strategy("lowvol", {"n": 5}).n == 5
    c = build_strategy("combo", {"members": [{"strategy": "lowvol", "weight": 1},
                                             {"strategy": "eqw", "weight": 2}]})
    assert c.name == "combo"
    with pytest.raises(ValueError):
        build_strategy("nope")
    cfg = engine_config(dict(initial_cash=5e5, match=dict(max_participation=0.05, exec_style="VWAP"),
                             risk=dict(max_drawdown=0.2)))
    assert cfg.initial_cash == 5e5 and cfg.match.max_participation == 0.05
    assert cfg.match.exec_style.name == "VWAP" and isinstance(cfg.risk, RiskConfig)
    with pytest.raises(ValueError, match="没有字段"):
        engine_config(dict(match=dict(bogus=1)))
    assert isinstance(engine_config(None), EngineConfig)
