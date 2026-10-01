from lookahead.samples import data_with_fields
from q6.lint.truncation_test import check_lookahead, strategy_runner
from q6.strategy.mean_reversion import ShortReversal


def test_short_reversal_is_point_in_time():
    d = data_with_fields()
    close = d["close"]
    close.columns = [f"sz.{i:06d}" for i in range(1, len(close.columns) + 1)]
    for frame in d.values():
        frame.columns = close.columns
    fields = {**d, "close_hfq": close, "close": close, "preclose": close.shift(1).fillna(close).round(2),
              "tradestatus": close * 0 + 1, "is_st": close * 0, "is_new": close * 0}
    def make():
        return ShortReversal(lookback=5, n=3, every=5, liq_window=20)

    rep = check_lookahead(strategy_runner(make), fields)
    assert rep.passed, str(rep)
