"""P2-03 涨跌停价与真实快照比对（需要 Q6_SNAPSHOT；验收模式缺快照即失败，开关同 test_real_snapshot）。

没有交易所公布的逐日涨停价可对照，所以用两条能从数据本身检验的性质：
1. 正常交易日（上市满 5 日、可交易、非异常）最高价不超过算出的涨停价、最低价不低于跌停价。
   取整算错的典型特征是"恰好越界 1 分钱"，这种情况必须为 0；其余越界只允许是规则表明确不建模的特例
   （重组 / 恢复上市复牌首日、退市整理期首日等），其涨跌幅远超比例限制。
2. 取整方向：前收 × (1±比例) 恰好落在半分上的日子，若交易所是四舍五入（half-up），涨停价可以成交到，
   最高价会出现恰好等于 half-up 价的情况；跌停一侧最低价不会低于 half-up 跌停价。

全样本（2005–2024H1）结果见 PROGRESS P2-03 节；这里只取 2015（涨跌停最密集）
和 2020（创业板改 20%）两年，控制内存。
"""

import os
from functools import cache
from pathlib import Path

import numpy as np
import pytest

from q6.market import rules_cn as R

from .test_real_snapshot import REQUIRE, SNAP

COLS = ("high", "low", "preclose", "is_st", "listed_days", "tradestatus", "bad")


@pytest.fixture(autouse=True, scope="module")
def _free_cache():
    yield
    _year.cache_clear()  # 同一进程后面还有别的真实快照测试，别让两年数据一直占着 RSS


@cache
def _year(y):
    from q6.data.snapshot import load_snapshot

    if not SNAP:
        if REQUIRE:
            pytest.fail("Q6_REQUIRE_SNAPSHOT=1 但 Q6_SNAPSHOT 未设置：验收模式不允许跳过真实快照测试")
        pytest.skip("Q6_SNAPSHOT 未设置")
    root = Path(os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots"))
    d = load_snapshot(root, SNAP, ("daily",), years=(y, y), columns=COLS)["daily"]
    d = d[(d.tradestatus == 1) & (~d.bad) & (d.listed_days > 5)]
    pct = R.limit_pct_array(d.code.values, d.is_st.values, d.date.values)
    up, dn = R.limit_prices(d.preclose.values, pct)
    return d, pct, up, dn


@pytest.mark.parametrize("year", [2015, 2020])
def test_real_prices_within_limits(year):
    d, pct, up, dn = _year(year)
    hi, lo = d.high.to_numpy(), d.low.to_numpy()
    over = hi - up
    under = dn - lo
    one_tick = (np.abs(over - 0.01) < 1e-9) | (np.abs(under - 0.01) < 1e-9)
    assert one_tick.sum() == 0, d[one_tick].head(20).to_string()
    bad = (over > 1e-9) | (under > 1e-9)
    # 剩余越界必须是规则表外的特例：当日振幅远超比例限制（> 2 倍）
    rng = np.maximum(hi / d.preclose.to_numpy() - 1, 1 - lo / d.preclose.to_numpy())
    assert np.all(rng[bad] > 2 * pct[bad] / 100), d[bad & (rng <= 2 * pct / 100)].to_string()
    assert bad.sum() <= 10, d[bad].to_string()


@pytest.mark.parametrize("year", [2015, 2020])
def test_real_half_cent_rounds_up(year):
    d, pct, up, dn = _year(year)
    pc = np.rint(d.preclose.to_numpy() * 100).astype(np.int64)
    half_up = (pc * (100 + pct)) % 100 == 50
    half_dn = (pc * (100 - pct)) % 100 == 50
    assert half_up.sum() > 1000
    hit_up = half_up & (np.abs(d.high.to_numpy() - up) < 1e-9)
    assert hit_up.sum() > 100  # half-up 涨停价确实成交过：若交易所是 half-down，这个价位不可能出现
    below_dn = half_dn & (d.low.to_numpy() < dn - 1e-9)
    assert below_dn.sum() == 0


def test_real_chinext_reform_visible():
    """创业板上市满 5 日的正常交易日：2020-08-24 之前最高价从不超过"按 10% 算的涨停价"，之后大量超过。

    比较对象是取整后的 10% 涨停价而不是固定涨幅：低价股取整后可以远超 10%（前收 0.17 → 涨停 0.19，+11.8%）。
    """
    d, pct, up, dn = _year(2020)
    gem = d.code.str.startswith("sz.30").to_numpy()
    before = (d.date < "2020-08-24").to_numpy()
    up10, _ = R.limit_prices(d.preclose.to_numpy(), np.full(len(d), 10))
    above = d.high.to_numpy() > up10 + 1e-9
    assert (above & gem & before).sum() == 0
    assert (above & gem & ~before).sum() > 100
