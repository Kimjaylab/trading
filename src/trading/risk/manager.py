"""리스크 관리 모듈.

계좌/포지션 레벨의 안전장치를 스코어링과 완전히 분리해 담당한다.
스코어가 아무리 높아도 이 모듈이 거부하면 진입할 수 없다 - "얼마나 좋아 보이는가"와
"지금 매매해도 되는가"를 서로 다른 책임으로 나누기 위함이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from trading.config import Config, get_config


@dataclass
class RiskState:
    start_equity: float
    current_equity: float
    realized_pnl_today: float = 0.0
    consecutive_losses: int = 0
    open_positions: int = 0
    entry_timestamps: list[datetime] = field(default_factory=list)
    cooldown_until: dict[str, datetime] = field(default_factory=dict)
    trading_halted: bool = False
    halt_reason: str = ""


class RiskManager:
    def __init__(self, start_equity: float, config: Config | None = None):
        self.config = config or get_config()
        self.cfg = self.config.risk
        self.state = RiskState(start_equity=start_equity, current_equity=start_equity)

    def update_equity(self, equity: float) -> None:
        self.state.current_equity = equity
        daily_loss_pct = (self.state.start_equity - equity) / self.state.start_equity * 100
        if daily_loss_pct >= self.cfg["daily_max_loss_pct"] and not self.state.trading_halted:
            self.state.trading_halted = True
            self.state.halt_reason = f"일일 최대 손실 한도 도달 ({daily_loss_pct:.2f}%)"

    def position_size_multiplier(self) -> float:
        """연속 손실 발생 시 다음 진입 사이즈를 축소한다."""
        if self.state.consecutive_losses >= 2:
            return 1.0 - self.cfg.get("consecutive_loss_size_cutback_pct", 0) / 100
        return 1.0

    def can_enter(
        self,
        symbol: str,
        timestamp: datetime,
        entry_price: float,
        stop_price: float,
        target_price: float,
    ) -> tuple[bool, str]:
        if self.state.trading_halted:
            return False, self.state.halt_reason

        if self.state.consecutive_losses >= self.cfg["max_consecutive_losses"]:
            return False, f"연속 손실 {self.state.consecutive_losses}회로 당일 매매 중단"

        if self.state.open_positions >= self.cfg["max_open_positions"]:
            return False, "최대 보유 종목 수 초과"

        cooldown = self.state.cooldown_until.get(symbol)
        if cooldown and timestamp < cooldown:
            return False, f"동일 종목 재진입 제한 (해제: {cooldown.strftime('%H:%M')})"

        window_start = timestamp - timedelta(minutes=5)
        recent_entries = [t for t in self.state.entry_timestamps if t >= window_start]
        if len(recent_entries) >= self.cfg["max_entries_per_5min"]:
            return False, "동일 시간대 과도한 신규 진입 제한"

        risk = entry_price - stop_price
        reward = target_price - entry_price
        if risk <= 0:
            return False, "손절가가 진입가 이상 (잘못된 주문)"
        reward_risk_ratio = reward / risk
        if reward_risk_ratio < self.cfg["min_reward_risk_ratio"]:
            return False, f"손익비 미달 ({reward_risk_ratio:.2f} < {self.cfg['min_reward_risk_ratio']})"

        return True, "ok"

    def position_size(self, cash_available: float, entry_price: float) -> int:
        max_weight = self.cfg["max_position_weight_pct"] / 100
        multiplier = self.position_size_multiplier()
        budget = cash_available * max_weight * multiplier
        if entry_price <= 0:
            return 0
        return max(int(budget // entry_price), 0)

    def register_entry(self, symbol: str, timestamp: datetime) -> None:
        self.state.open_positions += 1
        self.state.entry_timestamps.append(timestamp)

    def register_exit(self, symbol: str, timestamp: datetime, realized_pnl: float) -> None:
        self.state.open_positions = max(self.state.open_positions - 1, 0)
        self.state.realized_pnl_today += realized_pnl
        if realized_pnl < 0:
            self.state.consecutive_losses += 1
            cooldown_min = self.cfg.get("reentry_cooldown_minutes", 0)
            self.state.cooldown_until[symbol] = timestamp + timedelta(minutes=cooldown_min)
        else:
            self.state.consecutive_losses = 0

    def reset_day(self, start_equity: float) -> None:
        self.state = RiskState(start_equity=start_equity, current_equity=start_equity)
