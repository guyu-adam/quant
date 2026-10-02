from lookahead.samples import data_with_fields
from q6.lint.truncation_test import check_lookahead, strategy_runner
from q6.strategy.base import StrategyBase, StrategySpec
from q6.strategy.combo import Combo


class Fixed(StrategyBase):
    name = "fixed"

    @property
    def spec(self):
        return StrategySpec(("close_hfq",), 5)

    def on_bar(self, ctx):
        return {ctx.view.symbols[0]: 0.5}


def test_combo_passes_truncation_check():
    data = data_with_fields()
    c = data["close"]
    frames = {"close_hfq": c, "tradestatus": c * 0 + 1, "close": c}
    report = check_lookahead(strategy_runner(lambda: Combo([(Fixed(), 1)])), frames)
    assert report.passed, str(report)
