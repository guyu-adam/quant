from lookahead.samples import data_with_fields
from q6.lint.truncation_test import check_lookahead, strategy_runner
from q6.strategy.benchmarks import BuyAndHold, UniverseEqualWeight


def frames():
    data = data_with_fields()
    close = data["close"]
    return {
        "close": close,
        "tradestatus": close * 0 + 1,
        "is_st": close * 0,
        "is_new": close * 0,
    }


def test_universe_equal_weight_passes_truncation():
    report = check_lookahead(strategy_runner(lambda: UniverseEqualWeight(gross=0.5)), frames())
    assert report.passed, str(report)


def test_buy_and_hold_passes_truncation():
    report = check_lookahead(strategy_runner(lambda: BuyAndHold(gross=0.5)), frames())
    assert report.passed, str(report)
