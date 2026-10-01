from q6.alpha import ops
from q6.lint.truncation_test import check_lookahead

from .samples import make_data


def test_ops_are_truncation_safe():
    data = make_data()
    unary = {
        "delay": lambda x: ops.delay(x, 3), "delta": lambda x: ops.delta(x, 3),
        "ts_sum": lambda x: ops.ts_sum(x, 5), "ts_mean": lambda x: ops.ts_mean(x, 5),
        "ts_std": lambda x: ops.ts_std(x, 5), "ts_min": lambda x: ops.ts_min(x, 5),
        "ts_max": lambda x: ops.ts_max(x, 5), "ts_argmin": lambda x: ops.ts_argmin(x, 5),
        "ts_argmax": lambda x: ops.ts_argmax(x, 5), "ts_rank": lambda x: ops.ts_rank(x, 5),
        "ts_zscore": lambda x: ops.ts_zscore(x, 5), "ts_product": lambda x: ops.ts_product(x, 5),
        "decay_linear": lambda x: ops.decay_linear(x, 5), "ewm_mean": lambda x: ops.ewm_mean(x, 3),
        "returns": lambda x: ops.returns(x, 2), "cs_rank": ops.cs_rank, "cs_zscore": ops.cs_zscore,
        "cs_demean": ops.cs_demean, "cs_scale": ops.cs_scale,
        "cs_winsorize": ops.cs_winsorize, "sign": ops.sign,
        "signed_power": lambda x: ops.signed_power(x, 2),
    }
    for name, operator in unary.items():
        def fn(d, op=operator):
            return op(d["close"])

        rep = check_lookahead(fn, data, name=name, lag=0)
        assert rep.passed, str(rep)
    for name, operator in {"ts_corr": ops.ts_corr, "ts_cov": ops.ts_cov, "safe_div": ops.safe_div}.items():
        def fn(d, op=operator):
            if op is ops.safe_div:
                return op(d["close"], d["volume"])
            return op(d["close"], d["volume"], 5)

        rep = check_lookahead(fn, data, name=name, lag=0)
        assert rep.passed, str(rep)


def test_ops_truncation_detects_future_leak():
    data = make_data()
    def bad(d):
        return d["close"].shift(-1)  # Deliberately reads tomorrow's value.

    rep = check_lookahead(bad, data, name="bad_shift_negative", lag=0)
    assert not rep.passed, str(rep)
