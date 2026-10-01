import numpy as np
import pandas as pd
import pytest

from q6.research.metrics import (
    ann_return,
    ann_vol,
    calmar,
    capacity,
    forward_returns,
    ic_series,
    ic_summary,
    max_drawdown,
    max_drawdown_duration,
    pl_ratio,
    sharpe,
    sortino,
    total_return,
    win_rate,
)


@pytest.fixture
def known_returns():
    return pd.Series([0.01, -0.02, 0.015, 0.0, -0.01, 0.03, -0.005, 0.02, -0.025, 0.012])


def test_known_sequence_metrics(known_returns):
    expected = {
        total_return: 0.025874634586798617,
        ann_return: 0.9036019971933023,
        ann_vol: 0.28229558976363767,
        sharpe: 2.4102395668656733,
        sortino: 3.996824826740867,
        max_drawdown: -0.025000000000000133,
        max_drawdown_duration: 4,
        calmar: 36.1440798877319,
        win_rate: 0.5555555555555556,
        pl_ratio: 1.16,
    }
    for function, value in expected.items():
        assert abs(function(known_returns) - value) < 1e-9


def test_zero_returns_and_nan_input():
    zeros = pd.Series([0.0, 0.0, 0.0])
    assert np.isnan(sharpe(zeros))
    assert np.isnan(calmar(zeros))
    assert np.isnan(win_rate(zeros))
    assert max_drawdown(zeros) == 0
    with pytest.raises(ValueError, match="NaN"):
        total_return(pd.Series([0.01, np.nan]))


def test_unrecovered_drawdown_duration():
    assert max_drawdown_duration(pd.Series([0.1, -0.5, 0.01])) == 2


def test_capacity_zero_adv_reduces_quantile():
    trades = pd.DataFrame({"a": [100.0, 100.0]})
    adv = pd.DataFrame({"a": [10000.0, 0.0]})
    assert capacity(trades, adv, q=0.5) == 5.0


def test_ic_example():
    index = ["d1", "d2", "d3"]
    columns = list("abcde")
    factor = pd.DataFrame([[1, 2, 3, 4, 5] for _ in index], index=index, columns=columns, dtype=float)
    fwd = pd.DataFrame(
        [[0.1, 0.2, 0.3, 0.4, 0.5], [0.5, 0.4, 0.3, 0.2, 0.1], [0.3, 0.1, 0.2, 0.5, 0.4]],
        index=index,
        columns=columns,
    )
    ic = ic_series(factor, fwd, min_obs=5)
    np.testing.assert_allclose(ic, [1, -1, 0.6], atol=1e-12)
    result = ic_summary(ic)
    assert result["mean"] == pytest.approx(0.2)
    assert result["std"] == pytest.approx(np.sqrt(1.12))
    assert result["ir"] == pytest.approx(0.2 / np.sqrt(1.12))


def test_ic_requires_identical_axes():
    with pytest.raises(ValueError):
        ic_series(pd.DataFrame([[1]], columns=["a"]), pd.DataFrame([[1]], columns=["b"]))


def test_forward_returns_are_ahead():
    close = pd.DataFrame({"a": [10.0, 12.0, 15.0]})
    result = forward_returns(close, 1)
    assert result.iloc[0, 0] == pytest.approx(0.2)
    assert result.iloc[1, 0] == pytest.approx(0.25)
    assert np.isnan(result.iloc[2, 0])
