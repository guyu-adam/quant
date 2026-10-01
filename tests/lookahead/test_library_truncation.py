import numpy as np
import pytest

from q6.alpha.library import FACTORS
from q6.alpha.neutralize import neutralize
from q6.lint.truncation_test import check_lookahead

from .samples import make_data


@pytest.fixture
def factor_data():
    base = make_data(T=300, N=30)
    close, volume = base["close"], base["volume"]
    rng = np.random.default_rng(91)
    ret = close.pct_change(fill_method=None)
    return {
        "close": close,
        "open": close * (1 + rng.normal(0, 0.003, close.shape)),
        "high": close * 1.02,
        "low": close * 0.98,
        "ret": ret,
        "volume": volume,
        "amount": volume * close,
        "turn": volume / 1e6,
        "float_cap": close * 1e8,
        "vwap": close,
        "size": np.log(close * 1e8),
    }


@pytest.mark.parametrize("name", sorted(FACTORS), ids=sorted(FACTORS))
def test_library_factor_truncation(factor_data, name):
    def fn(d):
        return FACTORS[name](d)

    report = check_lookahead(fn, factor_data, lag=0)
    assert report.passed, str(report)


def test_library_neutralize_size_truncation(factor_data):
    def fn(d):
        return neutralize(FACTORS["mom_3"](d), {"size": d["size"]})

    report = check_lookahead(fn, factor_data, lag=0)
    assert report.passed, str(report)
