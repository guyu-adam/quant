from datetime import datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from q6.core.types import Fill, Side, validate_target_weights
from q6.engine.broker_sim import BrokerSim
from q6.risk.monitor import RiskConfig, RiskMonitor
from q6.risk.pretrade import check_target

D0 = datetime(2024, 1, 2, 15)


def mark(monitor, day, equity):
    return monitor.on_mark(D0 + timedelta(days=day), equity)


def test_floating_loss_triggers_daily_limit_with_broker_equity():
    broker = BrokerSim(100_000, D0)
    broker.apply_fill(Fill("b", "sh.600000", Side.BUY, 5000, 10.0, D0))
    equity = broker.mark(D0, {"sh.600000": 9.0}).equity
    monitor = RiskMonitor()
    monitor.on_mark(D0, 100_000)
    assert monitor.on_mark(D0 + timedelta(days=1), equity).daily_loss_hit


def test_daily_loss_threshold_boundary():
    monitor = RiskMonitor()
    mark(monitor, 0, 100)
    assert not mark(monitor, 1, 97.01).daily_loss_hit
    assert mark(monitor, 2, 94.0997).daily_loss_hit


def test_daily_loss_filter_rejects_new_adds_and_keeps_reductions():
    monitor = RiskMonitor()
    mark(monitor, 0, 100)
    mark(monitor, 1, 97)
    result = monitor.filter_target({"A": 0.4, "B": 0.3, "C": 0.2}, {"A": 0.2, "B": 0.1})
    assert result == {"A": 0.2, "B": 0.1}


def test_daily_loss_clears_next_day_and_target_passes():
    monitor = RiskMonitor()
    mark(monitor, 0, 100)
    mark(monitor, 1, 97)
    assert not mark(monitor, 2, 98).daily_loss_hit
    assert monitor.filter_target({"A": 0.3}, {}) == {"A": 0.3}


def test_drawdown_triggers_breaker_and_blocks_targets_during_cooldown():
    monitor = RiskMonitor(RiskConfig(cooldown_days=3))
    mark(monitor, 0, 100)
    assert mark(monitor, 1, 80).breaker_on
    assert monitor.filter_target({"NEW": 0.2}, {"A": 0.4}) == {}
    assert mark(monitor, 2, 81).breaker_on
    assert monitor.filter_target({"NEW": 0.2}, {"A": 0.4}) == {}


def test_cooldown_release_resets_peak_and_avoids_immediate_retrigger():
    monitor = RiskMonitor(RiskConfig(cooldown_days=2))
    mark(monitor, 0, 100)
    mark(monitor, 1, 80)
    assert mark(monitor, 2, 81).breaker_on
    released = mark(monitor, 3, 80.5)
    assert not released.breaker_on
    assert released.peak == 80.5
    assert not mark(monitor, 4, 79).breaker_on


def test_breaker_scale_halves_existing_positions():
    monitor = RiskMonitor(RiskConfig(cooldown_days=2, breaker_scale=0.5))
    mark(monitor, 0, 100)
    mark(monitor, 1, 80)
    assert monitor.filter_target({"NEW": 0.2}, {"A": 0.4, "B": 0.2}) == {"A": 0.2, "B": 0.1}


def test_breaker_scale_applies_once_not_every_cooldown_day():
    """回归（Cen 审查）：冷却期内每天调用 filter_target 不能让仓位逐日再减半。"""
    monitor = RiskMonitor(RiskConfig(cooldown_days=5, breaker_scale=0.5))
    mark(monitor, 0, 100)
    mark(monitor, 1, 80)
    assert monitor.breaker_triggered
    assert monitor.filter_target({"A": 0.4}, {"A": 0.4}) == {"A": 0.2}
    mark(monitor, 2, 80)
    assert not monitor.breaker_triggered
    assert monitor.filter_target({"A": 0.4}, {"A": 0.2}) == {"A": 0.2}  # 只许减仓：不加回去，也不再减半
    assert monitor.filter_target({"A": 0.1, "NEW": 0.3}, {"A": 0.2}) == {"A": 0.1}  # 减仓可以，新开不行


@pytest.mark.parametrize("equity", [0, -1, float("nan"), float("inf")])
def test_invalid_equity_rejected(equity):
    with pytest.raises(ValueError):
        mark(RiskMonitor(), 0, equity)


def test_nonincreasing_timestamp_rejected():
    monitor = RiskMonitor()
    mark(monitor, 1, 100)
    with pytest.raises(ValueError):
        mark(monitor, 1, 101)


def test_check_target_caps_then_scales_gross_and_drops_invalid_values():
    capped, issues = check_target({"A": 0.08})
    assert capped == {"A": 0.05}
    assert issues == ["CAP A 0.08->0.05"]
    scaled, issues = check_target({"A": 0.05, "B": 0.05}, cap=0.1, gross=0.08)
    assert sum(scaled.values()) == pytest.approx(0.08)
    assert issues == ["GROSS 0.1->0.08"]
    dropped, issues = check_target({"NEG": -0.1, "NAN": float("nan")})
    assert dropped == {}
    assert len(issues) == 2


@settings(max_examples=100, deadline=None)
@given(
    st.lists(
        st.floats(min_value=1, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=20,
    ),
    st.dictionaries(
        st.text(min_size=1, max_size=5),
        st.floats(0, 0.2, allow_nan=False, allow_infinity=False),
        max_size=5,
    ),
    st.dictionaries(
        st.text(min_size=1, max_size=5),
        st.floats(0, 0.2, allow_nan=False, allow_infinity=False),
        max_size=5,
    ),
)
def test_filter_results_are_valid_and_loss_day_never_exceeds_current(equities, target, current):
    # Normalize generated weights to valid portfolio inputs.
    target = {k: v / max(1, sum(target.values())) for k, v in target.items()}
    current = {k: v / max(1, sum(current.values())) for k, v in current.items()}
    monitor = RiskMonitor(RiskConfig(daily_loss_limit=-0.000001))
    for day, equity in enumerate(equities):
        monitor.on_mark(D0 + timedelta(days=day), equity)
    result = monitor.filter_target(target, current)
    validate_target_weights(result)
    if monitor._daily_loss_hit and not monitor._breaker_on:
        assert all(value <= current.get(symbol, 0) for symbol, value in result.items())
