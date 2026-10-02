from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from q6.core.pit import Panel
from q6.engine.event import EngineConfig, EventEngine
from q6.engine.feed import ENGINE_FIELDS, Segment
from q6.engine.matching import MatchConfig
from q6.strategy.base import StrategyBase, StrategySpec
from q6.strategy.combo import Combo


class Planned(StrategyBase):
    def __init__(self, name, plan, fields=("close",), warmup=1):
        self.name, self.plan = name, list(plan)
        self._spec = StrategySpec(fields, warmup)
        self.calls = 0
        self.fills = []

    @property
    def spec(self):
        return self._spec

    def on_bar(self, ctx):
        result = self.plan[min(self.calls, len(self.plan) - 1)]
        self.calls += 1
        return result

    def on_fill(self, fill):
        self.fills.append(fill)


CTX = SimpleNamespace()


def test_weights_are_normalized_and_combine_targets():
    c = Combo([(Planned("a", [{"A": 0.4}]), 1), (Planned("b", [{"A": 0.2, "B": 0.4}]), 3)])
    assert c.on_bar(CTX) == pytest.approx({"A": 0.25, "B": 0.3})


def test_none_reuses_previous_target():
    c = Combo([(Planned("a", [{"A": 0.5}, None]), 1)])
    assert c.on_bar(CTX) == {"A": 0.5}
    assert c.on_bar(CTX) is None  # no member updated: do not issue another rebalance


def test_none_member_target_remains_in_blend_when_other_updates():
    c = Combo([(Planned("a", [{"A": 0.5}, None]), 1), (Planned("b", [None, {"B": 0.5}]), 1)])
    assert c.on_bar(CTX) == {"A": 0.25}
    assert c.on_bar(CTX) == {"A": 0.25, "B": 0.25}


def test_all_none_returns_none():
    assert Combo([(Planned("a", [None]), 1)]).on_bar(CTX) is None


def test_disabled_member_is_never_called():
    off = Planned("off", [{"X": 1}])
    combo = Combo([(off, 1), (Planned("on", [{"A": 0.5}]), 1)], enabled=[False, True])
    assert combo.on_bar(CTX) == {"A": 0.5}
    assert off.calls == 0


def test_blend_is_scaled_to_gross():
    combo = Combo([(Planned("a", [{"A": 0.8}]), 1), (Planned("b", [{"B": 0.8}]), 1)], gross=0.6)
    assert combo.on_bar(CTX) == pytest.approx({"A": 0.3, "B": 0.3})


def test_fill_is_forwarded_to_enabled_members_only():
    active, disabled = Planned("active", [None]), Planned("disabled", [None])
    combo = Combo([(active, 1), (disabled, 0)], enabled=[True, False])
    fill = object()
    combo.on_fill(fill)
    assert active.fills == [fill]
    assert disabled.fills == []


def test_fields_warmup_and_params_use_enabled_members_only():
    combo = Combo([(Planned("a", [None], ("x", "shared"), 4), 2),
                   (Planned("b", [None], ("shared", "y"), 8), 1),
                   (Planned("off", [None], ("z",), 99), 0)], enabled=[True, True, False])
    assert combo.name == "combo"
    assert combo.spec.fields == ("x", "shared", "y")
    assert combo.spec.warmup == 8
    assert combo.spec.params == {"members": "a:0.6666666666666666,b:0.3333333333333333", "gross": 0.95}


@pytest.mark.parametrize("members,enabled", [
    ([(Planned("a", [None]), -1)], None),
    ([(Planned("a", [None]), 0)], None),
    ([(Planned("a", [None]), 1)], [True, False]),
])
def test_invalid_weights_or_enabled_shape_raise(members, enabled):
    with pytest.raises(ValueError):
        Combo(members, enabled=enabled)


def test_event_engine_runs_combo_with_fills_and_finite_equity():
    days = pd.bdate_range("2021-01-04", periods=6)
    idx = days
    close = pd.DataFrame({"sz.000001": np.full(6, 10.0), "sh.600000": np.full(6, 20.0)}, index=idx)
    pre = close.shift().fillna(close.iloc[0])
    f = {"open": close, "high": close * 1.005, "low": close * 0.995, "close": close,
         "preclose": pre, "volume": close * 0 + 1e7, "amount": close * 1e7,
         "tradestatus": close * 0 + 1, "is_st": close * 0, "is_new": close * 0,
         "adj_factor": close * 0 + 1, "ret": close / pre - 1, "bad": close * 0}
    panel = Panel.from_frames(f)

    class Feed:
        fields = ENGINE_FIELDS

        def segments(self, start, end, warmup, keep=None):
            yield Segment(panel, 0, np.ones((6, 2), dtype=bool))

    cfg = EngineConfig(match=MatchConfig(slippage_bp=0, impact_coef=0, commission_min=0))
    combo = Combo([(Planned("buy", [{"sz.000001": 0.5}]), 1)])
    result = EventEngine(cfg).run(combo, Feed(), days[0], days[-1])
    assert result.fills
    assert np.isfinite(result.daily["equity"]).all()
