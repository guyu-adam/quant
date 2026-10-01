import numpy as np
import pytest

from q6.alpha.library import FACTORS
from q6.ml.features import FEATURE_NAMES, ROLLING_NAMES, WINDOWS, assert_no_inf, build_features, to_long
from tests.lookahead.samples import make_data


def data_with_fields():
    data = make_data()
    close = data["close"]
    data.update(
        open=close * 0.999,
        high=close * 1.01,
        low=close * 0.99,
        ret=close.pct_change(fill_method=None),
        amount=data["volume"] * close,
        turn=data["volume"] / 1_000,
        float_cap=data["volume"] * close * 100,
    )
    return data


def test_feature_names_count_and_shapes():
    data = data_with_fields()
    features = build_features(data)
    expected = list(FACTORS) + [
        {("range", 20): "range_ts_20", ("amihud", 20): "amihud_ts_20"}.get((name, w), f"{name}_{w}")
        for w in WINDOWS
        for name in ROLLING_NAMES
    ]
    assert len(features) == 75
    assert list(features) == expected
    assert list(features) == list(FEATURE_NAMES)
    assert all(frame.shape == data["close"].shape for frame in features.values())
    assert all(frame.index.equals(data["close"].index) for frame in features.values())
    assert all(frame.columns.equals(data["close"].columns) for frame in features.values())


def test_to_long_keeps_order_and_applies_mask():
    features = build_features(data_with_fields())
    mask = features["close"] if "close" in features else next(iter(features.values())).notna()
    mask.iloc[0, 0] = False
    long = to_long(features, mask=mask)
    assert list(long.columns) == list(FEATURE_NAMES)
    assert len(long) == int(mask.to_numpy().sum())
    assert (long.index.get_level_values("date") == mask.index[0]).sum() == int(mask.iloc[0].sum())


def test_amihud_zero_amount_does_not_create_inf_and_assert_names_offender():
    data = data_with_fields()
    data["amount"].iloc[130, 0] = 0
    features = build_features(data)
    long = to_long(features)
    amihud = long[[name for name in FEATURE_NAMES if name.startswith("amihud")]]
    assert_no_inf(amihud)
    long.iloc[0, long.columns.get_loc("ret_5")] = np.inf
    with pytest.raises(ValueError, match="ret_5"):
        assert_no_inf(long)
