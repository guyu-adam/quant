from lookahead.samples import data_with_fields
from q6.lint.truncation_test import check_lookahead, strategy_runner
from q6.strategy.cs_multifactor import CSMultiFactor


def test_cs_multifactor_truncation():
    data = data_with_fields()
    data = {key: value.iloc[:280].copy() for key, value in data.items()}
    close = data["close"]
    data.update(
        close_hfq=close,
        tradestatus=close * 0 + 1,
        is_st=close * 0,
        is_new=close * 0,
    )
    rep = check_lookahead(strategy_runner(lambda: CSMultiFactor(n=3, every=5, history=260)), data)
    assert rep.passed, str(rep)
