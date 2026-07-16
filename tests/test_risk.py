from datetime import datetime

from trading.config import get_config
from trading.risk.manager import RiskManager


def test_daily_loss_limit_halts_trading():
    rm = RiskManager(start_equity=10_000_000, config=get_config())
    rm.update_equity(9_400_000)  # -6%, exceeds default 5% daily limit
    ok, reason = rm.can_enter("A000001", datetime(2026, 7, 16, 9, 5), 10000, 9800, 10300)
    assert not ok
    assert "일일 최대 손실" in reason


def test_consecutive_losses_halt_trading():
    rm = RiskManager(start_equity=10_000_000, config=get_config())
    ts = datetime(2026, 7, 16, 9, 5)
    for _ in range(rm.cfg["max_consecutive_losses"]):
        rm.register_entry("A000001", ts)
        rm.register_exit("A000001", ts, realized_pnl=-50_000)
    ok, reason = rm.can_enter("A000002", ts, 10000, 9800, 10300)
    assert not ok
    assert "연속 손실" in reason


def test_reentry_cooldown_blocks_same_symbol():
    rm = RiskManager(start_equity=10_000_000, config=get_config())
    ts = datetime(2026, 7, 16, 9, 5)
    rm.register_entry("A000001", ts)
    rm.register_exit("A000001", ts, realized_pnl=-10_000)

    ok, reason = rm.can_enter("A000001", ts, 10000, 9800, 10300)
    assert not ok
    assert "재진입" in reason


def test_reward_risk_ratio_filter():
    rm = RiskManager(start_equity=10_000_000, config=get_config())
    ts = datetime(2026, 7, 16, 9, 5)
    # 진입가 10000, 손절 9900(risk=100), 목표 10080(reward=80) -> ratio 0.8 < min 1.0
    ok, reason = rm.can_enter("A000001", ts, 10000, 9900, 10080)
    assert not ok
    assert "손익비" in reason


def test_max_open_positions_limit():
    rm = RiskManager(start_equity=10_000_000, config=get_config())
    ts = datetime(2026, 7, 16, 9, 5)
    max_pos = rm.cfg["max_open_positions"]
    for i in range(max_pos):
        rm.register_entry(f"A{i:06d}", ts)

    ok, reason = rm.can_enter("A999999", ts, 10000, 9800, 10500)
    assert not ok
    assert "보유 종목" in reason


def test_position_size_shrinks_after_consecutive_losses():
    rm = RiskManager(start_equity=10_000_000, config=get_config())
    ts = datetime(2026, 7, 16, 9, 5)
    assert rm.position_size_multiplier() == 1.0
    rm.register_entry("A000001", ts)
    rm.register_exit("A000001", ts, realized_pnl=-10_000)
    rm.register_entry("A000001", ts)
    rm.register_exit("A000001", ts, realized_pnl=-10_000)
    assert rm.position_size_multiplier() < 1.0


def test_update_equity_does_not_crash_when_start_equity_is_zero():
    """실거래 브로커가 예수금 필드를 못 찾아 0을 반환하는 경우, start_equity=0으로
    RiskManager가 생성될 수 있다 - 이때 0으로 나누기 에러 없이 그냥 넘어가야 한다."""
    rm = RiskManager(start_equity=0.0, config=get_config())
    rm.update_equity(0.0)  # 예외가 나면 테스트 실패
    assert not rm.state.trading_halted


def test_register_partial_exit_keeps_open_positions_and_consecutive_losses_unchanged():
    rm = RiskManager(start_equity=10_000_000, config=get_config())
    ts = datetime(2026, 7, 16, 9, 5)
    rm.register_entry("A000001", ts)

    rm.register_partial_exit(realized_pnl=50_000)

    assert rm.state.open_positions == 1  # 부분청산은 보유종목수를 줄이지 않는다
    assert rm.state.consecutive_losses == 0
    assert rm.state.realized_pnl_today == 50_000
