import numpy as np
import pandas as pd
import pytest

from q6.core.pit import Panel
from q6.engine.feed import SnapshotFeed, _universe_matrix


def test_universe_matrix_uses_latest_month_end_and_ignores_unknown_codes():
    dates = pd.DatetimeIndex(["2024-01-30", "2024-01-31", "2024-02-01"])
    uni = pd.DataFrame({"month_end": ["2024-01-31", "2024-01-31", "2024-02-29"],
                        "code": ["A", "MISSING", "B"]})
    got = _universe_matrix(uni, dates, ("A", "B"))
    np.testing.assert_array_equal(got, [[False, False], [True, False], [True, False]])
    assert _universe_matrix(uni.iloc[:0], dates, ("A", "B")).shape == (3, 2)


def test_snapshot_feed_segments_keeps_holdings_and_builds_pit_panels(monkeypatch):
    import q6.engine.feed as feed_module

    def load_snapshot(root, snapshot_id, tables, **kwargs):
        assert snapshot_id == "fixture"
        if tables == ("calendar",):
            return {"calendar": pd.DataFrame({"date": ["2024-01-02", "2024-01-03", "2024-01-04"]})}
        if tables == ("universe_monthly",):
            return {"universe_monthly": pd.DataFrame({"month_end": ["2023-12-29"], "code": ["A"]})}
        raise AssertionError((tables, kwargs))

    monkeypatch.setattr(feed_module, "load_snapshot", load_snapshot)
    feed = SnapshotFeed("unused", "fixture", extra_fields=("alpha", "alpha"))
    assert feed.fields[-1] == "alpha" and feed.fields.count("alpha") == 1
    assert feed._members(pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-04")) == {"A"}
    assert feed._members(pd.Timestamp("2023-12-01"), pd.Timestamp("2023-12-10")) == set()
    observed = []
    def panel(lo, hi, codes):
        observed.append((lo, hi, set(codes)))
        dates = pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-04"])
        return dates, Panel(dates, ["A", "B"], {"close": np.ones((3, 2))})
    monkeypatch.setattr(feed, "_load_panel", panel)
    keep_calls = []
    seg = next(feed.segments("2024-01-03", "2024-01-04", 1, keep=lambda: keep_calls.append(1) or ["B"]))
    assert seg.first == 1
    assert seg.panel.symbols == ("A", "B")
    np.testing.assert_array_equal(seg.universe, [[True, False], [True, False], [True, False]])
    assert keep_calls == [1] and observed[0][2] == {"A", "B"}
    with pytest.raises(ValueError, match="没有交易日"):
        list(feed.segments("2024-02-01", "2024-02-02", 0))


def test_load_panel_assembles_requested_columns(monkeypatch):
    import q6.engine.feed as feed_module

    feed = SnapshotFeed.__new__(SnapshotFeed)
    feed.root, feed.snapshot_id = "unused", "fixture"
    feed.fields = ("close", "volume")
    dates = ["2024-01-02", "2024-01-03"]

    def load_snapshot(root, snapshot_id, tables, **kwargs):
        assert tables == ("daily",) and kwargs["years"] == (2024, 2024)
        assert set(kwargs["codes"]) == {"A"}
        if kwargs["columns"] == ():
            return {"daily": pd.DataFrame({"date": dates, "code": ["A", "A"]})}
        return {"daily": pd.DataFrame({"date": dates, "code": ["A", "A"],
                                        "close": [10.0, 11.0], "volume": [100, 120]})}

    monkeypatch.setattr(feed_module, "load_snapshot", load_snapshot)
    got_dates, panel = feed._load_panel(pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03"), {"A"})
    assert got_dates.tolist() == list(pd.to_datetime(dates))
    np.testing.assert_array_equal(panel.row("close", 1), [11.0])
    np.testing.assert_array_equal(panel.row("volume", 0), [100.0])
