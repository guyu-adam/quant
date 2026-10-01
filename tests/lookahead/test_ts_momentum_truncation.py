from lookahead.samples import data_with_fields
from q6.lint.truncation_test import check_lookahead, strategy_runner
from q6.strategy.ts_momentum import TSMomentum


def test_ts_momentum_passes_truncation():
    data = data_with_fields()
    close = data["close"]
    data.update(
        close_hfq=close,
        tradestatus=close * 0 + 1,
        is_st=close * 0,
        is_new=close * 0,
    )
    report = check_lookahead(
        strategy_runner(lambda: TSMomentum(lookback=20, skip=3, vol_window=10, every=5, cap=0.1)),
        data,
    )
    assert report.passed, str(report)
