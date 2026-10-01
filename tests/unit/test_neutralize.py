import numpy as np
import pandas as pd

from q6.alpha.neutralize import neutralize


def test_neutralize_exposure_and_industry():
    rng = np.random.default_rng(8)
    size = pd.DataFrame(rng.normal(size=(3, 40)))
    industry = pd.DataFrame(np.tile(np.repeat(["a", "b", "c", "d"], 10), (3, 1)))
    noise = pd.DataFrame(rng.normal(size=(3, 40)))
    f = 2 * size + noise
    r = neutralize(f, {"size": size}, industry)
    for i in range(3):
        assert abs(np.corrcoef(r.iloc[i], size.iloc[i])[0, 1]) < 1e-10
        for label in "abcd":
            assert abs(r.iloc[i, industry.iloc[i].eq(label)].mean()) < 1e-10
