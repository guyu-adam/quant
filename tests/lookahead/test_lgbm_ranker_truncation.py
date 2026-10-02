"""策略④ ML 排序（P2-18）的截断 / 扰动测试：模型在另一份合成数据上训练好，作为常量传入；
策略在被测数据上只通过 PITView 取特征。"""

import numpy as np
import pandas as pd

from lookahead.samples import make_data
from q6.lint.truncation_test import check_lookahead, strategy_runner
from q6.ml import lgbm_ranker as lr
from q6.ml.features import FEATURE_NAMES


def frames(seed):
    d = make_data(T=250, N=8, seed=seed)
    c, v = d["close"], d["volume"]
    one = c * 0 + 1
    return {
        "open_hfq": c * 0.999, "high_hfq": c * 1.01, "low_hfq": c * 0.99, "close_hfq": c,
        "ret": c.pct_change(fill_method=None), "amount": v * c, "turn": v / 1_000, "volume": v,
        "adj_factor": one, "tradestatus": one, "bad": c * 0, "is_st": c * 0, "is_new": c * 0,
    }


def model():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(400, len(FEATURE_NAMES))).astype(np.float32)
    y = rng.integers(0, 5, 400).astype(np.int32)
    cfg = lr.RankerConfig(n_estimators=10, min_data_in_leaf=10)
    return lr.fit_ranker(x, y, np.full(8, 50), cfg)


def test_lgbm_ranker_passes_truncation():
    m = model()
    f = frames(11)
    assert isinstance(f["close_hfq"], pd.DataFrame)
    rep = check_lookahead(strategy_runner(lambda: lr.LGBMRankerStrategy(m, n=3, every=5, history=60)), f)
    assert rep.passed, str(rep)
