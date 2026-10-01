from __future__ import annotations

import pandas as pd
import pytest

from q6.research.benchmark import buy_and_hold, index_returns


def test_index_returns_uses_preclose() -> None:
    frame = pd.DataFrame(
        {"code": ["sh.000300"] * 3, "date": pd.date_range("2020-01-01", periods=3),
         "close": [10.0, 12.0, 9.0], "preclose": [9.0, 10.0, 12.0]}
    )
    result = index_returns(frame, "sh.000300")
    assert result.tolist() == pytest.approx([1 / 9, 0.2, -0.25])


def test_buy_and_hold_three_days_two_assets() -> None:
    close = pd.DataFrame({"A": [100.0, 110.0, 121.0], "B": [100.0, 90.0, 99.0]})
    result = buy_and_hold(close, pd.Series({"A": 0.5, "B": 0.5}))
    assert result.tolist() == pytest.approx([0.0, 0.0, 0.1])
