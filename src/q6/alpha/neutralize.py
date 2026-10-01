"""Daily cross-sectional OLS residualization."""

import numpy as np
import pandas as pd


def neutralize(
    f: pd.DataFrame, exposures: dict[str, pd.DataFrame], industry: pd.DataFrame | None = None
) -> pd.DataFrame:
    frames = [f, *exposures.values()]
    if industry is not None:
        frames.append(industry)
    for frame in frames[1:]:
        if not f.index.equals(frame.index) or not f.columns.equals(frame.columns):
            raise ValueError("factor, exposures, and industry must have identical axes")
    result = pd.DataFrame(np.nan, index=f.index, columns=f.columns, dtype=float)
    for i in range(len(f)):
        y = f.iloc[i].to_numpy(dtype=float)
        cols = [np.ones(len(f.columns))]
        cols.extend(frame.iloc[i].to_numpy(dtype=float) for frame in exposures.values())
        if industry is not None:
            labels = industry.iloc[i].to_numpy()
            categories = sorted(set(labels[pd.notna(labels)]), key=str)
            cols.extend((labels == category).astype(float) for category in categories[1:])
        x = np.column_stack(cols)
        valid = np.isfinite(y) & np.isfinite(x).all(axis=1)
        if valid.sum() < x.shape[1] + 10:
            continue
        beta = np.linalg.lstsq(x[valid], y[valid], rcond=None)[0]
        result.iloc[i, np.flatnonzero(valid)] = y[valid] - x[valid] @ beta
    return result
