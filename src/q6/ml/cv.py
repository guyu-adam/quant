"""Purged time-series cross-validation for panel samples with forward labels."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pandas as pd


class PurgedKFold:
    """Contiguous date-block K-fold with label-overlap purging and a day embargo.

    ``dates`` contains one date per panel row and must be sorted. ``label_end``
    is indexed by unique sample date. Rows with a missing label end are excluded
    from both training and test sets.
    """

    def __init__(self, n_splits: int = 5, embargo: int = 0):
        if isinstance(n_splits, bool) or not isinstance(n_splits, (int, np.integer)) or n_splits < 2:
            raise ValueError("n_splits must be an integer >= 2")
        if isinstance(embargo, bool) or not isinstance(embargo, (int, np.integer)) or embargo < 0:
            raise ValueError("embargo must be a non-negative integer")
        self.n_splits = int(n_splits)
        self.embargo = int(embargo)

    def split(
        self, dates: pd.DatetimeIndex, label_end: pd.Series
    ) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        """Yield row indices; label_end NaT rows are omitted from every fold."""
        dates = pd.DatetimeIndex(dates)
        if not dates.is_monotonic_increasing:
            raise ValueError("dates must be sorted in ascending order")
        if dates.hasnans:
            raise ValueError("dates cannot contain NaT")
        unique_dates = dates.unique()
        if len(unique_dates) < self.n_splits:
            raise ValueError("n_splits cannot exceed the number of unique dates")
        if not label_end.index.is_unique:
            raise ValueError("label_end index must contain unique dates")
        missing = unique_dates.difference(label_end.index)
        if len(missing):
            raise ValueError("label_end must contain every sample date")

        ends = pd.to_datetime(label_end.reindex(unique_dates))
        end_by_date = pd.Series(ends.to_numpy(), index=unique_dates)
        valid_rows = np.asarray(pd.notna(dates) & pd.notna(dates.map(end_by_date)), dtype=bool)
        row_ends = pd.DatetimeIndex(dates.map(end_by_date))

        for test_dates in np.array_split(unique_dates, self.n_splits):
            test_mask = dates.isin(test_dates) & valid_rows
            test_idx = np.flatnonzero(test_mask)
            if not len(test_idx):
                yield np.array([], dtype=int), test_idx
                continue

            test_start = test_dates[0]
            test_end = end_by_date.loc[test_dates].max()  # lookahead: ok purge 区间截止于测试标签终点
            overlaps = (dates <= test_end) & (row_ends >= test_start)

            date_positions = pd.Series(np.arange(len(unique_dates)), index=unique_dates)
            test_last_pos = int(date_positions.loc[test_dates[-1]])
            embargo_dates = unique_dates[test_last_pos + 1 : test_last_pos + 1 + self.embargo]
            embargo_mask = dates.isin(embargo_dates)
            train_mask = valid_rows & ~test_mask & ~overlaps & ~embargo_mask
            yield np.flatnonzero(train_mask), test_idx
