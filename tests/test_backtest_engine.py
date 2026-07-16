from trading.backtest.engine import BacktestEngine
from trading.backtest.metrics import compute_metrics
from trading.backtest.report import exclusion_report, feature_condition_report, trades_to_dataframe
from trading.config import get_config
from trading.data.synthetic import SyntheticDataProvider


def test_backtest_runs_end_to_end_without_error():
    provider = SyntheticDataProvider(n_symbols=8, seed=3, bar_minutes=5)
    engine = BacktestEngine(provider, initial_cash=50_000_000, config=get_config(), index_close=provider._index_series)

    result = engine.run()

    assert len(result.equity_curve) == len(provider.get_session_timestamps())
    assert result.final_equity > 0
    assert result.start_equity == 50_000_000

    # 세션 종료 시점에는 보유 포지션이 전량 청산되어 있어야 한다.
    assert len(engine.broker.get_positions()) == 0

    metrics = compute_metrics(result)
    assert 0.0 <= metrics.win_rate <= 1.0

    df = trades_to_dataframe(result)
    assert len(df) == metrics.n_trades

    # 리포트 함수들이 거래 0건이어도 예외 없이 동작해야 한다.
    feature_condition_report(result)
    exclusion_report(result)


def test_no_new_entries_after_force_liquidation_time():
    provider = SyntheticDataProvider(n_symbols=8, seed=3, bar_minutes=5)
    engine = BacktestEngine(provider, initial_cash=50_000_000, config=get_config(), index_close=provider._index_series)
    result = engine.run()

    cutoff = get_config().force_liquidation_time
    late_entries = [t for t in result.trades if t.entry_ts.time() >= cutoff]
    assert late_entries == []
