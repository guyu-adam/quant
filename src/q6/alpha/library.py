"""Predefined custom and Alpha101 factors; each row is known after that close."""

from collections.abc import Callable

import numpy as np
import pandas as pd

from . import ops

Factor = Callable[[dict[str, pd.DataFrame]], pd.DataFrame]


def mom_12_1(d):
    """12-to-1 month momentum: delayed 21-day price / delayed 252-day price - 1; customary price momentum."""
    return ops.delay(d["close"], 21) / ops.delay(d["close"], 252) - 1


def mom_6_1(d):
    """6-to-1 month momentum: delayed 21-day price / delayed 126-day price - 1; customary price momentum."""
    return ops.delay(d["close"], 21) / ops.delay(d["close"], 126) - 1


def mom_3(d):
    """Three-month price momentum: 63-day return; customary price momentum."""
    return ops.returns(d["close"], 63)


def rev_1(d):
    """One-day reversal: negative daily return; short-horizon reversal literature."""
    return -d["ret"]


def rev_5(d):
    """Five-day reversal: negative five-day return; short-horizon reversal literature."""
    return -ops.returns(d["close"], 5)


def rev_20(d):
    """Twenty-day reversal: negative twenty-day return; short-horizon reversal literature."""
    return -ops.returns(d["close"], 20)


def lowvol_20(d):
    """Low volatility: negative 20-day return deviation; low-risk anomaly."""
    return -ops.ts_std(d["ret"], 20)


def lowvol_60(d):
    """Low volatility: negative 60-day return deviation; low-risk anomaly."""
    return -ops.ts_std(d["ret"], 60)


def lottery_20(d):
    """Lottery avoidance: negative best 20-day return; Bali et al. lottery demand."""
    return -ops.ts_max(d["ret"], 20)


def low_turn_20(d):
    """Low turnover: negative 20-day mean turnover; turnover anomaly."""
    return -ops.ts_mean(d["turn"], 20)


def turn_vol_20(d):
    """Stable turnover: negative 20-day turnover deviation; turnover anomaly."""
    return -ops.ts_std(d["turn"], 20)


def amihud_20(d):
    """Illiquidity premium: 20-day mean absolute return per amount scaled by 1e8; Amihud (2002)."""
    return ops.ts_mean(d["ret"].abs() / d["amount"] * 1e8, 20)


def size(d):
    """Small size: negative log float capitalization; Banz (1981)."""
    return -np.log(d["float_cap"].where(d["float_cap"] > 0))


def pv_corr_10(d):
    """Price-volume divergence: negative ten-day close-volume correlation; price-volume effect."""
    return -ops.ts_corr(d["close"], d["volume"], 10)


def range_20(d):
    """Narrow range: negative mean 20-day daily price range over close; range anomaly."""
    return -ops.ts_mean((d["high"] - d["low"]) / d["close"], 20)


def trend_ma_20_60(d):
    """Moving-average trend: 20-day mean close / 60-day mean close - 1; time-series momentum."""
    return ops.ts_mean(d["close"], 20) / ops.ts_mean(d["close"], 60) - 1


def alpha_2(d):
    """Alpha101 #2: (-1 * correlation(rank(delta(log(volume), 2)), rank(((close - open) / open)), 6)); Kakushadze 2016, arXiv:1601.00991."""  # noqa: E501
    return -ops.ts_corr(
        ops.cs_rank(ops.delta(np.log(d["volume"].where(d["volume"] > 0)), 2)),
        ops.cs_rank((d["close"] - d["open"]) / d["open"]),
        6,
    )


def alpha_3(d):
    """Alpha101 #3: (-1 * correlation(rank(open), rank(volume), 10)); Kakushadze 2016, arXiv:1601.00991."""
    return -ops.ts_corr(ops.cs_rank(d["open"]), ops.cs_rank(d["volume"]), 10)


def alpha_4(d):
    """Alpha101 #4: (-1 * Ts_Rank(rank(low), 9)); Kakushadze 2016, arXiv:1601.00991."""
    return -ops.ts_rank(ops.cs_rank(d["low"]), 9)


def alpha_6(d):
    """Alpha101 #6: (-1 * correlation(open, volume, 10)); Kakushadze 2016, arXiv:1601.00991."""
    return -ops.ts_corr(d["open"], d["volume"], 10)


def alpha_12(d):
    """Alpha101 #12: (sign(delta(volume, 1)) * (-1 * delta(close, 1))); Kakushadze 2016, arXiv:1601.00991."""
    return ops.sign(ops.delta(d["volume"], 1)) * -ops.delta(d["close"], 1)


def alpha_13(d):
    """Alpha101 #13: (-1 * rank(covariance(rank(close), rank(volume), 5))); Kakushadze 2016, arXiv:1601.00991."""  # noqa: E501
    return -ops.cs_rank(ops.ts_cov(ops.cs_rank(d["close"]), ops.cs_rank(d["volume"]), 5))


def alpha_14(d):
    """Alpha101 #14: ((-1 * rank(delta(returns, 3))) * correlation(open, volume, 10)); Kakushadze 2016, arXiv:1601.00991."""  # noqa: E501
    return -ops.cs_rank(ops.delta(d["ret"], 3)) * ops.ts_corr(d["open"], d["volume"], 10)


def alpha_15(d):
    """Alpha101 #15: (-1 * sum(rank(correlation(rank(high), rank(volume), 3)), 3)); Kakushadze 2016, arXiv:1601.00991."""  # noqa: E501
    return -ops.ts_sum(ops.cs_rank(ops.ts_corr(ops.cs_rank(d["high"]), ops.cs_rank(d["volume"]), 3)), 3)


def alpha_16(d):
    """Alpha101 #16: (-1 * rank(covariance(rank(high), rank(volume), 5))); Kakushadze 2016, arXiv:1601.00991."""  # noqa: E501
    return -ops.cs_rank(ops.ts_cov(ops.cs_rank(d["high"]), ops.cs_rank(d["volume"]), 5))


def alpha_18(d):
    """Alpha101 #18: (-1 * rank(((stddev(abs((close - open)), 5) + (close - open)) + correlation(close, open, 10)))); Kakushadze 2016, arXiv:1601.00991."""  # noqa: E501
    co = d["close"] - d["open"]
    return -ops.cs_rank(ops.ts_std(co.abs(), 5) + co + ops.ts_corr(d["close"], d["open"], 10))


def alpha_33(d):
    """Alpha101 #33: rank((-1 * ((1 - (open / close))^1))); Kakushadze 2016, arXiv:1601.00991."""
    return ops.cs_rank(-(1 - d["open"] / d["close"]))


def alpha_34(d):
    """Alpha101 #34: rank(((1 - rank((stddev(returns, 2) / stddev(returns, 5)))) + (1 - rank(delta(close, 1))))); Kakushadze 2016, arXiv:1601.00991."""  # noqa: E501
    return ops.cs_rank(
        (1 - ops.cs_rank(ops.ts_std(d["ret"], 2) / ops.ts_std(d["ret"], 5)))
        + (1 - ops.cs_rank(ops.delta(d["close"], 1)))
    )


def alpha_44(d):
    """Alpha101 #44: (-1 * correlation(high, rank(volume), 5)); Kakushadze 2016, arXiv:1601.00991."""
    return -ops.ts_corr(d["high"], ops.cs_rank(d["volume"]), 5)


def alpha_101(d):
    """Alpha101 #101: ((close - open) / ((high - low) + .001)); Kakushadze 2016, arXiv:1601.00991."""
    return (d["close"] - d["open"]) / (d["high"] - d["low"] + 0.001)


FACTORS: dict[str, Factor] = {
    f.__name__: f
    for f in (
        mom_12_1,
        mom_6_1,
        mom_3,
        rev_1,
        rev_5,
        rev_20,
        lowvol_20,
        lowvol_60,
        lottery_20,
        low_turn_20,
        turn_vol_20,
        amihud_20,
        size,
        pv_corr_10,
        range_20,
        trend_ma_20_60,
        alpha_2,
        alpha_3,
        alpha_4,
        alpha_6,
        alpha_12,
        alpha_13,
        alpha_14,
        alpha_15,
        alpha_16,
        alpha_18,
        alpha_33,
        alpha_34,
        alpha_44,
        alpha_101,
    )
}
