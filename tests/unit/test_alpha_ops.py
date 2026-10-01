import numpy as np
import pandas as pd
import pytest

from q6.alpha import ops

X = pd.DataFrame([[1., 2.], [2., 4.], [3., 6.], [4., 8.], [5., 10.]])
Y = pd.DataFrame([[2., 1.], [4., 2.], [6., 3.], [8., 4.], [10., 5.]])
N = np.nan


@pytest.mark.parametrize("fn,expected", [
    (lambda x: ops.delay(x, 2), [[N,N],[N,N],[1,2],[2,4],[3,6]]),  # x[t-2]
    (lambda x: ops.delta(x, 2), [[N,N],[N,N],[2,4],[2,4],[2,4]]),  # x[t]-x[t-2]
    (lambda x: ops.ts_sum(x, 3), [[N,N],[N,N],[6,12],[9,18],[12,24]]),  # sum last 3
    (lambda x: ops.ts_mean(x, 3), [[N,N],[N,N],[2,4],[3,6],[4,8]]),  # sum / 3
    (lambda x: ops.ts_std(x, 3), [[N,N],[N,N],[1,2],[1,2],[1,2]]),  # sample std
    (lambda x: ops.ts_min(x, 3), [[N,N],[N,N],[1,2],[2,4],[3,6]]),  # min last 3
    (lambda x: ops.ts_max(x, 3), [[N,N],[N,N],[3,6],[4,8],[5,10]]),  # max last 3
    (lambda x: ops.ts_argmin(x, 3), [[N,N],[N,N],[0,0],[0,0],[0,0]]),  # earliest minimum position
    (lambda x: ops.ts_argmax(x, 3), [[N,N],[N,N],[2,2],[2,2],[2,2]]),  # today's maximum position
    (lambda x: ops.ts_rank(x, 3), [[N,N],[N,N],[1,1],[1,1],[1,1]]),  # (2 smaller + .5*(1-1))/2
    (lambda x: ops.ts_zscore(x, 3), [[N,N],[N,N],[1,1],[1,1],[1,1]]),  # (today-mean)/sample std
    (lambda x: ops.ts_product(x, 3), [[N,N],[N,N],[6,48],[24,192],[60,480]]),  # product last 3
    (lambda x: ops.ts_corr(x, Y, 3), [[N,N],[N,N],[1,1],[1,1],[1,1]]),  # rolling correlation
    (lambda x: ops.ts_cov(x, Y, 3), [[N,N],[N,N],[2,2],[2,2],[2,2]]),  # sample covariance
    (lambda x: ops.decay_linear(x, 3), [[N,N],[N,N],[14/6,28/6],[20/6,40/6],[26/6,52/6]]),  # weights 1,2,3
    (lambda x: ops.ewm_mean(x, 1), [[1,2],[1.5,3],[2.25,4.5],[3.125,6.25],[4.0625,8.125]]),
    # alpha=.5 adjust=False
    (lambda x: ops.returns(x), [[N,N],[1,1],[.5,.5],[1/3,1/3],[.25,.25]]),  # x[t]/x[t-1]-1
    (lambda x: ops.cs_rank(x), [[.5,1],[.5,1],[.5,1],[.5,1],[.5,1]]),  # row percentile ranks
    (lambda x: ops.cs_zscore(x), [[-2**-.5,2**-.5]]*5),  # (x-row mean)/sample std
    (lambda x: ops.cs_demean(x), [[-.5,.5],[-1,1],[-1.5,1.5],[-2,2],[-2.5,2.5]]),  # subtract row mean
    (lambda x: ops.cs_scale(x), [[1/3,2/3]]*5),  # x / row absolute sum
    (lambda x: ops.cs_winsorize(x), X.to_numpy().tolist()),  # two points lie within median +/- 3*scaled MAD
    (lambda x: ops.sign(x - 3), [[-1,-1],[-1,1],[0,1],[1,1],[1,1]]),  # sign(x-3)
    (lambda x: ops.signed_power(x - 3, 2), [[-4,-1],[-1,1],[0,9],[1,25],[4,49]]),  # sign(v)*abs(v)^2
    (lambda x: ops.safe_div(x, X - 3), [[-0.5,-2],[-2,4],[N,2],[4,1.6],[2.5,10/7]]),  # a/b, zero b -> NaN
])
def test_each_operator_hand_calculation(fn, expected):
    result = fn(X)
    np.testing.assert_allclose(result.to_numpy(), np.asarray(expected), equal_nan=True)
    assert result.index.equals(X.index) and result.columns.equals(X.columns) and result.shape == X.shape


def test_ts_rank_ties_and_nan_propagation():
    x = pd.DataFrame([[1.], [2.], [2.], [np.nan], [3.]])
    np.testing.assert_allclose(ops.ts_rank(x, 3).to_numpy(), [[N], [N], [.75], [N], [N]], equal_nan=True)
    # The NaN row invalidates every complete three-row window containing it.


@pytest.mark.parametrize("fn", [ops.delay, ops.delta, ops.ts_sum, ops.ts_mean, ops.ts_std,
    ops.ts_min, ops.ts_max, ops.ts_argmin, ops.ts_argmax, ops.ts_rank, ops.ts_zscore,
    ops.ts_product, ops.decay_linear, ops.returns])
def test_invalid_window(fn):
    with pytest.raises(ValueError):
        fn(X, 0)


def test_binary_alignment_and_ewm_halflife_validation():
    with pytest.raises(ValueError):
        ops.ts_corr(X, Y.set_axis(["a", "b"], axis=1), 2)
    with pytest.raises(ValueError):
        ops.ewm_mean(X, 0)
