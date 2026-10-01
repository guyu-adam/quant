"""截断测试自测样例：5 个带未来函数（LEAKY）+ 5 个干净（CLEAN）。

输入统一为 {"close": DataFrame, "volume": DataFrame}（index=日期，columns=代码）。
同一组样例也是 lint/lookahead_ast 的验收样本，所以写法刻意保持"常见 pandas 写法"。
LAG 标注每个样例输出的语义：0 = 信号（t+1 才成交）；1 = 第 t 根 bar 期间的持仓。
"""

import pandas as pd

# ---------------- 带未来函数 ----------------

def leaky_shift_negative(d):
    """用明天的收益做今天的信号。"""
    c = d["close"]
    return c.shift(-1) / c - 1


def leaky_center_rolling(d):
    """居中滚动均值：窗口包含未来 2 根。"""
    return d["close"].rolling(5, center=True).mean() / d["close"] - 1


def leaky_fullsample_zscore(d):
    """全样本均值 / 标准差做标准化。"""
    c = d["close"]
    return (c - c.mean()) / c.std()


def leaky_bfill(d):
    """缺失值用后面的值回填。"""
    c = d["close"].bfill()
    return c / c.rolling(10).mean() - 1


def leaky_same_bar_exec(d):
    """用第 t 根收盘价算出的信号，当作第 t 根 bar 期间的持仓（=当日收盘价当日成交）。"""
    c = d["close"]
    return (c > c.rolling(10).mean()).astype(float)


# ---------------- 干净 ----------------

def clean_momentum(d):
    c = d["close"]
    return c / c.shift(20) - 1


def clean_rolling_zscore(d):
    c = d["close"]
    m = c.rolling(20).mean()
    return (c - m) / c.rolling(20).std()


def clean_expanding_zscore(d):
    c = d["close"]
    return (c - c.expanding(10).mean()) / c.expanding(10).std()


def clean_ffill_cs_rank(d):
    """前值填充 + 截面排名 + 成交量过滤。"""
    c = d["close"].ffill()
    r = c.pct_change(5, fill_method=None).rank(axis=1, pct=True)
    v = d["volume"].rolling(5).mean()
    return r.where(v > 0)


def clean_next_bar_exec(d):
    """信号滞后一根再作为持仓：第 t 根 bar 的持仓只用到 ≤t-1 的收盘价。"""
    c = d["close"]
    return (c > c.rolling(10).mean()).astype(float).shift(1)


LEAKY = {f.__name__: f for f in (leaky_shift_negative, leaky_center_rolling, leaky_fullsample_zscore,
                                  leaky_bfill, leaky_same_bar_exec)}
CLEAN = {f.__name__: f for f in (clean_momentum, clean_rolling_zscore, clean_expanding_zscore,
                                  clean_ffill_cs_rank, clean_next_bar_exec)}
LAG = {"leaky_same_bar_exec": 1, "clean_next_bar_exec": 1}


def make_data(T: int = 250, N: int = 8, seed: int = 7) -> dict[str, pd.DataFrame]:
    """合成行情：对数随机游走 + 随机停牌缺失（NaN）。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-05", periods=T)
    cols = [f"S{i:03d}" for i in range(N)]
    close = 10 * np.exp(np.cumsum(rng.normal(0, 0.02, (T, N)), axis=0))
    close[rng.random((T, N)) < 0.03] = np.nan
    vol = rng.integers(1_000, 100_000, (T, N)).astype(float)
    return {"close": pd.DataFrame(close, idx, cols), "volume": pd.DataFrame(vol, idx, cols)}


def data_with_fields() -> dict[str, pd.DataFrame]:
    """make_data 再补上 ML 特征需要的列（P2-18b）。"""
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
