import numpy as np
import pandas as pd

from q6.core.pit import Panel
from q6.core.types import AccountSnapshot
from q6.strategy.base import BarContext
from q6.strategy.mean_reversion import ShortReversal

SYMS = ("sz.000001", "sh.600000", "sz.000002", "sh.600001")


def context(close_hfq=None, amount=None, status=None, date="2015-01-30"):
    t, n = 25, len(SYMS)
    px = np.tile(np.arange(10, 10 + t, dtype=float)[:, None], (1, n))
    if close_hfq is not None:
        px[-6:] = close_hfq
    amt = np.full((t, n), 100.0) if amount is None else np.asarray(amount, dtype=float)
    fields = {
        "close_hfq": px, "amount": amt, "close": np.full((t, n), 10.0),
        "preclose": np.full((t, n), 10.0), "tradestatus": np.ones((t, n)),
        "is_st": np.zeros((t, n)), "is_new": np.zeros((t, n)),
    }
    if status is not None:
        fields["tradestatus"][-1] = status
    panel = Panel(pd.bdate_range(end=date, periods=t), SYMS, fields)
    view = panel.view(t - 1)
    return BarContext(view, AccountSnapshot(view.now.to_pydatetime(), 1_000_000), SYMS)


def test_selects_lowest_recent_return_and_weights_sum():
    c = context(close_hfq=np.array([[20, 20, 20, 20], [19, 20, 21, 22], [18, 20, 22, 24],
                                    [17, 20, 23, 26], [16, 20, 24, 28], [15, 20, 25, 30]], float))
    got = ShortReversal(lookback=5, n=2, every=1, liq_window=20).on_bar(c)
    assert got == {SYMS[0]: 0.475, SYMS[1]: 0.475}


def test_liquidity_median_filter():
    amounts = np.full((25, 4), [10, 20, 30, 40], dtype=float)
    got = ShortReversal(lookback=5, n=4, every=1, liq_window=20).on_bar(context(amount=amounts))
    assert set(got) == {SYMS[2], SYMS[3]}


def test_nontradable_excluded():
    got = ShortReversal(lookback=5, n=4, every=1, liq_window=20).on_bar(
        context(status=[0, 1, 1, 1]))
    assert SYMS[0] not in got and len(got) == 3


def test_all_ineligible_returns_empty():
    got = ShortReversal(lookback=5, n=4, every=1, liq_window=20).on_bar(
        context(status=[0, 0, 0, 0]))
    assert got == {}
