"""브로커(증권사) 연동 인터페이스. 백테스트/페이퍼트레이딩/실거래가 동일한 인터페이스를 공유한다."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    PENDING = "PENDING"
    CANCELLED = "CANCELLED"


@dataclass
class Position:
    symbol: str
    quantity: int
    avg_price: float
    opened_at: datetime
    strategy: str
    stop_price: float
    target_price: float


@dataclass
class OrderResult:
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    status: OrderStatus
    order_id: str
    timestamp: datetime
    reason: str = ""
    realized_pnl: float = 0.0  # 매도 체결 시 실현손익(원), 매수는 항상 0


class BrokerClient(ABC):
    """실거래/페이퍼트레이딩 공용 브로커 인터페이스."""

    @abstractmethod
    def get_cash_balance(self) -> float:
        ...

    @abstractmethod
    def get_positions(self) -> dict[str, Position]:
        ...

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        price: float,
        timestamp: datetime,
        strategy: str = "",
        stop_price: float = 0.0,
        target_price: float = 0.0,
    ) -> OrderResult:
        ...
