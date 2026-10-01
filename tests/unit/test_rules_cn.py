"""A 股规则（P2-03）：板块、涨跌停比例与取整、手数。真实数据比对待补（P2-03b）。"""

import numpy as np
import pytest

from q6.market.rules_cn import (
    Board,
    board_of,
    limit_pct,
    limit_pct_array,
    limit_price,
    limit_prices,
    no_limit_days,
    round_buy_qty,
    round_sell_qty,
)


@pytest.mark.parametrize(
    ("code", "board"),
    [
        ("sh.600000", Board.MAIN), ("sh.601318", Board.MAIN), ("sh.603259", Board.MAIN),
        ("sh.605499", Board.MAIN), ("sz.000001", Board.MAIN), ("sz.002594", Board.MAIN),
        ("sz.003816", Board.MAIN), ("sz.001979", Board.MAIN), ("sz.300750", Board.CHINEXT),
        ("sz.301269", Board.CHINEXT), ("sh.688981", Board.STAR), ("sh.689009", Board.STAR),
    ],
)
def test_board_of(code, board):
    assert board_of(code) is board


@pytest.mark.parametrize("code", ["bj.830799", "sh.900901", "sz.200002", "sh.000300", "600000"])
def test_board_of_rejects_out_of_scope(code):
    with pytest.raises(ValueError):
        board_of(code)


def test_chinext_reform_boundary():
    assert limit_pct(Board.CHINEXT, False, "2020-08-21") == 10
    assert limit_pct(Board.CHINEXT, False, "2020-08-24") == 20
    assert limit_pct(Board.CHINEXT, True, "2020-08-21") == 5
    assert limit_pct(Board.CHINEXT, True, "2020-08-24") == 20


def test_main_and_star():
    assert limit_pct(Board.MAIN, False, "2015-07-08") == 10
    assert limit_pct(Board.MAIN, True, "2015-07-08") == 5
    assert limit_pct(Board.STAR, False, "2019-07-22") == 20
    assert limit_pct(Board.STAR, True, "2023-01-03") == 20
    with pytest.raises(ValueError):
        limit_pct(Board.STAR, False, "2019-07-19")


def test_rules_after_lockbox_not_verified():
    limit_pct(Board.MAIN, False, "2024-06-28")
    with pytest.raises(NotImplementedError):
        limit_pct(Board.MAIN, False, "2024-07-01")
    with pytest.raises(NotImplementedError):
        limit_pct_array(["sh.600000"], [False], ["2024-07-01"])


@pytest.mark.parametrize(
    ("preclose", "pct", "up", "down"),
    [
        (10.05, 10, 11.06, 9.05),  # 11.055 → 11.06；9.045 → 9.05（四舍五入，恰好在 .5 上）
        (4.45, 10, 4.90, 4.01),  # 4.895 → 4.90；4.005 → 4.01
        (0.24, 10, 0.26, 0.22),  # 0.264 → 0.26；0.216 → 0.22（低价股跌停只有 -8.33%）
        (0.22, 5, 0.23, 0.21),  # 0.231 → 0.23；0.209 → 0.21
        (13.40, 10, 14.74, 12.06),
        (12.35, 20, 14.82, 9.88),  # 14.82；9.88（整除，无舍入）
        (16.44, 5, 17.26, 15.62),  # 17.262 → 17.26；15.618 → 15.62
    ],
)
def test_limit_rounding(preclose, pct, up, down):
    u, d = limit_prices(preclose, pct)
    assert (u[0], d[0]) == (up, down)


def test_limit_price_scalar():
    assert limit_price("sz.300750", "2020-08-24", 10.05, is_st=False) == (12.06, 8.04)
    assert limit_price("sz.300750", "2020-08-21", 10.05, is_st=False) == (11.06, 9.05)


def test_non_cent_preclose_rejected():
    with pytest.raises(ValueError):
        limit_prices(10.005, 10)
    with pytest.raises(ValueError):
        limit_prices(0.0, 10)


def test_array_matches_scalar():
    rng = np.random.default_rng(0)
    codes = rng.choice(["sh.600000", "sz.000001", "sz.300750", "sh.688981"], 300)
    st = rng.random(300) < 0.3
    choices = np.array(["2019-08-01", "2020-08-21", "2020-08-24", "2023-05-05"], dtype="datetime64[D]")
    days = rng.choice(choices, 300)
    arr = limit_pct_array(codes, st, days)
    one = [limit_pct(board_of(c), bool(s), d) for c, s, d in zip(codes, st, days, strict=True)]
    assert arr.tolist() == one


def test_no_limit_days():
    assert no_limit_days(Board.MAIN, "2015-06-01") == 1
    assert no_limit_days(Board.MAIN, "2023-04-10") == 5
    assert no_limit_days(Board.CHINEXT, "2020-08-21") == 1
    assert no_limit_days(Board.CHINEXT, "2020-08-24") == 5
    assert no_limit_days(Board.STAR, "2019-07-22") == 5


def test_lots():
    assert round_buy_qty("sh.600000", 1999) == 1900
    assert round_buy_qty("sh.600000", 99) == 0
    assert round_buy_qty("sh.688981", 199) == 0
    assert round_buy_qty("sh.688981", 201) == 201
    assert round_sell_qty("sh.600000", 150, sellable=150) == 150  # 零股一次卖完
    assert round_sell_qty("sh.600000", 120, sellable=150) == 100
    assert round_sell_qty("sh.600000", 500, sellable=150) == 150  # 不超过可卖
    assert round_sell_qty("sh.688981", 150, sellable=300) == 0
    assert round_sell_qty("sh.688981", 250, sellable=300) == 250
    with pytest.raises(ValueError):
        round_buy_qty("sh.600000", -1)
