"""이벤트 기반(분봉 단위) 백테스트 엔진.

실거래 루프(trading.execution.live_runner)와 최대한 동일한 순서로 동작하도록 설계했다:
제외필터 -> 스코어링 -> 전략별 진입판단 -> 리스크매니저 승인 -> 체결 -> (다음 루프에서) 청산판단.
이렇게 하면 백테스트에서 검증한 로직을 실거래로 옮길 때 동작이 달라질 위험이 줄어든다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from trading.brokers.interfaces import OrderSide, OrderStatus, Position
from trading.brokers.paper_broker import PaperBroker
from trading.config import Config, get_config
from trading.data.interfaces import MarketDataProvider
from trading.filters.exclusion import is_excluded
from trading.market_regime.classifier import MarketRegimeClassifier, RegimeResult
from trading.risk.manager import RiskManager
from trading.scoring.engine import ScoringEngine
from trading.scoring.features import FeatureExtractor, FeatureVector
from trading.strategies.selector import PhaseSelector
from trading.utils.time_utils import minutes_between, minutes_since_open

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    symbol: str
    phase: str
    entry_ts: datetime
    exit_ts: datetime
    entry_price: float
    exit_price: float
    quantity: int
    pnl_krw: float
    pnl_pct: float
    exit_reason: str
    entry_score: float
    features_at_entry: dict[str, float]

    @property
    def win(self) -> bool:
        return self.pnl_krw > 0


@dataclass
class BacktestResult:
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    final_equity: float = 0.0
    start_equity: float = 0.0
    rejected_by_risk: list[tuple[datetime, str, str]] = field(default_factory=list)
    excluded_counts: dict[str, int] = field(default_factory=dict)


class BacktestEngine:
    def __init__(
        self,
        data_provider: MarketDataProvider,
        initial_cash: float = 50_000_000,
        config: Config | None = None,
        index_close: pd.Series | None = None,
        broker=None,
        ignore_symbols: set[str] | None = None,
    ):
        self.data_provider = data_provider
        self.config = config or get_config()
        self.broker = broker or PaperBroker(data_provider, initial_cash)
        self.risk_manager = RiskManager(initial_cash, self.config)
        self.feature_extractor = FeatureExtractor(self.config)
        self.scoring_engine = ScoringEngine(self.config)
        self.regime_classifier = MarketRegimeClassifier(self.config)
        self.phase_selector = PhaseSelector(self.config)
        self.index_close = index_close
        # 이 시스템이 산 게 아닌 수동 보유종목 등, 아예 건드리면 안 되는 종목 목록.
        # 신규 진입/청산 판단 양쪽에서 완전히 제외한다(자산 평가에는 그대로 포함됨).
        self.ignore_symbols = ignore_symbols or set()

        # 자정을 넘기는 세션(미국장)도 올바르게 처리하기 위해 개장 시각 기준 경과분(elapsed
        # minutes)으로 비교한다 - 단순 ts.time() 비교는 자정 넘김 세션에서 깨진다.
        self._force_liquidation_elapsed = minutes_between(self.config.market_open, self.config.force_liquidation_time)
        self._hard_close_elapsed = minutes_between(self.config.market_open, self.config.hard_close_time)

        self._entry_context: dict[str, dict] = {}

    def _portfolio_value(self, timestamp: datetime) -> float:
        """현금 + 보유 포지션 평가액. PaperBroker는 자체 구현을 쓰고(빠름, API 호출 없음),
        실거래 브로커(KISBroker 등)는 BrokerClient 인터페이스만 만족하면 되므로 여기서
        get_cash_balance()/get_positions()를 조합해 계산한다."""
        if hasattr(self.broker, "portfolio_value"):
            return self.broker.portfolio_value(timestamp)

        value = self.broker.get_cash_balance()
        for symbol, pos in self.broker.get_positions().items():
            snap = self.data_provider.get_snapshot(symbol, timestamp)
            value += snap.last_close * pos.quantity
        return value

    def _current_regime(self, timestamp: datetime) -> RegimeResult:
        if self.index_close is None:
            from trading.market_regime.classifier import Regime

            return RegimeResult(Regime.SIDEWAYS, 0, 0, 0, self.config.regime["threshold_adjust"]["sideways"])
        series = self.index_close.loc[:timestamp]
        return self.regime_classifier.classify(series)

    def run(self) -> BacktestResult:
        result = BacktestResult(start_equity=self.broker.get_cash_balance())
        excluded_counts: dict[str, int] = {}

        for ts in self.data_provider.get_session_timestamps():
            self.step(ts, result, excluded_counts)

        self._liquidate_all(self.data_provider.get_session_timestamps()[-1], result)
        result.final_equity = self.broker.get_cash_balance()
        result.excluded_counts = excluded_counts
        return result

    def step(self, ts: datetime, result: BacktestResult, excluded_counts: dict[str, int]) -> None:
        """timestamp ts 하나에 대한 청산->진입->자산갱신 한 사이클.

        백테스트(run)와 실시간 루프(trading.execution.live_runner)가 동일 로직을 공유하도록
        분리했다 - 백테스트로 검증한 판단 로직이 실거래에서도 그대로 재사용된다.
        """
        universe = self.data_provider.get_universe(ts)
        snapshots = {sym: self.data_provider.get_snapshot(sym, ts) for sym in universe}
        features = self.feature_extractor.extract_batch(snapshots)
        regime = self._current_regime(ts)
        phase = self.phase_selector.phase_name(ts)

        self._process_exits(ts, snapshots, features, result)

        elapsed = minutes_since_open(ts, self.config.market_open)
        past_liquidation_cutoff = elapsed >= self._force_liquidation_elapsed
        if not past_liquidation_cutoff:
            self._process_entries(ts, phase, regime, snapshots, features, result, excluded_counts)

        equity = self._portfolio_value(ts)
        self.risk_manager.update_equity(equity)
        result.equity_curve.append((ts, equity))

    def _process_exits(self, ts, snapshots, features, result: BacktestResult) -> None:
        for symbol, position in list(self.broker.get_positions().items()):
            if symbol in self.ignore_symbols:
                continue
            snap = snapshots.get(symbol) or self.data_provider.get_snapshot(symbol, ts)
            feat = features.get(symbol) or self.feature_extractor.extract_single(snap)
            minutes_held = (ts - position.opened_at).total_seconds() / 60

            ctx = self._entry_context.get(symbol, {})
            # 실거래 브로커(KISBroker 등)는 get_positions()가 매번 새 응답으로 Position을
            # 만들어 stop/target을 모른다(항상 0.0) - 내부 트래커(ctx)의 값으로 덮어써야
            # 손절/익절이 실거래에서도 실제로 동작한다. PaperBroker는 이미 올바른 값이 있어
            # 덮어써도 결과가 같다.
            if "stop_price" in ctx:
                position.stop_price = ctx["stop_price"]
            elif position.stop_price <= 0:
                # 내부 트래커에 없는(다른 프로세스 실행분/원래 보유 등) '복구된' 포지션 -
                # 손절가가 0이면 절대 손절이 발동하지 않으므로, 평단가 기준 보수적 기본값을 부여한다.
                fallback_cfg = self.config.strategies["trend_pullback"]
                position.stop_price = position.avg_price * (1 - fallback_cfg["hard_stop_pct"] / 100)
                logger.warning(
                    "%s: 내부 트래커에 없는 포지션 발견 - 평단가(%.2f) 기준 임시 손절가(%.2f) 부여",
                    symbol, position.avg_price, position.stop_price,
                )
            if "target_price" in ctx:
                position.target_price = ctx["target_price"]
            elif position.target_price <= 0:
                fallback_cfg = self.config.strategies["trend_pullback"]
                position.target_price = position.avg_price * (1 + fallback_cfg["full_take_profit_pct"] / 100)
            partial_exit_done = ctx.get("partial_exit_done", False)

            strategy = self.phase_selector.get(position.strategy)
            decision = strategy.evaluate_exit(snap, feat, position, minutes_held, partial_exit_done)

            elapsed = minutes_since_open(ts, self.config.market_open)
            if not decision.should_exit and elapsed >= self._hard_close_elapsed:
                decision.should_exit = True
                decision.reason = "hard_close_liquidation"
                decision.exit_fraction = 1.0

            if decision.should_exit:
                self._execute_exit(ts, symbol, position, decision.reason, result, decision.exit_fraction)

    def _execute_exit(
        self, ts, symbol, position: Position, reason: str, result: BacktestResult, exit_fraction: float = 1.0
    ) -> None:
        original_qty = position.quantity
        if exit_fraction >= 1.0 or original_qty <= 1:
            sell_qty = original_qty
        else:
            sell_qty = min(max(1, round(original_qty * exit_fraction)), original_qty - 1)
        is_full_close = sell_qty >= original_qty

        order = self.broker.place_order(symbol, OrderSide.SELL, sell_qty, 0.0, ts, strategy=position.strategy)
        if order.status != OrderStatus.FILLED:
            return

        if is_full_close:
            self.risk_manager.register_exit(symbol, ts, order.realized_pnl)
            ctx = self._entry_context.pop(symbol, None)
        else:
            self.risk_manager.register_partial_exit(order.realized_pnl)
            ctx = self._entry_context.get(symbol)
            if ctx is not None:
                ctx["partial_exit_done"] = True

        if ctx is not None:
            pnl_pct = (order.price / ctx["entry_price"] - 1) * 100
            result.trades.append(
                TradeRecord(
                    symbol=symbol,
                    phase=ctx["phase"],
                    entry_ts=ctx["entry_ts"],
                    exit_ts=ts,
                    entry_price=ctx["entry_price"],
                    exit_price=order.price,
                    quantity=sell_qty,
                    pnl_krw=order.realized_pnl,
                    pnl_pct=pnl_pct,
                    exit_reason=reason,
                    entry_score=ctx["score"],
                    features_at_entry=ctx["features"],
                )
            )

    def _process_entries(self, ts, phase, regime, snapshots, features, result: BacktestResult, excluded_counts: dict) -> None:
        strategy = self.phase_selector.get(phase)
        positions = self.broker.get_positions()
        entered = 0

        for symbol, snap in snapshots.items():
            if symbol in positions or symbol in self.ignore_symbols:
                continue

            excluded, reasons = is_excluded(snap, phase, self.config)
            if excluded:
                for r in reasons:
                    key = r.split(" (")[0].split(":")[0]
                    excluded_counts[key] = excluded_counts.get(key, 0) + 1
                logger.debug("%s 제외(%s): %s", symbol, phase, reasons)
                continue

            feat = features[symbol]
            score_result = self.scoring_engine.score(feat, phase)
            entry_decision = strategy.evaluate_entry(snap, feat, score_result, regime)
            if not entry_decision.should_enter:
                logger.debug(
                    "%s 진입보류(%s): score=%.3f reason=%s", symbol, phase, score_result.score, entry_decision.reason
                )
                continue

            can_enter, reject_reason = self.risk_manager.can_enter(
                symbol, ts, snap.last_close, entry_decision.stop_price, entry_decision.target_price
            )
            if not can_enter:
                result.rejected_by_risk.append((ts, symbol, reject_reason))
                logger.debug("%s 리스크거부(%s): %s", symbol, phase, reject_reason)
                continue

            qty = self.risk_manager.position_size(self.broker.get_cash_balance(), snap.last_close)
            if qty <= 0:
                logger.debug("%s 매수수량 0 (예산 부족 - 가격 %.2f)", symbol, snap.last_close)
                continue

            order = self.broker.place_order(
                symbol, OrderSide.BUY, qty, snap.last_close, ts,
                strategy=phase, stop_price=entry_decision.stop_price, target_price=entry_decision.target_price,
            )
            if order.status == OrderStatus.FILLED:
                self.risk_manager.register_entry(symbol, ts)
                self._entry_context[symbol] = {
                    "entry_price": order.price,
                    "entry_ts": ts,
                    "phase": phase,
                    "score": score_result.score,
                    "features": feat.as_dict(),
                    "stop_price": entry_decision.stop_price,
                    "target_price": entry_decision.target_price,
                    "partial_exit_done": False,
                }
                entered += 1
                logger.info(
                    "%s 진입 체결: phase=%s qty=%d price=%.2f score=%.3f reason=%s",
                    symbol, phase, qty, order.price, score_result.score, entry_decision.reason,
                )
            else:
                logger.debug("%s 주문 거부/실패: %s", symbol, order.reason)

        if entered == 0 and snapshots:
            logger.info("이번 주기 신규 진입 없음 (스캔 %d종목, phase=%s) - DEBUG 로그 레벨에서 종목별 사유 확인 가능", len(snapshots), phase)

    def _liquidate_all(self, ts: datetime, result: BacktestResult) -> None:
        for symbol, position in list(self.broker.get_positions().items()):
            self._execute_exit(ts, symbol, position, "session_end_liquidation", result)
