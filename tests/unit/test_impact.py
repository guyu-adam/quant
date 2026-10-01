import numpy as np
import pytest

from q6.core.types import Side
from q6.market.impact import ImpactResult, cost_rate, cost_rate_array, exec_price


def test_zero_quantity_has_no_cost_even_with_fixed_slippage() -> None:
    result = cost_rate(0, 0, 0.02, slippage_bp=5)
    assert result.slippage_rate == 0
    assert result.impact_rate == 0


def test_cost_increases_strictly_with_order_value() -> None:
    values = [cost_rate(float(q), 1e8, 0.02).total_rate for q in range(1, 11)]
    assert all(left < right for left, right in zip(values, values[1:], strict=False))


def test_parameters_match_hand_calculated_values() -> None:
    # impact = 0.1 * 0.02 * sqrt(1e6 / 1e8) = 0.0002
    base = cost_rate(1e6, 1e8, 0.02)
    assert base.slippage_rate == 0.0005
    assert base.impact_rate == 0.0002
    assert cost_rate(1e6, 1e8, 0.02, slippage_bp=10).slippage_rate == 0.001
    # Doubling impact_coef doubles 0.0002 to 0.0004.
    assert cost_rate(1e6, 1e8, 0.02, impact_coef=0.2).impact_rate == 0.0004
    # Doubling cost_multiplier doubles both components.
    scaled = cost_rate(1e6, 1e8, 0.02, cost_multiplier=2)
    assert scaled.slippage_rate == 0.001
    assert scaled.impact_rate == 0.0004


def test_execution_price_applies_cost_by_side_and_rejects_total_loss() -> None:
    result = ImpactResult(0.001, 0.002)
    assert exec_price(100, Side.BUY, result) > 100
    assert exec_price(100, Side.SELL, result) < 100
    with pytest.raises(ValueError):
        exec_price(100, Side.SELL, ImpactResult(1, 0))


def test_array_matches_scalar_for_random_values() -> None:
    rng = np.random.default_rng(2404)
    qty = rng.uniform(1, 1e7, 200)
    adv = rng.uniform(1e6, 1e9, 200)
    sigma = rng.uniform(0, 0.1, 200)
    slip, impact = cost_rate_array(qty, adv, sigma)
    expected = [cost_rate(q, a, s) for q, a, s in zip(qty, adv, sigma, strict=True)]
    np.testing.assert_allclose(slip, [r.slippage_rate for r in expected], rtol=0, atol=0)
    np.testing.assert_allclose(impact, [r.impact_rate for r in expected], rtol=0, atol=0)


@pytest.mark.parametrize(
    ("qty", "adv", "sigma", "kwargs"),
    [
        (1, 0, 0.1, {}),
        (1, float("nan"), 0.1, {}),
        (1, 10, float("nan"), {}),
        (1, 10, -0.1, {}),
        (-1, 10, 0.1, {}),
        (1, 10, 0.1, {"slippage_bp": -1}),
        (1, 10, 0.1, {"impact_coef": -1}),
        (1, 10, 0.1, {"cost_multiplier": -1}),
    ],
)
def test_invalid_scalar_inputs_raise(qty, adv, sigma, kwargs) -> None:
    with pytest.raises(ValueError):
        cost_rate(qty, adv, sigma, **kwargs)


@pytest.mark.parametrize(
    ("qty", "adv", "sigma"),
    [
        ([1, 2], [10, 0], [0.1, 0.1]),
        ([1, 2], [10, 20], [0.1, float("nan")]),
        ([1, -2], [10, 20], [0.1, 0.1]),
        ([1, 2], [10, 20], [0.1, -0.1]),
    ],
)
def test_invalid_array_inputs_raise(qty, adv, sigma) -> None:
    with pytest.raises(ValueError):
        cost_rate_array(np.array(qty), np.array(adv), np.array(sigma))
