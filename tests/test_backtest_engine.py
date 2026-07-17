from trading.backtest.engine import BacktestEngine, BacktestResult
from trading.backtest.metrics import compute_metrics
from trading.backtest.report import exclusion_report, feature_condition_report, trades_to_dataframe
from trading.brokers.interfaces import BrokerClient, OrderResult, OrderSide, OrderStatus, Position
from trading.config import get_config
from trading.data.synthetic import SyntheticDataProvider


class _FakeRealBroker(BrokerClient):
    """PaperBroker의 portfolio_value()가 없는 실거래 브로커(KISBroker 등)를 흉내낸다.
    get_positions()가 매번 stop_price=0.0인 새 Position을 주는 실제 KISBroker의 특성도
    재현한다 - _process_exits가 entry_context 값으로 덮어쓰는지 검증하기 위함이다."""

    def __init__(self, cash: float, positions: dict[str, Position] | None = None):
        self.cash = cash
        self.positions = positions or {}
        self._order_seq = 0

    def get_cash_balance(self) -> float:
        return self.cash

    def get_positions(self) -> dict[str, Position]:
        return self.positions

    def place_order(self, symbol, side, quantity, price, timestamp, strategy="", stop_price=0.0, target_price=0.0):
        self._order_seq += 1
        pos = self.positions.get(symbol)
        if side == OrderSide.SELL and pos is not None:
            fill_price = pos.avg_price
            realized_pnl = 0.0
            pos.quantity -= quantity
            if pos.quantity <= 0:
                del self.positions[symbol]
            return OrderResult(symbol, side, quantity, fill_price, OrderStatus.FILLED, f"FAKE-{self._order_seq}", timestamp, realized_pnl=realized_pnl)
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


def _seed_position_and_context(engine: BacktestEngine, symbol: str, ts, entry_price: float, qty: int = 10):
    engine.broker.positions[symbol] = Position(
        symbol=symbol, quantity=qty, avg_price=entry_price, opened_at=ts,
        strategy="breakout", stop_price=entry_price * 0.97, target_price=entry_price * 1.03,
    )
    engine._entry_context[symbol] = {
        "entry_price": entry_price, "entry_ts": ts, "phase": "breakout", "score": 0.7,
        "features": {}, "stop_price": entry_price * 0.97, "target_price": entry_price * 1.03,
        "partial_exit_done": False,
    }


def test_partial_exit_reduces_quantity_and_keeps_entry_context():
    provider = SyntheticDataProvider(n_symbols=4, seed=11, bar_minutes=15)
    ts = provider.get_session_timestamps()[2]
    symbol = provider.get_universe(ts)[0]
    engine = BacktestEngine(provider, initial_cash=10_000_000, config=get_config())
    _seed_position_and_context(engine, symbol, ts, entry_price=10_000.0, qty=10)

    result = BacktestResult(start_equity=10_000_000)
    engine._execute_exit(ts, symbol, engine.broker.positions[symbol], "partial_take_profit", result, exit_fraction=0.5)

    assert engine.broker.positions[symbol].quantity == 5
    assert symbol in engine._entry_context  # 완전히 청산된 게 아니므로 컨텍스트가 남아있어야 한다
    assert engine._entry_context[symbol]["partial_exit_done"] is True
    assert len(result.trades) == 1
    assert result.trades[0].quantity == 5

    # 이어서 전량 청산하면 컨텍스트가 제거되고 두 번째 트레이드 기록이 남아야 한다
    engine._execute_exit(ts, symbol, engine.broker.positions[symbol], "full_take_profit", result, exit_fraction=1.0)
    assert symbol not in engine.broker.get_positions()
    assert symbol not in engine._entry_context
    assert len(result.trades) == 2
    assert result.trades[1].quantity == 5


def test_partial_exit_does_not_change_open_position_count():
    provider = SyntheticDataProvider(n_symbols=4, seed=11, bar_minutes=15)
    ts = provider.get_session_timestamps()[2]
    symbol = provider.get_universe(ts)[0]
    engine = BacktestEngine(provider, initial_cash=10_000_000, config=get_config())
    _seed_position_and_context(engine, symbol, ts, entry_price=10_000.0, qty=10)
    engine.risk_manager.state.open_positions = 1

    result = BacktestResult(start_equity=10_000_000)
    engine._execute_exit(ts, symbol, engine.broker.positions[symbol], "partial_take_profit", result, exit_fraction=0.5)

    assert engine.risk_manager.state.open_positions == 1  # 부분청산은 보유종목수를 줄이면 안 된다


def test_stop_price_from_entry_context_overrides_broker_position():
    """실거래 브로커(get_positions가 stop_price=0.0인 새 객체를 매번 만듦)에서도
    _process_exits가 entry_context의 실제 손절가로 덮어써서 손절이 실제로 발동해야 한다."""
    provider = SyntheticDataProvider(n_symbols=4, seed=12, bar_minutes=15)
    ts = provider.get_session_timestamps()[2]
    symbol = provider.get_universe(ts)[0]
    snap = provider.get_snapshot(symbol, ts)
    current_price = snap.last_close

    entry_price = current_price / 0.90  # 현재가가 진입가 대비 -10%가 되도록 역산
    real_stop = entry_price * 0.97      # 실제 손절가(-3%) - 현재가는 이보다 훨씬 아래

    fake_broker = _FakeRealBroker(cash=1_000_000)
    fake_broker.positions[symbol] = Position(
        symbol=symbol, quantity=10, avg_price=entry_price, opened_at=ts,
        strategy="breakout", stop_price=0.0, target_price=0.0,  # 실거래 브로커는 항상 0
    )
    engine = BacktestEngine(provider, initial_cash=1_000_000, config=get_config(), broker=fake_broker)
    engine._entry_context[symbol] = {
        "entry_price": entry_price, "entry_ts": ts, "phase": "breakout", "score": 0.7,
        "features": {}, "stop_price": real_stop, "target_price": entry_price * 1.03,
        "partial_exit_done": False,
    }

    result = BacktestResult(start_equity=1_000_000)
    features = engine.feature_extractor.extract_batch({symbol: snap})
    engine._process_exits(ts, {symbol: snap}, features, result)

    # 손절가가 entry_context 값으로 덮어써져서 실제로 청산되었어야 한다
    assert symbol not in fake_broker.get_positions()
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "hard_stop_hit"


def test_orphaned_position_gets_fallback_strategy_and_stop_instead_of_crashing():
    """entry_context에도 없고 strategy명도 모르는(예: 'recovered_from_broker') 포지션을
    만나도 크래시하지 않고, 평단가 기준 안전한 손절가를 부여해 관리할 수 있어야 한다."""
    provider = SyntheticDataProvider(n_symbols=4, seed=13, bar_minutes=15)
    ts = provider.get_session_timestamps()[2]
    symbol = provider.get_universe(ts)[0]
    snap = provider.get_snapshot(symbol, ts)
    current_price = snap.last_close

    entry_price = current_price / 0.80  # 현재가가 평단가 대비 -20%가 되도록 역산 (fallback -4% 손절보다 훨씬 아래)

    fake_broker = _FakeRealBroker(cash=1_000_000)
    fake_broker.positions[symbol] = Position(
        symbol=symbol, quantity=5, avg_price=entry_price, opened_at=ts,
        strategy="recovered_from_broker", stop_price=0.0, target_price=0.0,
    )
    engine = BacktestEngine(provider, initial_cash=1_000_000, config=get_config(), broker=fake_broker)
    # 일부러 _entry_context를 비워둔다 - 완전히 낯선(복구된) 포지션 상황을 재현

    result = BacktestResult(start_equity=1_000_000)
    features = engine.feature_extractor.extract_batch({symbol: snap})

    engine._process_exits(ts, {symbol: snap}, features, result)  # 예외가 나면 테스트 실패

    assert symbol not in fake_broker.get_positions()  # 안전한 fallback 손절가에 걸려 청산됐어야 함
