import numpy as np
import pandas as pd
from lookahead.samples import make_data

from q6.alpha.inputs import build_inputs
from q6.alpha.library import FACTORS, mom_12_1, rev_1, rev_5


def _data():
    base = make_data(T=300, N=12)
    close = base["close"]
    rng = np.random.default_rng(9)
    frame = pd.DataFrame(
        {
            "date": np.repeat(close.index, len(close.columns)),
            "code": list(close.columns) * len(close),
            "close_hfq": close.to_numpy().ravel(),
            "open_hfq": close.to_numpy().ravel() * (1 + rng.normal(0, 0.005, close.size)),
            "high_hfq": close.to_numpy().ravel() * 1.02,
            "low_hfq": close.to_numpy().ravel() * 0.98,
            "ret": close.pct_change().to_numpy().ravel(),
            "amount": 1e7,
            "turn": 2.0,
            "volume": base["volume"].to_numpy().ravel(),
            "adj_factor": 1.0,
            "tradestatus": 1,
            "bad": False,
        }
    )
    return build_inputs(frame)


def test_registry_and_all_factors_run():
    d = _data()
    assert len(FACTORS) == 30
    for name, factor in FACTORS.items():
        value = factor(d)
        assert value.shape == d["close"].shape, name
        assert not value.isna().all().all(), name


def test_three_factors_hand_calculation():
    idx = pd.bdate_range("2020-01-01", periods=260)
    c = pd.DataFrame({"x": np.arange(1.0, 261)}, index=idx)
    d = {"close": c, "ret": c / c.shift(1) - 1}
    np.testing.assert_allclose(mom_12_1(d).iloc[-1, 0], 239 / 8 - 1)
    np.testing.assert_allclose(rev_1(d).iloc[-1, 0], -d["ret"].iloc[-1, 0])
    np.testing.assert_allclose(rev_5(d).iloc[-1, 0], -(260 / 255 - 1))
