"""Purged time-series cross-validation for panel samples with forward labels."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pandas as pd


class PurgedKFold:
    """Contiguous date-block K-fold with label-overlap purging and a day embargo.

    Embargo removes the ``embargo`` unique dates right after the test block's
    latest label end (AFML snippet 7.3), not after the block's last date.
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

            # Embargo 从测试块的最大 label_end 之后开始（López de Prado AFML 第 7 章 PurgedKFold，
            # snippet 7.3：train 取 maxT1Idx + mbrg 之后）。从测试块最后一天之后起算会被 purge 吞掉。
            end_pos = int(unique_dates.searchsorted(test_end, side="right"))
            embargo_dates = unique_dates[end_pos : end_pos + self.embargo]
            embargo_mask = dates.isin(embargo_dates)
            train_mask = valid_rows & ~test_mask & ~overlaps & ~embargo_mask
            yield np.flatnonzero(train_mask), test_idx
