"""研究视界（P2-20 walk-forward）在真实快照上的验证：训练步骤读不到训练窗口之后的数据。

快照环境变量约定同 test_real_snapshot.py。
"""

import os
from pathlib import Path

import pandas as pd
import pytest

from q6.data import lockbox
from q6.data.lockbox import HorizonError, research_horizon

SNAP = os.environ.get("Q6_SNAPSHOT")
REQUIRE = os.environ.get("Q6_REQUIRE_SNAPSHOT") == "1"
H = pd.Timestamp("2012-06-29")


@pytest.fixture(scope="module")
def snapshot():
    if not SNAP:
        if REQUIRE:
            pytest.fail("Q6_REQUIRE_SNAPSHOT=1 但 Q6_SNAPSHOT 未设置：验收模式不允许跳过真实快照测试")
        pytest.skip("Q6_SNAPSHOT 未设置")
    lockbox.relock()
    return Path(os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots")), SNAP


def test_unbounded_reads_are_truncated_at_horizon(snapshot):
    from q6.data.snapshot import load_snapshot

    with research_horizon(H):
        daily = load_snapshot(*snapshot, years=(2012, 2012), columns=("close",))["daily"]
        meta = load_snapshot(*snapshot, tables=("calendar", "universe_monthly"))
    assert pd.to_datetime(daily["date"]).max() == H  # 截到视界当天（2012-06-29 是交易日）
    cal = meta["calendar"]
    assert pd.to_datetime(cal[cal.columns[0]]).max() <= H
    assert pd.to_datetime(meta["universe_monthly"]["month_end"]).max() <= H


def test_explicit_reads_beyond_horizon_raise(snapshot):
    from q6.data.snapshot import load_snapshot

    with research_horizon(H):
        with pytest.raises(HorizonError):
            load_snapshot(*snapshot, years=(2013, 2013), columns=("close",))
        with pytest.raises(HorizonError):
            load_snapshot(*snapshot, date_range=("2012-06-01", "2012-07-31"), columns=("close",))


def test_feed_built_inside_horizon_cannot_reach_test_period(snapshot):
    from q6.engine.feed import SnapshotFeed

    with research_horizon(H):
        feed = SnapshotFeed(*snapshot)
        assert feed.calendar.max() <= H
        with pytest.raises((HorizonError, ValueError)):
            next(feed.segments("2012-07-02", "2012-12-31", 10))
