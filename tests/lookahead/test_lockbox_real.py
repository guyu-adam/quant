"""锁箱期守卫在真实快照上的验证（P2-20）。快照环境变量的约定与 test_real_snapshot.py 相同。"""

import os
from pathlib import Path

import pandas as pd
import pytest

from q6.data import lockbox
from q6.data.lockbox import LOCKBOX_TS, LockboxError

SNAP = os.environ.get("Q6_SNAPSHOT")
REQUIRE = os.environ.get("Q6_REQUIRE_SNAPSHOT") == "1"


@pytest.fixture(scope="module")
def snapshot():
    if not SNAP:
        if REQUIRE:
            pytest.fail("Q6_REQUIRE_SNAPSHOT=1 但 Q6_SNAPSHOT 未设置：验收模式不允许跳过真实快照测试")
        pytest.skip("Q6_SNAPSHOT 未设置")
    lockbox.relock()
    return Path(os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots")), SNAP


def test_real_snapshot_physically_contains_lockbox(snapshot):
    """前提：快照里确实有锁箱期数据（否则下面的测试什么也证明不了）。只看清单，不读数据。"""
    import json

    from q6.data.snapshot import _resolve

    directory, _ = _resolve(*snapshot)
    files = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["files"]
    assert {"daily/year=2025.parquet", "daily/year=2026.parquet"} <= {f["path"] for f in files}


def test_real_2024_truncated(snapshot):
    from q6.data.snapshot import load_snapshot

    daily = load_snapshot(*snapshot, years=(2024, 2024), columns=("close",))["daily"]
    assert daily["date"].max() == pd.Timestamp("2024-06-28")  # 锁箱期前最后一个交易日
    assert (daily["date"] < LOCKBOX_TS).all()


def test_real_aux_tables_truncated(snapshot):
    from q6.data.snapshot import load_snapshot

    t = load_snapshot(*snapshot, tables=("calendar", "quarantine", "universe_monthly"))
    assert t["calendar"]["date"].max() < LOCKBOX_TS
    assert t["quarantine"].empty or t["quarantine"]["date"].max() < LOCKBOX_TS
    assert t["universe_monthly"]["month_end"].max() < LOCKBOX_TS
    assert t["universe_monthly"]["update_date"].max() < LOCKBOX_TS


@pytest.mark.parametrize("years", [(2024, 2025), (2025, 2025), (2026, 2026)])
def test_real_lockbox_years_rejected(snapshot, years):
    from q6.data.snapshot import load_snapshot

    with pytest.raises(LockboxError):
        load_snapshot(*snapshot, years=years, columns=("close",))
