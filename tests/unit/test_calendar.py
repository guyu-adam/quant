from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from q6.market.calendar import TradingCalendar


@pytest.fixture
def calendar() -> TradingCalendar:
    return TradingCalendar(
        ["2020-01-22", date(2020, 1, 23), "2020-02-03", "2020-02-04", "2020-02-03"]
    )


def test_normalizes_sorts_deduplicates_and_is_read_only(calendar: TradingCalendar) -> None:
    assert calendar.first == pd.Timestamp("2020-01-22")
    assert calendar.last == pd.Timestamp("2020-02-04")
    with pytest.raises(ValueError):
        calendar._days[0] = np.datetime64("2020-01-01")


def test_navigation(calendar: TradingCalendar) -> None:
    assert calendar.next("2020-01-23") == pd.Timestamp("2020-02-03")
    assert calendar.prev("2020-02-03") == pd.Timestamp("2020-01-23")
    assert calendar.offset(pd.Timestamp("2020-02-03"), 0) == pd.Timestamp("2020-02-03")
    assert calendar.offset("2020-02-03", 1) == pd.Timestamp("2020-02-04")
    assert calendar.range("2020-01-23", "2020-02-03").equals(
        pd.DatetimeIndex(["2020-01-23", "2020-02-03"])
    )


def test_rejects_non_trading_offset_and_out_of_bounds(calendar: TradingCalendar) -> None:
    with pytest.raises(ValueError):
        calendar.offset("2020-01-24", 0)
    with pytest.raises(IndexError):
        calendar.next("2020-02-04")
    with pytest.raises(IndexError):
        calendar.prev("2020-01-22")
    with pytest.raises(IndexError):
        calendar.offset("2020-02-04", 1)


def test_csv_and_parquet_round_trip(tmp_path, calendar: TradingCalendar) -> None:
    csv_path = tmp_path / "dates.csv"
    csv_path.write_text(
        "calendar_date,is_trading_day\n"
        "2020-01-22,1\n2020-01-23,1\n2020-01-24,0\n2020-02-03,1\n2020-02-04,1\n",
        encoding="utf-8",
    )
    from_csv = TradingCalendar.from_csv(csv_path)
    assert from_csv.range("2020-01-01", "2020-12-31").equals(
        pd.DatetimeIndex(["2020-01-22", "2020-01-23", "2020-02-03", "2020-02-04"])
    )
    parquet_path = tmp_path / "dates.parquet"
    from_csv.to_parquet(parquet_path)
    restored = TradingCalendar.from_parquet(parquet_path)
    assert restored.range("2020-01-01", "2020-12-31").equals(
        from_csv.range("2020-01-01", "2020-12-31")
    )


def test_recorded_baostock_fixture() -> None:
    fixture_path = Path(__file__).parents[1] / "fixtures" / "trade_dates.csv"
    calendar = TradingCalendar.from_csv(fixture_path)
    assert calendar.is_trading_day("2015-06-15")
    assert calendar.is_trading_day("2020-01-23")
    assert not calendar.is_trading_day("2020-01-24")
    assert not calendar.is_trading_day("2020-02-02")
    assert calendar.is_trading_day("2020-02-03")
    assert not calendar.is_trading_day("2008-10-01")
    assert calendar.is_trading_day("2008-10-07")
    assert calendar.next("2020-01-23") == pd.Timestamp("2020-02-03")
    assert calendar.prev("2020-02-03") == pd.Timestamp("2020-01-23")
    assert calendar.next("2018-12-28") == pd.Timestamp("2019-01-02")
