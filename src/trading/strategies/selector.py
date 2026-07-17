"""장 시작 시각 기준 경과 시간에 따라 사용할 전략을 자동으로 전환한다."""
from __future__ import annotations

import logging
from datetime import datetime

from trading.config import Config, get_config
from trading.strategies.base import Strategy
from trading.strategies.breakout import BreakoutStrategy
from trading.strategies.pullback import PullbackStrategy
from trading.strategies.trend_pullback import TrendPullbackStrategy
from trading.utils.time_utils import minutes_since_open

logger = logging.getLogger(__name__)

# 브로커 재시작 등으로 내부 트래커(entry_context)를 잃은 뒤 get_positions()로 복구된
# 포지션은 어떤 전략이 열었는지 알 수 없다 - 청산 관리라도 하도록 안전한 기본값으로 대체한다.
FALLBACK_STRATEGY_NAME = "trend_pullback"


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
        if name not in self._strategies:
            logger.warning(
                "알 수 없는 전략명 '%s' (내부 트래커에 없는 복구된 포지션 등) - "
                "안전을 위해 %s 전략 규칙으로 청산 관리한다.", name, FALLBACK_STRATEGY_NAME,
            )
            return self._strategies[FALLBACK_STRATEGY_NAME]
        return self._strategies[name]
