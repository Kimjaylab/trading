from datetime import datetime

import pandas as pd

from trading.brokers.interfaces import Position
from trading.config import get_config
from trading.data.interfaces import MarketSnapshot
from trading.scoring.features import FeatureVector
from trading.strategies.breakout import BreakoutStrategy
from trading.strategies.pullback import PullbackStrategy
from trading.strategies.selector import PhaseSelector
from trading.strategies.trend_pullback import TrendPullbackStrategy


def _snapshot(last_close: float, execution_strength: float = 100.0, timestamp: datetime | None = None) -> MarketSnapshot:
    ts = timestamp or datetime(2026, 7, 16, 9, 5)
    idx = pd.date_range(end=ts, periods=5, freq="1min")
    minute_bars = pd.DataFrame(
        {
            "open": [last_close] * 5,
            "high": [last_close * 1.001] * 5,
            "low": [last_close * 0.999] * 5,
            "close": [last_close] * 5,
            "volume": [1000] * 5,
        },
        index=idx,
    )
    daily_bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume", "trading_value"])
    return MarketSnapshot(
        symbol="TEST",
        timestamp=ts,
        minute_bars=minute_bars,
        daily_bars=daily_bars,
        bid_price=last_close * 0.999,
        ask_price=last_close * 1.001,
        bid_qty_top5=1000,
        ask_qty_top5=1000,
        execution_strength=execution_strength,
        program_net_buy_krw=0.0,
        foreign_net_buy_krw=0.0,
        institution_net_buy_krw=0.0,
        sector_return_pct=0.0,
        theme_return_pct=0.0,
        index_return_pct=0.0,
        vi_triggered=False,
    )


def _features(**overrides) -> FeatureVector:
    base = {name: 0.0 for name in FeatureVector.__dataclass_fields__}
    base.update(overrides)
    return FeatureVector(**base)


def _position(avg_price: float, stop_price: float, target_price: float, opened_at: datetime | None = None) -> Position:
    return Position(
        symbol="TEST",
        quantity=10,
        avg_price=avg_price,
        opened_at=opened_at or datetime(2026, 7, 16, 9, 0),
        strategy="breakout",
        stop_price=stop_price,
        target_price=target_price,
    )


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


def test_breakout_partial_take_profit_then_full():
    cfg = get_config()
    strat = BreakoutStrategy(cfg)
    scfg = cfg.strategies["breakout"]
    entry_price = 10000.0
    stop = entry_price * (1 - scfg["hard_stop_pct"] / 100)

    # +2% (partial 임계값) 도달, 아직 부분익절 안 한 상태 -> 절반 익절
    partial_price = entry_price * (1 + scfg["partial_take_profit_pct"] / 100)
    snap = _snapshot(partial_price, execution_strength=150)
    pos = _position(entry_price, stop, entry_price * (1 + scfg["full_take_profit_pct"] / 100))
    feat = _features(candle_strength=0.5)

    decision = strat.evaluate_exit(snap, feat, pos, minutes_held=3, partial_exit_done=False)
    assert decision.should_exit
    assert decision.reason == "partial_take_profit"
    assert decision.exit_fraction == scfg["partial_exit_fraction"]

    # 이미 부분익절 했고 아직 전량 목표(+3%) 미도달, 체결강도도 정상 -> 계속 보유
    decision2 = strat.evaluate_exit(snap, feat, pos, minutes_held=4, partial_exit_done=True)
    assert not decision2.should_exit

    # +3%(전량 목표) 도달 -> 전량 익절
    full_price = entry_price * (1 + scfg["full_take_profit_pct"] / 100)
    snap_full = _snapshot(full_price, execution_strength=150)
    decision3 = strat.evaluate_exit(snap_full, feat, pos, minutes_held=5, partial_exit_done=True)
    assert decision3.should_exit
    assert decision3.reason == "full_take_profit"
    assert decision3.exit_fraction == 1.0


def test_breakout_hard_stop_at_configured_percentage():
    cfg = get_config()
    strat = BreakoutStrategy(cfg)
    scfg = cfg.strategies["breakout"]
    entry_price = 10000.0
    stop = entry_price * (1 - scfg["hard_stop_pct"] / 100)
    pos = _position(entry_price, stop, entry_price * 1.03)

    snap = _snapshot(stop - 1, execution_strength=100)
    feat = _features()

    decision = strat.evaluate_exit(snap, feat, pos, minutes_held=2)
    assert decision.should_exit
    assert decision.reason == "hard_stop_hit"
    assert decision.exit_fraction == 1.0


def test_pullback_partial_and_fixed_stop():
    cfg = get_config()
    strat = PullbackStrategy(cfg)
    scfg = cfg.strategies["pullback"]
    entry_price = 10000.0
    stop = entry_price * (1 - scfg["hard_stop_pct"] / 100)
    pos = _position(entry_price, stop, entry_price * (1 + scfg["full_take_profit_pct"] / 100))

    # 고정 -4% 손절 확인
    snap_stop = _snapshot(stop - 1)
    assert strat.evaluate_exit(snap_stop, _features(), pos, minutes_held=10).reason == "hard_stop_hit"

    # +5% 부분 익절
    partial_price = entry_price * (1 + scfg["partial_take_profit_pct"] / 100)
    snap_partial = _snapshot(partial_price)
    decision = strat.evaluate_exit(snap_partial, _features(minute_trend=0.1), pos, minutes_held=10, partial_exit_done=False)
    assert decision.reason == "partial_take_profit"
    assert decision.exit_fraction == scfg["partial_exit_fraction"]


def test_trend_pullback_partial_and_full_take_profit():
    cfg = get_config()
    strat = TrendPullbackStrategy(cfg)
    scfg = cfg.strategies["trend_pullback"]
    entry_price = 10000.0
    stop = entry_price * (1 - scfg["hard_stop_pct"] / 100)
    pos = _position(entry_price, stop, entry_price * (1 + scfg["full_take_profit_pct"] / 100))

    partial_price = entry_price * (1 + scfg["partial_take_profit_pct"] / 100)
    snap = _snapshot(partial_price, timestamp=datetime(2026, 7, 16, 10, 0))
    feat = _features(daily_position=0.2, minute_trend=0.1)

    decision = strat.evaluate_exit(snap, feat, pos, minutes_held=20, partial_exit_done=False)
    assert decision.reason == "partial_take_profit"
    assert decision.exit_fraction == scfg["partial_exit_fraction"]

    full_price = entry_price * (1 + scfg["full_take_profit_pct"] / 100)
    snap_full = _snapshot(full_price, timestamp=datetime(2026, 7, 16, 10, 0))
    decision2 = strat.evaluate_exit(snap_full, feat, pos, minutes_held=21, partial_exit_done=True)
    assert decision2.reason == "full_take_profit"
    assert decision2.exit_fraction == 1.0
