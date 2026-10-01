import numpy as np

from lookahead.samples import data_with_fields
from q6.lint.truncation_test import check_lookahead, strategy_runner
from q6.strategy.pairs import PairsDistance


def frames():
    d = data_with_fields()
    c = d["close"]
    return {**d, "close_hfq": c, "close": c, "preclose": c.shift(1).fillna(c).round(2),
            "tradestatus": c * 0 + 1, "is_st": c * 0, "is_new": c * 0}


def test_pairs_passes_strategy_runner_truncation():
    rep = check_lookahead(strategy_runner(lambda: PairsDistance(
        formation=50, trading=30, n_pairs=2, pool=6, entry=0.2, exit=0.05, stop=1.0)), frames())
    assert rep.passed, str(rep)


def test_frozen_formation_parameters_ignore_post_formation_noise():
    original = frames()
    cut = 79
    changed = {k: v.copy() for k, v in original.items()}
    rng = np.random.default_rng(22)
    for value in changed.values():
        value.iloc[cut + 1:] = rng.normal(50, 10, value.iloc[cut + 1:].shape)
    strategy_a = PairsDistance(formation=50, trading=1000, n_pairs=2, pool=6)
    strategy_b = PairsDistance(formation=50, trading=1000, n_pairs=2, pool=6)
    strategy_runner(lambda: strategy_a)(original)
    strategy_runner(lambda: strategy_b)(changed)
    assert strategy_a._pairs == strategy_b._pairs
