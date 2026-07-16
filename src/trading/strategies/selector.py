"""장 시작 시각 기준 경과 시간에 따라 사용할 전략을 자동으로 전환한다."""
from __future__ import annotations

from datetime import datetime

from trading.config import Config, get_config
from trading.strategies.base import Strategy
from trading.strategies.breakout import BreakoutStrategy
from trading.strategies.pullback import PullbackStrategy
from trading.strategies.trend_pullback import TrendPullbackStrategy
from trading.utils.time_utils import minutes_since_open


class PhaseSelector:
    def __init__(self, config: Config | None = None):
        self.config = config or get_config()
        self.phases = self.config.phases
        self._strategies: dict[str, Strategy] = {
            "breakout": BreakoutStrategy(self.config),
            "pullback": PullbackStrategy(self.config),
            "trend_pullback": TrendPullbackStrategy(self.config),
        }

    def phase_name(self, timestamp: datetime) -> str:
        elapsed = minutes_since_open(timestamp, self.config.market_open)
        for phase in self.phases:
            if phase.start_offset_min <= elapsed < phase.end_offset_min:
                return phase.name
        return self.phases[-1].name

    def strategy_for(self, timestamp: datetime) -> Strategy:
        return self._strategies[self.phase_name(timestamp)]

    def get(self, name: str) -> Strategy:
        return self._strategies[name]
