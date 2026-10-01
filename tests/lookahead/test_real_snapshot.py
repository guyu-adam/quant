"""端到端：真实快照 → Panel → 截断测试。

需要环境变量 Q6_SNAPSHOT（快照 ID；可选 Q6_SNAPSHOT_ROOT，默认 data/snapshots）。
未设置时 skip——verify 脚本的第 6 步会显式打印 SKIP，不会静默。
只取 2015-05 ~ 2016-02：覆盖 2015 股灾、千股停牌、2016-01 熔断，缺失与停牌最密集，最容易暴露 bfill 类问题。
窗口和列都收窄是为了守住进程 RSS ≤512MB（两年全列读入时峰值 845MB）。
"""

import os
from pathlib import Path

import pytest

from q6.core.pit import Panel
from q6.lint.truncation_test import check_lookahead

from .samples import CLEAN, LAG, LEAKY

SNAP = os.environ.get("Q6_SNAPSHOT")
pytestmark = pytest.mark.skipif(not SNAP, reason="Q6_SNAPSHOT 未设置")


@pytest.fixture(scope="module")
def real_data():
    from q6.data.snapshot import load_snapshot

    root = Path(os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots"))
    daily = load_snapshot(root, SNAP, ("daily",), years=(2015, 2016),
                          columns=("close_hfq", "volume", "tradable"))["daily"]
    daily = daily[(daily["date"] >= "2015-05-01") & (daily["date"] <= "2016-02-29")]
    # 停牌行的价格是占位值：信号输入只用可交易行，其余置 NaN（与引擎口径一致）
    daily = daily.assign(close_sig=daily["close_hfq"].where(daily["tradable"]),
                         vol_sig=daily["volume"].astype("float64").where(daily["tradable"]))
    p = Panel.from_long(daily, ["close_sig", "vol_sig"])
    v = p.view(len(p) - 1)
    return {"close": v.frame("close_sig"), "volume": v.frame("vol_sig")}


def test_real_shape(real_data):
    c = real_data["close"]
    assert len(c) > 190 and c.shape[1] > 800
    assert c.isna().to_numpy().mean() > 0.01  # 真实数据确实有停牌 / 未上市缺失


@pytest.mark.parametrize("name", sorted(LEAKY))
def test_real_leaky_detected(real_data, name):
    assert not check_lookahead(LEAKY[name], real_data, lag=LAG.get(name, 0), n_cuts=4).passed


@pytest.mark.parametrize("name", sorted(CLEAN))
def test_real_clean_passes(real_data, name):
    rep = check_lookahead(CLEAN[name], real_data, lag=LAG.get(name, 0), n_cuts=4)
    assert rep.passed, str(rep)
