import numpy as np
import pandas as pd

from q6.lint.truncation_test import check_lookahead
from q6.research.metrics import forward_returns


def test_metrics_forward_returns_lookahead_is_detected():
    close = pd.DataFrame({"asset": np.arange(1.0, 101.0)})
    report = check_lookahead(
        lambda data: forward_returns(data, 1), close, min_rows=30, n_cuts=3, nan_probe=False
    )
    assert not report.passed
    assert report.failures
