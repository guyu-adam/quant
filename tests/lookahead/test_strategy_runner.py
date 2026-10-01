"""strategy_runner 自检（P2-14）：干净的样板策略通过；构造时注入外部数据并偷看下一行的策略被抓到。"""

from lookahead.samples import data_with_fields
from q6.lint.truncation_test import check_lookahead, strategy_runner
from q6.strategy.base import StrategyBase, StrategySpec
from q6.strategy.examples import LowVolEqualWeight


def frames():
    d = data_with_fields()
    c = d["close"]
    return {"close_hfq": c, "tradestatus": c * 0 + 1, "close": c}

class Leaky(StrategyBase):
    name = "leaky"
    def __init__(self, future): self.future = future
    @property
    def spec(self): return StrategySpec(("close_hfq",), 5)
    def on_bar(self, ctx):
        i = ctx.view.t + 1
        row = self.future.iloc[min(i, len(self.future) - 1)]  # 偷看下一行
        best = row.idxmax()
        return {best: 0.5}

def test_lowvol_passes():
    rep = check_lookahead(strategy_runner(lambda: LowVolEqualWeight(n=3, lookback=20, every=5)), frames())
    assert rep.passed, str(rep)

def test_injected_future_is_caught():
    f = frames()
    # 最常见的绕过 PITView 的写法：构造时把整块数据塞进策略。截断 / 扰动后传进来的数据变了，
    # 偷看下一行的决定跟着变，应被发现
    holder = {}
    def run(d):
        holder["d"] = d["close_hfq"]
        return strategy_runner(lambda: Leaky(holder["d"]))(d)
    rep = check_lookahead(run, f)
    assert not rep.passed
