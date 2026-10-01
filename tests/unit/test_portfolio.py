from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from q6.portfolio.construct import Constraints, project
from q6.portfolio.hrp import hrp_weights


def test_cap_redistributes_and_preserves_gross() -> None:
    result = project({"A": 0.8, "B": 0.1, "C": 0.1}, Constraints(cap=0.5, gross=1))
    assert sum(result.weights.values()) == pytest.approx(1)
    assert result.weights["A"] == pytest.approx(0.5)
    assert result.weights["B"] == pytest.approx(0.25)


def test_infeasible_cap_keeps_cash_and_note() -> None:
    result = project({str(i): 1 for i in range(3)}, Constraints(cap=0.2, gross=0.9))
    assert sum(result.weights.values()) == pytest.approx(0.6)
    assert result.notes and result.notes[0].startswith("INFEASIBLE_GROSS:")


def test_industry_cap_redistributes() -> None:
    result = project(
        {"A": 0.4, "B": 0.4, "C": 0.2},
        Constraints(cap=0.5, gross=1, industry_cap=0.5),
        industry={"A": "x", "B": "x", "C": "y"},
    )
    assert result.weights["A"] + result.weights["B"] == pytest.approx(0.5)
    assert sum(result.weights.values()) == pytest.approx(1)


def test_none_industry_skips_industry_constraint() -> None:
    result = project({"A": 0.4, "B": 0.4, "C": 0.2}, Constraints(cap=0.5, gross=1, industry_cap=0.3))
    assert result.weights["A"] + result.weights["B"] == pytest.approx(0.8)


def test_turnover_blend_lambda_by_hand() -> None:
    result = project(
        {"A": 0.8, "B": 0.2}, Constraints(cap=1, gross=1, turnover_cap=0.1), current={"A": 0.5, "B": 0.5}
    )
    assert result.weights == {"A": pytest.approx(0.6), "B": pytest.approx(0.4)}


def test_turnover_with_breached_current_notes() -> None:
    result = project(
        {"A": 0.0, "B": 1.0}, Constraints(cap=0.5, gross=1, turnover_cap=0.05), current={"A": 0.8, "B": 0.2}
    )
    assert "TURNOVER_BLEND_MAY_VIOLATE_CAP" in result.notes
    assert result.weights["A"] > 0.5


def test_does_not_scale_up() -> None:
    result = project({"A": 0.1, "B": 0.2}, Constraints(cap=1, gross=0.9))
    assert sum(result.weights.values()) == pytest.approx(0.3)


def test_empty_input() -> None:
    assert project({}).weights == {}
    assert project({"A": 0, "B": -1, "C": float("nan")}).weights == {}


def test_deterministic_order_and_values() -> None:
    target = {"Z": 0.7, "A": 0.2, "M": 0.1}
    a = project(target, Constraints(cap=0.5, gross=1))
    b = project(dict(reversed(list(target.items()))), Constraints(cap=0.5, gross=1))
    assert list(a.weights) == ["A", "M", "Z"]
    assert list(a.weights.items()) == list(b.weights.items())


@given(
    values=st.lists(
        st.floats(min_value=0.01, max_value=1, allow_nan=False, allow_infinity=False), min_size=3, max_size=12
    ),
    gross=st.floats(min_value=0.05, max_value=0.8, allow_nan=False, allow_infinity=False),
)
def test_feasible_projection_properties(values: list[float], gross: float) -> None:
    cap = max(gross / len(values), 0.01)
    cap = min(cap, 0.5)
    result = project({f"{i:02}": value for i, value in enumerate(values)}, Constraints(cap=cap, gross=gross))
    expected = min(sum(values), gross)
    if len(values) * cap + 1e-12 >= expected:
        assert sum(result.weights.values()) == pytest.approx(expected, abs=1e-10)
        assert max(result.weights.values()) <= cap + 1e-12


def test_two_asset_hrp_matches_inverse_variance() -> None:
    data = np.array([[1, 2], [-1, -2], [2, 4], [-2, -4], [3, 6]], dtype=float)
    weights = hrp_weights(pd.DataFrame(data, columns=["A", "B"]))
    variances = np.var(data, axis=0, ddof=1)
    expected = (1 / variances) / np.sum(1 / variances)
    assert weights.to_numpy() == pytest.approx(expected)


def test_diagonal_four_asset_hrp_matches_inverse_variance() -> None:
    rng = np.random.default_rng(222)
    data = rng.normal(size=(20000, 4)) * np.array([1.0, 2.0, 3.0, 4.0])
    weights = hrp_weights(pd.DataFrame(data, columns=list("ABCD")))
    inverse = 1 / np.var(data, axis=0, ddof=1)
    assert weights.to_numpy() == pytest.approx(inverse / inverse.sum(), abs=0.02)


def test_hrp_positive_and_sums_to_one() -> None:
    rng = np.random.default_rng(17)
    weights = hrp_weights(pd.DataFrame(rng.normal(size=(100, 5)), columns=list("ABCDE")))
    assert (weights > 0).all()
    assert weights.sum() == pytest.approx(1)


def test_hrp_single_asset() -> None:
    assert hrp_weights(pd.DataFrame({"A": [1, -1, 2, -2]})).to_dict() == {"A": 1.0}


@pytest.mark.parametrize(
    "data",
    [
        pd.DataFrame({"A": [1.0, 2.0, np.nan], "B": [2.0, 3.0, 4.0]}),
        pd.DataFrame({"A": [1.0, 1.0, 1.0], "B": [2.0, 3.0, 4.0]}),
    ],
)
def test_hrp_rejects_nan_or_zero_variance(data: pd.DataFrame) -> None:
    with pytest.raises(ValueError):
        hrp_weights(data)


def test_hrp_completely_correlated_pair_and_independent_asset() -> None:
    rng = np.random.default_rng(29)
    x, z = rng.normal(size=1000), rng.normal(size=1000)
    data = pd.DataFrame({"A": x, "B": 2 * x, "C": z})
    result = hrp_weights(data)
    assert result["A"] + result["B"] < result["C"]
    assert result["B"] > 0


def _independent_hrp(data: np.ndarray) -> np.ndarray:
    """Reference implementation mirroring the paper pseudocode, test-local only."""
    cov = np.cov(data, rowvar=False, ddof=1)
    corr = np.corrcoef(data, rowvar=False)
    from scipy.cluster.hierarchy import leaves_list, linkage
    from scipy.spatial.distance import squareform

    order = leaves_list(
        linkage(squareform(np.sqrt(np.clip((1 - corr) / 2, 0, None)), checks=False), method="single")
    )
    ordered = cov[np.ix_(order, order)]
    result = np.ones(data.shape[1])

    def var(cluster: np.ndarray) -> float:
        block = ordered[np.ix_(cluster, cluster)]
        iv = 1 / np.diag(block)
        iv /= iv.sum()
        return float(iv @ block @ iv)

    clusters = [np.arange(data.shape[1])]
    while clusters:
        next_ = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            midpoint = len(cluster) // 2
            left, right = cluster[:midpoint], cluster[midpoint:]
            v1, v2 = var(left), var(right)
            result[left] *= v2 / (v1 + v2)
            result[right] *= v1 / (v1 + v2)
            next_.extend([left, right])
        clusters = next_
    original = np.empty_like(result)
    original[order] = result
    return original / original.sum()


def test_hrp_matches_independent_numpy_reference() -> None:
    rng = np.random.default_rng(813)
    data = rng.normal(size=(500, 10))
    actual = hrp_weights(pd.DataFrame(data, columns=[f"S{i}" for i in range(10)])).to_numpy()
    assert np.max(np.abs(actual - _independent_hrp(data))) < 1e-12
