"""Paper checks: Bailey & López de Prado (2012, 2014) and CSCV construction tests."""

import math

import numpy as np
import pytest

from q6.research.overfit import dsr, expected_max_sr, pbo_cscv, psr


def test_psr_matches_2012_numerical_example():
    # The Sharpe Ratio Efficient Frontier, numerical example, pp. 25-26:
    # monthly track record, annual SR 1.59, skew -2.448, ordinary kurtosis 10.164;
    # paper reports PSR(0) = 0.913. Convert SR to monthly units as the paper does.
    result = psr(1.59 / math.sqrt(12), 24, -2.448, 10.164)
    assert result == pytest.approx(0.913, abs=1e-3)


def test_dsr_matches_2014_numerical_example():
    # The Deflated Sharpe Ratio, "A Numerical Example", pp. 9-10:
    # N=100, V=1/(2*250), T=1250, skew=-3, ordinary kurtosis=10,
    # observed annual SR=2.5 (daily SR=2.5/sqrt(250)); paper reports SR0~=0.1132, DSR=0.9004.
    variance = 1 / (2 * 250)
    assert expected_max_sr(100, variance) == pytest.approx(0.1132, abs=1e-4)
    assert dsr(2.5 / math.sqrt(250), 1250, -3, 10, 100, variance) == pytest.approx(0.9004, abs=1e-4)


def test_cscv_noise_has_near_half_pbo():
    rng = np.random.default_rng(20261001)
    result = pbo_cscv(rng.standard_normal((1000, 50)), n_splits=16)
    assert 0.3 <= result["pbo"] <= 0.7


def test_cscv_true_drift_has_low_pbo():
    rng = np.random.default_rng(20261001)
    perf = rng.standard_normal((1000, 50))
    perf[:, 0] += 0.2
    assert pbo_cscv(perf, n_splits=16)["pbo"] < 0.1


@pytest.mark.parametrize("splits", [3, 12])
def test_cscv_rejects_odd_splits_and_splits_over_t(splits):
    with pytest.raises(ValueError):
        pbo_cscv(np.ones((10, 3)), n_splits=splits)


def test_cscv_enumerates_all_combinations():
    result = pbo_cscv(np.random.default_rng(0).normal(size=(32, 4)), n_splits=16)
    assert result["n_combinations"] == math.comb(16, 8) == 12870
    assert result["logits"].shape == (12870,)


def test_cscv_drops_front_remainder():
    result = pbo_cscv(np.random.default_rng(1).normal(size=(19, 4)), n_splits=4)
    assert result["n_combinations"] == math.comb(4, 2)

