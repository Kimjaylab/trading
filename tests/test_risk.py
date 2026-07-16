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
