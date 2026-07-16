"""시세/수급 데이터를 스코어링·전략 계층에 공급하는 인터페이스.

실거래에서는 이 인터페이스의 구현체가 증권사 API(REST/WebSocket)를 감싸고,
백테스트에서는 SyntheticDataProvider나 CSVDataProvider가 동일한 인터페이스를 구현한다.
전략/스코어링 코드는 어떤 구현체가 붙어있는지 몰라도 되도록 설계되어 있다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd


@dataclass
class MarketSnapshot:
    """특정 시각(timestamp)에 특정 종목(symbol)의 스코어링에 필요한 모든 원자료."""

    symbol: str
    timestamp: datetime

    # 당일 분봉 (지금까지 누적, 마지막 행이 현재 분)
    minute_bars: pd.DataFrame  # columns: open, high, low, close, volume
    # 과거 일봉 (당일 제외, 최근 N일)
    daily_bars: pd.DataFrame  # columns: open, high, low, close, volume, trading_value

    # 호가/체결 미시구조
    bid_price: float
    ask_price: float
    bid_qty_top5: int
    ask_qty_top5: int
    execution_strength: float  # 체결강도(%), 100 = 매수/매도 체결 균형, >100 매수 우위

    # 수급
    program_net_buy_krw: float
    foreign_net_buy_krw: float
    institution_net_buy_krw: float

    # 상대강도
    sector_return_pct: float
    theme_return_pct: float
    index_return_pct: float

    # 상태 플래그
    vi_triggered: bool
    admin_flags: list[str] = field(default_factory=list)

    # 유동성
    today_trading_value_krw: float = 0.0
    avg_trading_value_20d_krw: float = 0.0

    @property
    def last_close(self) -> float:
        return float(self.minute_bars["close"].iloc[-1])

    @property
    def today_open(self) -> float:
        return float(self.minute_bars["open"].iloc[0])

    @property
    def spread_pct(self) -> float:
        mid = (self.bid_price + self.ask_price) / 2
        if mid <= 0:
            return 999.0
        return (self.ask_price - self.bid_price) / mid * 100


class MarketDataProvider(ABC):
    """실시간/백테스트 공용 시세 공급자 인터페이스."""

    @abstractmethod
    def get_universe(self, timestamp: datetime) -> list[str]:
        """해당 시각에 스캔 대상이 되는 종목 코드 목록."""

    @abstractmethod
    def get_snapshot(self, symbol: str, timestamp: datetime) -> MarketSnapshot:
        """해당 시각 기준 스코어링에 필요한 스냅샷."""

    @abstractmethod
    def get_session_timestamps(self) -> list[datetime]:
        """백테스트/시뮬레이션이 순회할 타임스탬프 목록 (1분 간격 등)."""
