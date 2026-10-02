import numpy as np
import pandas as pd
import pytest

from q6.market.calendar import TradingCalendar, _date


def test_calendar_normalization_search_and_boundaries(tmp_path):
    cal = TradingCalendar(["2024-01-03", pd.Timestamp("2024-01-02 13:00"), "2024-01-03"])
    assert cal.is_trading_day("2024-01-03")
    assert not cal.is_trading_day("2024-01-04")
    assert cal.next("2024-01-02") == pd.Timestamp("2024-01-03")
    assert cal.prev("2024-01-03") == pd.Timestamp("2024-01-02")
    assert cal.range("2024-01-02", "2024-01-03").tolist() == list(
        pd.to_datetime(["2024-01-02", "2024-01-03"])
    )
    assert len(cal.range("2024-01-04", "2024-01-03")) == 0
    assert cal.offset("2024-01-02", 1) == pd.Timestamp("2024-01-03")
    assert cal.first == pd.Timestamp("2024-01-02") and cal.last == pd.Timestamp("2024-01-03")
    with pytest.raises(IndexError, match="index out of range"):
        cal.next("2024-01-03")
    with pytest.raises(IndexError, match="index out of range"):
        cal.prev("2024-01-02")
    with pytest.raises(ValueError, match="not a trading day"):
        cal.offset("2024-01-01", 0)
    with pytest.raises(ValueError, match="positive integer"):
        cal.next("2024-01-02", 0)
    with pytest.raises(ValueError, match="positive integer"):
        cal.prev("2024-01-02", 0)
    assert cal._days.flags.writeable is False
    assert _date(np.datetime64("2024-01-02T12:00")) == np.datetime64("2024-01-02", "D")
    with pytest.raises(TypeError, match="unsupported date value"):
        _date(123)


def test_calendar_csv_roundtrip_and_validation(tmp_path):
    csv = tmp_path / "calendar.csv"
    csv.write_text("calendar_date,is_trading_day\n2024-01-01,0\n2024-01-02,true\n2024-01-03,1\n")
    cal = TradingCalendar.from_csv(csv)
    assert cal.range("2024-01-01", "2024-01-03").tolist() == list(
        pd.to_datetime(["2024-01-02", "2024-01-03"])
    )
    pq = tmp_path / "calendar.parquet"
    cal.to_parquet(pq)
    assert TradingCalendar.from_parquet(pq).last == pd.Timestamp("2024-01-03")
    bad = tmp_path / "bad.csv"
    bad.write_text("date\n2024-01-01\n")
    with pytest.raises(ValueError, match="calendar_date"):
        TradingCalendar.from_csv(bad)
    badpq = tmp_path / "bad.parquet"
    pd.DataFrame({"date": ["2024-01-01"]}).to_parquet(badpq)
    with pytest.raises(ValueError, match="calendar_date"):
        TradingCalendar.from_parquet(badpq)


def test_calendar_baostock_success_uses_trading_rows(monkeypatch):
    import sys
    from types import SimpleNamespace

    class Result:
        error_code, error_msg = "0", ""
        def __init__(self):
            self.rows = iter([["2024-01-02", "1"], ["2024-01-03", "0"], ["2024-01-04", "1"]])
        def next(self):
            self.row = next(self.rows, None)
            return self.row is not None
        def get_row_data(self):
            return self.row

    calls = []
    fake = SimpleNamespace(login=lambda: SimpleNamespace(error_code="0", error_msg=""),
                           query_trade_dates=lambda **kw: Result(), logout=lambda: calls.append("logout"))
    monkeypatch.setitem(sys.modules, "baostock", fake)
    cal = TradingCalendar.from_baostock("2024-01-01", "2024-01-05")
    assert cal.range("2024-01-01", "2024-01-05").tolist() == list(
        pd.to_datetime(["2024-01-02", "2024-01-04"])
    )
    assert calls == ["logout"]
