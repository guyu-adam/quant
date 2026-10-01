"""Full feature truncation check on the pinned real market snapshot."""

import os

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from q6.alpha.inputs import build_inputs
from q6.data.snapshot import load_snapshot
from q6.lint.truncation_test import check_lookahead
from q6.ml.features import build_features

from .test_real_snapshot import SNAP, require_snapshot_configured


@pytest.fixture(scope="module")
def feature_data():
    require_snapshot_configured()
    root = os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots")
    # Load through the snapshot API first so manifest, hashes, date limits and lockbox are checked,
    # while avoiding materializing the entire market's price columns in this RSS-limited process.
    code_frame = load_snapshot(
        root,
        SNAP,
        ("daily",),
        years=(2015, 2016),
        # One date is enough to obtain the actual universe for deterministic 300-code sampling.
        date_range=("2015-05-04", "2015-05-04"),
        columns=(),
    )["daily"]
    codes = sorted(code_frame["code"].unique())
    indices = np.linspace(0, len(codes) - 1, min(300, len(codes)), dtype=int)
    sampled_codes = [codes[i] for i in indices]
    del code_frame, codes
    from pathlib import Path

    snapshot_dir = Path(root) / SNAP
    columns = [
        "date", "code", "open_hfq", "high_hfq", "low_hfq", "close_hfq", "ret", "amount", "turn",
        "volume", "adj_factor", "tradestatus", "bad",
    ]
    date_filters = [
        ("date", ">=", pd.Timestamp("2015-05-01").to_pydatetime()),
        ("date", "<=", pd.Timestamp("2016-02-29").to_pydatetime()),
    ]
    daily = pd.concat(
        [
            pq.read_table(
                snapshot_dir / f"daily/year={year}.parquet",
                columns=columns,
                filters=[*date_filters, ("code", "in", sampled_codes)],
            ).to_pandas()
            for year in (2015, 2016)
        ],
        ignore_index=True,
    )
    daily = daily.sort_values(["date", "code"], kind="mergesort").reset_index(drop=True)
    wide = build_inputs(daily)
    return wide


def test_all_features_real_snapshot_truncation(feature_data):
    report = check_lookahead(build_features, feature_data, name="features_real", lag=0, n_cuts=1)
    assert report.passed, str(report)
