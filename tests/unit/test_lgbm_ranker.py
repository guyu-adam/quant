"""策略④ ML 排序（P2-18）：特征口径、标签、视界约束、固定 seed 逐位一致、引擎跑通。合成数据，不依赖快照。"""

import numpy as np
import pandas as pd
import pytest

from q6.alpha.inputs import build_inputs
from q6.core.pit import Panel
from q6.data import lockbox
from q6.engine.event import EventEngine
from q6.engine.feed import Segment
from q6.ml import lgbm_ranker as lr
from q6.ml.features import FEATURE_NAMES, build_features

T, N = 330, 12
DAYS = pd.bdate_range("2019-01-02", periods=T)
CODES = [f"sh.6000{i:02d}" for i in range(N)]


def synth(seed=3):
    rng = np.random.default_rng(seed)
    drift = rng.normal(0, 0.0005, N)
    close = 10 * np.exp(np.cumsum(rng.normal(drift, 0.015, (T, N)), axis=0))
    close = pd.DataFrame(close, DAYS, CODES).round(2)  # 撮合要求整分价格
    pre = close.shift(1)
    pre.iloc[0] = close.iloc[0]
    vol = pd.DataFrame(rng.integers(100_000, 5_000_000, (T, N)).astype(float), DAYS, CODES)
    ts = pd.DataFrame(1.0, DAYS, CODES)
    ts.iloc[rng.random((T, N)) < 0.02] = 0.0  # 随机停牌
    bad = pd.DataFrame(0.0, DAYS, CODES)
    bad.iloc[rng.random((T, N)) < 0.01] = 1.0
    adj = pd.DataFrame(np.repeat(np.linspace(1.0, 1.3, T)[:, None], N, axis=1), DAYS, CODES)
    zero = close * 0
    f = {
        "open": close, "high": (close * 1.01).round(2), "low": (close * 0.99).round(2), "close": close,
        "preclose": pre,
        "volume": vol, "amount": vol * close, "tradestatus": ts, "is_st": zero.copy(), "is_new": zero.copy(),
        "adj_factor": adj, "ret": close / pre - 1, "bad": bad, "turn": vol / 50_000,
    }
    for k in ("open", "high", "low", "close"):
        f[f"{k}_hfq"] = f[k] * adj
    return f


class FakeFeed:
    def __init__(self, f, universe=None):
        self.fields = tuple(f)
        self.panel = Panel.from_frames(f)
        self.universe = np.ones((T, N), dtype=bool) if universe is None else universe
        self.calendar = DAYS
        self.snapshot_id = "fake"

    def segments(self, start, end, warmup, keep=None):
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        i0 = int(DAYS.searchsorted(start))
        i1 = int(DAYS.searchsorted(end, side="right"))
        lockbox.check_dates(DAYS[:i1], "fake feed")  # 和 SnapshotFeed 一样受研究视界约束
        lo = max(0, i0 - warmup + 1)
        sub = {k: v.iloc[lo:i1] for k, v in self.panel_frames.items()}
        yield Segment(Panel.from_frames(sub), i0 - lo, self.universe[lo:i1])

    @property
    def panel_frames(self):
        return {k: pd.DataFrame(self.panel.block(k, 0, T), DAYS, CODES) for k in self.fields}


def test_load_lightgbm_does_not_skip():
    lgb = lr.load_lightgbm()
    assert hasattr(lgb, "train")


def test_feature_matrix_matches_build_inputs_path():
    f = synth()
    p = Panel.from_frames(f)
    t, h = 300, 260
    uni = np.ones(N, dtype=bool)
    uni[4] = False
    codes, x = lr.feature_matrix(p.view(t), uni, h)
    rows = slice(t - h + 1, t + 1)
    long = pd.concat({k: f[k].iloc[rows].stack(future_stack=True) for k in
                      ("open_hfq", "high_hfq", "low_hfq", "close_hfq", "ret", "amount", "turn", "volume",
                       "adj_factor", "tradestatus", "bad")}, axis=1)
    long.index.names = ["date", "code"]
    long = long.reset_index()
    long = long[long["code"].isin(codes)]
    feats = build_features(build_inputs(long))
    want = np.column_stack([feats[n].iloc[-1].reindex(codes).to_numpy() for n in FEATURE_NAMES])
    np.testing.assert_array_equal(x, want.astype(np.float32))
    assert x.shape == (len(codes), 75) and np.isfinite(x).any(axis=1).all()


def test_feature_matrix_excludes_ineligible():
    f = synth()
    t = 300
    f["is_st"].iloc[t, 0] = 1
    f["is_new"].iloc[t, 1] = 1
    f["tradestatus"].iloc[t, 2] = 0
    f["close_hfq"].iloc[t, 3] = np.nan
    uni = np.ones(N, dtype=bool)
    uni[4] = False
    codes, _ = lr.feature_matrix(Panel.from_frames(f).view(t), uni, 260)
    assert codes == CODES[5:]


def _cache(tmp_path, f, every=10, history=60):
    feed = FakeFeed(f)
    return lr.build_feature_cache(feed, DAYS[0], DAYS[-1], tmp_path / "c", sample_every=every,
                                  history=history)


def test_training_labels_hand_computed(tmp_path):
    f = synth()
    path = _cache(tmp_path, f)
    feats = lr.load_feature_cache(path, DAYS[0], DAYS[-1])
    h = 5
    x, y, group, kept = lr.training_set(feats, f["close_hfq"], h, 5)
    d = kept["date"].iloc[0]
    i = DAYS.get_loc(d)
    codes_d = feats.loc[feats["date"] == d, "code"].tolist()
    c = f["close_hfq"]
    r = (c.iloc[i + 1 + h] / c.iloc[i + 1] - 1)[codes_d]
    pct = r.rank(pct=True)
    got = kept[kept["date"] == d].set_index("code")["label"]
    np.testing.assert_allclose(got.to_numpy(), pct[got.index].to_numpy())
    assert group.sum() == len(y) == len(x)
    assert set(np.unique(y)) <= {0, 1, 2, 3, 4}


def test_samples_whose_label_passes_close_end_are_dropped(tmp_path):
    f = synth()
    path = _cache(tmp_path, f)
    feats = lr.load_feature_cache(path, DAYS[0], DAYS[-1])
    end = DAYS[200]
    _, _, _, kept = lr.training_set(feats, f["close_hfq"].loc[:end], 5, 5)
    # t+1+5 ≤ end ⇔ t ≤ DAYS[194]
    assert kept["date"].max() <= DAYS[194]


def test_cache_read_beyond_research_horizon_raises(tmp_path):
    path = _cache(tmp_path, synth())
    with lockbox.research_horizon(DAYS[200]):
        lr.load_feature_cache(path, DAYS[0], DAYS[200])
        with pytest.raises(lockbox.HorizonError):
            lr.load_feature_cache(path, DAYS[0], DAYS[260])


def test_fit_window_inside_horizon_and_deterministic(tmp_path):
    f = synth()
    path = _cache(tmp_path, f, every=2)
    feed = FakeFeed(f)
    cfg = lr.RankerConfig(horizon=5, n_estimators=30, min_data_in_leaf=20)
    with lockbox.research_horizon(DAYS[250]):
        m1 = lr.fit_window(feed, path, DAYS[60], DAYS[250], cfg)
        m2 = lr.fit_window(feed, path, DAYS[60], DAYS[250], cfg)
    assert m1.model_to_string() == m2.model_to_string()
    x = lr.load_feature_cache(path, DAYS[251], DAYS[-1])[list(FEATURE_NAMES)].to_numpy(dtype=np.float32)
    assert np.array_equal(m1.predict(x), m2.predict(x))


class ColumnModel:
    def __init__(self, k):
        self.k = k

    def predict(self, x):
        return np.nan_to_num(x[:, self.k], nan=-np.inf)


def test_strategy_takes_top_n_by_score():
    from q6.core.types import AccountSnapshot
    from q6.strategy.base import BarContext

    f = synth()
    p = Panel.from_frames(f)
    view = p.view(300)
    k = FEATURE_NAMES.index("ret_20")
    s = lr.LGBMRankerStrategy(ColumnModel(k), n=3, every=5, history=260)
    w = s.on_bar(BarContext(view, AccountSnapshot(view.now.to_pydatetime(), 1e6), tuple(CODES)))
    codes, x = lr.feature_matrix(view, np.ones(N, dtype=bool), 260)
    top = [codes[j] for j in np.argsort(-np.nan_to_num(x[:, k], nan=-np.inf), kind="stable")[:3]]
    assert w == {c: 0.95 / 3 for c in top}
    assert s.on_bar(BarContext(view, AccountSnapshot(view.now.to_pydatetime(), 1e6), tuple(CODES))) is None


def test_event_engine_runs_with_trained_model(tmp_path):
    f = synth()
    path = _cache(tmp_path, f, every=2)
    feed = FakeFeed(f)
    cfg = lr.RankerConfig(horizon=5, n_estimators=20, min_data_in_leaf=20)
    model = lr.fit_window(feed, path, DAYS[60], DAYS[200], cfg)
    s = lr.LGBMRankerStrategy(model, n=4, every=10, history=60)
    res = EventEngine().run(s, feed, DAYS[201], DAYS[-1])
    assert len(res.fills) > 0
    assert np.isfinite(res.daily["equity"]).all()
