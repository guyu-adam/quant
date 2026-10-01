from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from q6.research.regimes import REGIMES, regime_table, slice_regime


def test_regime_definition_bounds_and_non_overlap() -> None:
    assert len(REGIMES) == 10
    assert all(regime.end < date(2024, 7, 1) for regime in REGIMES)
    ordered = sorted(REGIMES, key=lambda item: item.start)
    assert all(left.end < right.start for left, right in zip(ordered, ordered[1:], strict=False))


def test_regime_table_total_return_matches_hand_calculation() -> None:
    dates = pd.date_range("2008-01-01", "2024-02-29")
    values = [0.0] * len(dates)
    values[:3] = [0.1, -0.1, 0.2]
    returns = pd.Series(values, index=dates)
    selected = slice_regime(returns, REGIMES[0])
    table = regime_table(returns)
    assert selected.iloc[:3].equals(returns.iloc[:3])
    assert selected.index.min() == pd.Timestamp("2008-01-01")
    assert selected.index.max() == pd.Timestamp("2008-11-30")
    assert table.loc["gfc_2008", "total_return"] == pytest.approx(1.1 * 0.9 * 1.2 - 1)
