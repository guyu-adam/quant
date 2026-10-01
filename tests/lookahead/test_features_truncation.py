from q6.lint.truncation_test import check_lookahead
from q6.ml.features import build_features
from tests.unit.test_ml_features import data_with_fields


def test_features_have_no_lookahead():
    data = data_with_fields()
    report = check_lookahead(build_features, data, name="features", lag=0)
    assert report.passed, str(report)
    features = build_features(data)
    assert all(frame.shape == data["close"].shape for frame in features.values())
