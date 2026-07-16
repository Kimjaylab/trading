from trading.backtest.engine import BacktestEngine
from trading.backtest.metrics import compute_metrics
from trading.backtest.report import exclusion_report, feature_condition_report, trades_to_dataframe
from trading.brokers.interfaces import BrokerClient, Position
from trading.config import get_config
from trading.data.synthetic import SyntheticDataProvider


class _FakeRealBroker(BrokerClient):
    """PaperBroker의 portfolio_value()가 없는 실거래 브로커(KISBroker 등)를 흉내낸다."""

    def __init__(self, cash: float, positions: dict[str, Position] | None = None):
        self.cash = cash
        self.positions = positions or {}

    def get_cash_balance(self) -> float:
        return self.cash

    def get_positions(self) -> dict[str, Position]:
        return self.positions

    def place_order(self, *args, **kwargs):
        raise NotImplementedError


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


def test_portfolio_value_works_without_paper_broker_method():
    """BacktestEngine이 PaperBroker 전용 portfolio_value()에 의존하면 실거래 브로커
    (KISBroker 등, 그 메서드가 없음)에서 크래시한다 - _portfolio_value()가 이를
    get_cash_balance()+get_positions()+시세로 대체 계산하는지 확인한다."""
    provider = SyntheticDataProvider(n_symbols=4, seed=9, bar_minutes=15)
    symbol = provider.get_universe(provider.get_session_timestamps()[0])[0]
    ts = provider.get_session_timestamps()[3]
    price = provider.get_snapshot(symbol, ts).last_close

    fake_broker = _FakeRealBroker(
        cash=1_000_000,
        positions={symbol: Position(symbol=symbol, quantity=10, avg_price=price, opened_at=ts, strategy="x", stop_price=0, target_price=0)},
    )
    engine = BacktestEngine(provider, initial_cash=1_000_000, config=get_config(), broker=fake_broker)

    value = engine._portfolio_value(ts)

    assert value == 1_000_000 + 10 * price
