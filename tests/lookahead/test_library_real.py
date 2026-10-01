"""Full factor truncation against the pinned 2015–2016 snapshot."""

import numpy as np
import pytest

from q6.alpha.inputs import build_inputs
from q6.alpha.library import FACTORS
from q6.data.snapshot import load_snapshot
from q6.lint.truncation_test import check_lookahead

from .test_real_snapshot import SNAP, require_snapshot_configured


@pytest.fixture(scope="module")
def factor_data():
    # Depend on the established fixture so验收缺快照行为与既有真实快照测试一致。
    require_snapshot_configured()
    root = __import__("os").environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots")
    daily = load_snapshot(
        root,
        SNAP,
        ("daily",),
        years=(2015, 2016),
        date_range=("2015-05-01", "2016-02-29"),
        columns=(
            "open_hfq",
            "high_hfq",
            "low_hfq",
            "close_hfq",
            "ret",
            "amount",
            "turn",
            "volume",
            "adj_factor",
            "tradestatus",
            "bad",
        ),
    )["daily"]
    wide = build_inputs(daily)
    # Spread 300 real securities across the universe to stay below the RSS cap.
    indices = np.linspace(0, len(wide["close"].columns) - 1, min(300, len(wide["close"].columns)), dtype=int)
    return {key: value.iloc[:, indices] for key, value in wide.items()}


@pytest.mark.parametrize("name", sorted(FACTORS), ids=sorted(FACTORS))
def test_library_real_snapshot_truncation(factor_data, name):
    report = check_lookahead(lambda d: FACTORS[name](d), factor_data, lag=0, n_cuts=4)
    assert report.passed, str(report)
