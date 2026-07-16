"""백테스트 및 페이퍼트레이딩용 시뮬레이션 브로커.

MarketDataProvider의 호가(bid/ask)를 이용해 슬리피지를 반영한 체결가를 계산하고,
수수료/거래세를 적용해 실현손익까지 계산한다. 실거래 브로커(KISBroker 등)와
동일한 BrokerClient 인터페이스를 구현하므로, 전략/리스크 코드는 수정 없이
백테스트 -> 페이퍼 -> 실거래로 전환할 수 있다.
"""
from __future__ import annotations

from datetime import datetime

from trading.brokers.interfaces import BrokerClient, OrderResult, OrderSide, OrderStatus, Position
from trading.data.interfaces import MarketDataProvider


class PaperBroker(BrokerClient):
    def __init__(
        self,
        data_provider: MarketDataProvider,
        initial_cash: float,
        commission_rate: float = 0.00015,
        sell_tax_rate: float = 0.0018,
        slippage_pct: float = 0.05,
    ):
        self.data_provider = data_provider
        self.cash = initial_cash
        self.commission_rate = commission_rate
        self.sell_tax_rate = sell_tax_rate
        self.slippage_pct = slippage_pct
        self.positions: dict[str, Position] = {}
        self.order_log: list[OrderResult] = []
        self._seq = 0

    def get_cash_balance(self) -> float:
        return self.cash

    def get_positions(self) -> dict[str, Position]:
        return self.positions

    def portfolio_value(self, timestamp: datetime) -> float:
        value = self.cash
        for symbol, pos in self.positions.items():
            snap = self.data_provider.get_snapshot(symbol, timestamp)
            value += snap.last_close * pos.quantity
        return value

    def _next_order_id(self) -> str:
        self._seq += 1
        return f"PAPER-{self._seq:08d}"

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
        if quantity <= 0:
            return OrderResult(symbol, side, quantity, price, OrderStatus.REJECTED, self._next_order_id(), timestamp, "invalid_quantity")

        snapshot = self.data_provider.get_snapshot(symbol, timestamp)

        if side == OrderSide.BUY:
            fill_price = snapshot.ask_price * (1 + self.slippage_pct / 100)
            cost = fill_price * quantity
            commission = cost * self.commission_rate
            total = cost + commission
            if total > self.cash:
                return OrderResult(symbol, side, quantity, fill_price, OrderStatus.REJECTED, self._next_order_id(), timestamp, "insufficient_cash")

            self.cash -= total
            existing = self.positions.get(symbol)
            if existing is None:
                self.positions[symbol] = Position(
                    symbol=symbol,
                    quantity=quantity,
                    avg_price=fill_price,
                    opened_at=timestamp,
                    strategy=strategy,
                    stop_price=stop_price,
                    target_price=target_price,
                )
            else:
                total_qty = existing.quantity + quantity
                existing.avg_price = (existing.avg_price * existing.quantity + fill_price * quantity) / total_qty
                existing.quantity = total_qty
                existing.stop_price = stop_price or existing.stop_price
                existing.target_price = target_price or existing.target_price

            result = OrderResult(symbol, side, quantity, fill_price, OrderStatus.FILLED, self._next_order_id(), timestamp)
            self.order_log.append(result)
            return result

        # SELL
        existing = self.positions.get(symbol)
        if existing is None or existing.quantity < quantity:
            return OrderResult(symbol, side, quantity, price, OrderStatus.REJECTED, self._next_order_id(), timestamp, "no_position")

        fill_price = snapshot.bid_price * (1 - self.slippage_pct / 100)
        proceeds = fill_price * quantity
        commission = proceeds * self.commission_rate
        tax = proceeds * self.sell_tax_rate
        net = proceeds - commission - tax
        realized_pnl = (fill_price - existing.avg_price) * quantity - commission - tax

        self.cash += net
        existing.quantity -= quantity
        if existing.quantity == 0:
            del self.positions[symbol]

        result = OrderResult(
            symbol, side, quantity, fill_price, OrderStatus.FILLED, self._next_order_id(), timestamp,
            realized_pnl=realized_pnl,
        )
        self.order_log.append(result)
        return result
