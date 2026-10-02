import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from q6.ml.cv import PurgedKFold
from q6.ml.labels import forward_rank_label


def _prices(n=10):
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {"a": np.arange(100, 100 + n), "b": np.arange(100, 100 + n) * 1.1,
         "c": np.arange(100, 100 + n)[::-1] + 100}, index=idx, dtype=float
    )


def test_labels_hand_calculated_horizon_zero():
    prices = _prices()
    labels, ends = forward_rank_label(prices, 0)
    for i in range(len(prices) - 1):
        returns = prices.iloc[i + 1] / prices.iloc[i + 1] - 1
        expected = returns.rank(pct=True)
        pd.testing.assert_series_equal(labels.iloc[i], expected, check_names=False)
        assert ends.iloc[i] == prices.index[i + 1]
    assert labels.iloc[-1].isna().all() and pd.isna(ends.iloc[-1])


def test_labels_hand_calculated_horizon_two():
    prices = _prices()
    labels, _ = forward_rank_label(prices, 2)
    i = 1
    returns = prices.iloc[i + 3] / prices.iloc[i + 1] - 1
    pd.testing.assert_series_equal(labels.iloc[i], returns.rank(pct=True), check_names=False)


def test_labels_horizon_out_of_range_is_nan_nat():
    labels, ends = forward_rank_label(_prices(), 20)
    assert labels.isna().all().all()
    assert ends.isna().all()


def test_labels_eligible_masks_before_ranking():
    prices = _prices()
    eligible = pd.DataFrame(True, index=prices.index, columns=prices.columns)
    eligible.iloc[0, 1] = False
    labels, _ = forward_rank_label(prices, 1, eligible=eligible)
    returns = prices.iloc[2] / prices.iloc[1] - 1
    expected = returns.drop("b").rank(pct=True)
    pd.testing.assert_series_equal(labels.iloc[0].dropna(), expected, check_names=False)
    assert pd.isna(labels.iloc[0, 1])


def test_labels_nan_and_infinite_returns_excluded():
    prices = _prices()
    prices.iloc[2, 0] = np.nan
    prices.iloc[2, 1] = np.inf
    labels, _ = forward_rank_label(prices, 1)
    assert labels.iloc[0, :2].isna().all()
    assert np.isfinite(labels.iloc[0, 2])


def test_labels_rejects_bad_horizon_or_mask_shape():
    with pytest.raises(ValueError):
        forward_rank_label(_prices(), -1)
    with pytest.raises(ValueError, match="same index and columns"):
        forward_rank_label(_prices(), 1, eligible=pd.DataFrame())


def test_label_end_dates_are_trading_positions():
    prices = _prices()
    _, ends = forward_rank_label(prices, 3)
    assert ends.iloc[0] == prices.index[4]
    assert ends.iloc[5] == prices.index[9]
    assert ends.iloc[6:].isna().all()


def test_features_and_strategies_do_not_import_labels():
    root = Path(__file__).parents[2] / "src" / "q6"
    for package in (root / "ml" / "features.py", root / "strategy"):
        files = [package] if package.is_file() else package.rglob("*.py")
        for path in files:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert node.module != "q6.ml.labels" and node.module != "labels", path
                elif isinstance(node, ast.Import):
                    assert all(alias.name != "q6.ml.labels" for alias in node.names), path


def _panel(n_dates=20, width=2, horizon=3):
    unique = pd.bdate_range("2020-01-01", periods=n_dates)
    dates = pd.DatetimeIndex(np.repeat(unique, width))
    ends = pd.Series(unique + pd.offsets.BDay(horizon), index=unique)
    return unique, dates, ends


def test_cv_train_intervals_do_not_overlap_test_interval():
    unique, dates, ends = _panel()
    for train, test in PurgedKFold(4).split(dates, ends):
        if len(test):
            lo, hi = dates[test].min(), ends.loc[dates[test]].max()
            for i in train:
                assert not (dates[i] <= hi and ends.loc[dates[i]] >= lo)


def test_cv_embargo_removes_next_three_unique_dates():
    unique, dates, ends = _panel(24, horizon=0)
    for train, test in PurgedKFold(4, embargo=3).split(dates, ends):
        if not len(test):
            continue
        last = unique.get_loc(dates[test].max())
        banned = unique[last + 1 : last + 4]
        assert not dates[train].isin(banned).any()


def test_cv_embargo_starts_after_test_label_end_not_block_end():
    # h=20 时 purge 已覆盖测试块之后 20 天；embargo=5 必须再往后多剔 5 天，否则 embargo 形同虚设。
    unique, dates, ends = _panel(120, horizon=20)
    for train, test in PurgedKFold(3, embargo=5).split(dates, ends):
        if not len(test):
            continue
        end = ends.loc[dates[test]].max()
        banned = unique[unique > end][:5]
        if len(banned):
            assert not dates[train].isin(banned).any()
            after = unique[unique > end][5:]
            if len(after):
                assert dates[train].isin(after[:1]).any()


def test_cv_test_folds_partition_all_valid_rows_without_overlap():
    _, dates, ends = _panel()
    splits = list(PurgedKFold(5).split(dates, ends))
    test_rows = [int(i) for _, test in splits for i in test]
    assert sorted(test_rows) == list(range(len(dates)))
    assert len(test_rows) == len(set(test_rows))


def test_cv_zero_horizon_embargo_zero_is_ordinary_time_kfold():
    _, dates, ends = _panel(10, horizon=0)
    for train, test in PurgedKFold(5).split(dates, ends):
        assert set(train) == set(range(len(dates))) - set(test)


def test_cv_duplicate_panel_dates_stay_in_same_fold():
    _, dates, ends = _panel(12, width=4)
    for _, test in PurgedKFold(3).split(dates, ends):
        test_dates = set(dates[test])
        for date in dates.unique():
            rows = np.flatnonzero(dates == date)
            assert int(np.isin(rows, test).sum()) in (0, len(rows))
            assert (date in test_dates) == bool(np.isin(rows, test).all())


def _book_oracle(dates, label_end, n_splits, embargo):
    unique = dates.unique()
    groups = np.array_split(unique, n_splits)
    valid = label_end.reindex(dates).notna().to_numpy()
    result = []
    for block in groups:
        test = np.flatnonzero(dates.isin(block) & valid)
        start, end = block[0], label_end.loc[block].max()
        # Independent getTrainTimes-style conditions: starts inside, ends inside, or spans test.
        starts_inside = (dates >= start) & (dates <= end)
        ends_inside = (label_end.reindex(dates).array >= start) & (label_end.reindex(dates).array <= end)
        spans = (dates < start) & (label_end.reindex(dates).array > end)
        purge = starts_inside | ends_inside | spans
        embargo_dates = unique[unique > end][:embargo]  # AFML snippet 7.3: after max test label end
        train = np.flatnonzero(valid & ~purge & ~dates.isin(block) & ~dates.isin(embargo_dates))
        result.append((train, test))
    return result


@pytest.mark.parametrize("horizon", [1, 5, 20])
@pytest.mark.parametrize("embargo", [0, 5])
def test_cv_matches_independent_get_train_times_oracle(horizon, embargo):
    rng = np.random.default_rng(9000 + horizon + embargo)
    unique = pd.bdate_range("2010-01-01", periods=200)
    dates = pd.DatetimeIndex(np.repeat(unique, 3))
    ends = pd.Series([unique[min(i + horizon, 199)] for i in range(200)], index=unique)
    # Random missing labels exercise the documented exclusion behavior.
    ends.iloc[rng.choice(200, size=7, replace=False)] = pd.NaT
    actual = list(PurgedKFold(5, embargo).split(dates, ends))
    expected = _book_oracle(dates, ends, 5, embargo)
    assert len(actual) == len(expected)
    for (atr, ate), (etr, ete) in zip(actual, expected, strict=True):
        np.testing.assert_array_equal(atr, etr)
        np.testing.assert_array_equal(ate, ete)


def test_cv_nat_rows_never_enter_train_or_test():
    unique, dates, ends = _panel(12, horizon=0)
    ends.iloc[3] = pd.NaT
    omitted = set(np.flatnonzero(dates == unique[3]))
    for train, test in PurgedKFold(3).split(dates, ends):
        assert omitted.isdisjoint(train)
        assert omitted.isdisjoint(test)
