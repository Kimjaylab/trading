"""② 장 시작 10분 ~ 30분: 눌림목 매매.

첫 상승 이후 건강한 눌림에서 진입해 두 번째 상승 파동을 노린다.
"""
from __future__ import annotations

from trading.brokers.interfaces import Position
from trading.config import Config, get_config
from trading.data.interfaces import MarketSnapshot
from trading.market_regime.classifier import RegimeResult
from trading.scoring.engine import ScoreResult
from trading.scoring.features import FeatureVector
from trading.strategies.base import EntryDecision, ExitDecision, Strategy

FIRST_RISE_MIN_PCT = 1.5  # 첫 상승으로 인정할 최소 상승폭(시가 대비 %)
MAX_PULLBACK_RETRACE_PCT = 4.0  # 이 이상 눌리면 '건강한 눌림'이 아니라 추세 훼손으로 간주


class PullbackStrategy(Strategy):
    name = "pullback"

    def __init__(self, config: Config | None = None):
        self.config = config or get_config()
        self.cfg = self.config.strategies["pullback"]

    def evaluate_entry(
        self,
        snapshot: MarketSnapshot,
        features: FeatureVector,
        score_result: ScoreResult,
        regime: RegimeResult,
    ) -> EntryDecision:
        threshold = self.cfg["score_entry_threshold"] + regime.threshold_adjustment
        if score_result.score < threshold:
            return EntryDecision(False, reason="score_below_threshold", score=score_result.score)

        mbars = snapshot.minute_bars
        open_price = mbars["open"].iloc[0]
        session_high = mbars["high"].max()
        first_rise_pct = (session_high / open_price - 1) * 100
        if first_rise_pct < FIRST_RISE_MIN_PCT:
            return EntryDecision(False, reason="no_meaningful_first_rise", score=score_result.score)

        retrace_pct = (session_high / snapshot.last_close - 1) * 100
        if not (0 < retrace_pct <= MAX_PULLBACK_RETRACE_PCT):
            return EntryDecision(False, reason="not_a_healthy_pullback", score=score_result.score)

        # 눌림 구간 거래량이 상승 구간 대비 충분히 감소했는가 (매도 실망 물량이 아닌 '쉬어가는' 눌림)
        rise_idx = mbars["high"].values.argmax()
        pullback_bars = mbars.iloc[rise_idx:]
        rise_bars = mbars.iloc[: rise_idx + 1]
        if len(pullback_bars) >= 2 and len(rise_bars) >= 2:
            pullback_vol = pullback_bars["volume"].iloc[1:].mean()
            rise_vol = rise_bars["volume"].mean()
            decay_ratio = pullback_vol / rise_vol if rise_vol > 0 else 1.0
            if decay_ratio > self.cfg["pullback_volume_decay_max"]:
                return EntryDecision(False, reason="pullback_volume_not_decaying", score=score_result.score)

        # 5일선/기준선 근처
        if abs(features.daily_position) > 0.35:
            return EntryDecision(False, reason="too_far_from_baseline", score=score_result.score)

        # 분봉 반등 확인 + 매수세 재유입
        last_two_closes = mbars["close"].tail(2)
        rebounding = len(last_two_closes) >= 2 and last_two_closes.iloc[-1] > last_two_closes.iloc[-2]
        if not (rebounding and features.candle_strength > 0):
            return EntryDecision(False, reason="no_rebound_confirmation", score=score_result.score)

        if not (features.execution_strength > 0 or features.orderbook_imbalance > 0):
            return EntryDecision(False, reason="buying_pressure_not_returning", score=score_result.score)

        entry_price = snapshot.last_close
        stop_price = entry_price * (1 - self.cfg["hard_stop_pct"] / 100)
        target_price = entry_price * (1 + self.cfg["full_take_profit_pct"] / 100)
        return EntryDecision(True, stop_price=stop_price, target_price=target_price, reason="healthy_pullback_rebound", score=score_result.score)

    def evaluate_exit(
        self,
        snapshot: MarketSnapshot,
        features: FeatureVector,
        position: Position,
        minutes_held: float,
        partial_exit_done: bool = False,
    ) -> ExitDecision:
        price = snapshot.last_close
        profit_pct = (price / position.avg_price - 1) * 100

        if price <= position.stop_price:
            return ExitDecision(True, reason="hard_stop_hit")

        if profit_pct >= self.cfg["full_take_profit_pct"]:
            return ExitDecision(True, reason="full_take_profit")

        if profit_pct >= self.cfg["partial_take_profit_pct"]:
            if not partial_exit_done:
                return ExitDecision(True, reason="partial_take_profit", exit_fraction=self.cfg["partial_exit_fraction"])
            if features.minute_trend < -0.2:
                return ExitDecision(True, reason="momentum_weakening_after_partial")

        if minutes_held >= self.cfg["max_hold_minutes"]:
            return ExitDecision(True, reason="max_hold_time")

        return ExitDecision(False)
