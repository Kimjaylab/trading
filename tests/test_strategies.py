from datetime import datetime

from trading.config import get_config
from trading.strategies.selector import PhaseSelector


def test_phase_selector_switches_by_elapsed_minutes():
    selector = PhaseSelector(get_config())
    open_dt = datetime(2026, 7, 16, 9, 0)

    assert selector.phase_name(open_dt) == "breakout"
    assert selector.phase_name(open_dt.replace(minute=5)) == "breakout"
    assert selector.phase_name(open_dt.replace(minute=15)) == "pullback"
    assert selector.phase_name(open_dt.replace(minute=29)) == "pullback"
    assert selector.phase_name(open_dt.replace(hour=10, minute=0)) == "trend_pullback"
    assert selector.phase_name(open_dt.replace(hour=15, minute=20)) == "trend_pullback"


def test_phase_selector_returns_distinct_strategy_instances():
    selector = PhaseSelector(get_config())
    assert selector.get("breakout").name == "breakout"
    assert selector.get("pullback").name == "pullback"
    assert selector.get("trend_pullback").name == "trend_pullback"
