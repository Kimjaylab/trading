from datetime import datetime, time

from trading.backtest.engine import BacktestEngine
from trading.config import Config, get_config
from trading.data.synthetic import SyntheticDataProvider
from trading.strategies.selector import PhaseSelector
from trading.utils.time_utils import is_within_session, minutes_since_open, minutes_until


def test_us_profile_loads_overnight_session():
    cfg = get_config("US")
    assert cfg.market_open == time(22, 30)
    assert cfg.market_close == time(5, 0)


def test_minutes_since_open_handles_midnight_rollover():
    market_open = time(22, 30)
    # 다음날 01:00 -> 전날 22:30 개장 기준 150분 경과
    ts = datetime(2026, 7, 17, 1, 0)
    assert abs(minutes_since_open(ts, market_open) - 150) < 1e-6

    # 개장 직후 22:35 -> 5분 경과 (당일 기준, 자정 넘김 아님)
    ts2 = datetime(2026, 7, 16, 22, 35)
    assert abs(minutes_since_open(ts2, market_open) - 5) < 1e-6


def test_minutes_until_handles_midnight_rollover():
    ts = datetime(2026, 7, 16, 23, 0)
    target = time(4, 55)  # hard_close, 다음날
    remaining = minutes_until(ts, target)
    assert abs(remaining - (5 * 60 + 55)) < 1e-6  # 23:00 -> 다음날 04:55 = 5시간55분


def test_is_within_session_overnight():
    market_open = time(22, 30)
    market_close = time(5, 0)
    assert is_within_session(datetime(2026, 7, 16, 22, 30), market_open, market_close)
    assert is_within_session(datetime(2026, 7, 17, 2, 0), market_open, market_close)
    assert not is_within_session(datetime(2026, 7, 16, 12, 0), market_open, market_close)
    assert not is_within_session(datetime(2026, 7, 17, 10, 0), market_open, market_close)


def test_phase_selector_switches_correctly_across_midnight_for_us_market():
    cfg = get_config("US")
    selector = PhaseSelector(cfg)
    assert selector.phase_name(datetime(2026, 7, 16, 22, 30)) == "breakout"
    assert selector.phase_name(datetime(2026, 7, 16, 22, 45)) == "pullback"
    assert selector.phase_name(datetime(2026, 7, 17, 1, 0)) == "trend_pullback"


def test_backtest_runs_on_us_overnight_session():
    cfg = get_config("US")
    provider = SyntheticDataProvider(
        n_symbols=6, seed=5, bar_minutes=15, market_open=cfg.market_open, market_close=cfg.market_close
    )
    engine = BacktestEngine(provider, initial_cash=50_000_000, config=cfg, index_close=provider._index_series)
    result = engine.run()

    assert len(result.equity_curve) == len(provider.get_session_timestamps())
    assert len(engine.broker.get_positions()) == 0  # 세션 종료 시 전량 청산

    cutoff = cfg.force_liquidation_time
    for t in result.trades:
        # 강제청산 시각 이후에는 신규 진입이 없어야 한다 (자정 넘김 세션에서도 유지되어야 함)
        elapsed = minutes_since_open(t.entry_ts, cfg.market_open)
        force_liq_elapsed = minutes_since_open(
            t.entry_ts.replace(hour=cutoff.hour, minute=cutoff.minute), cfg.market_open
        )
        assert elapsed <= force_liq_elapsed + 1e-6
