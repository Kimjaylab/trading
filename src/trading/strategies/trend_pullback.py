"""③ 장 시작 30분 이후 ~ 장 마감: 눌림목 + 추세매매.

이미 추세가 형성된 강한 종목만 매매한다.
"""
from __future__ import annotations

from datetime import datetime

from trading.brokers.interfaces import Position
from trading.config import Config, get_config
from trading.data.interfaces import MarketSnapshot
from trading.indicators import technical as ta
from trading.market_regime.classifier import RegimeResult
from trading.scoring.engine import ScoreResult
from trading.scoring.features import FeatureVector
from trading.strategies.base import EntryDecision, ExitDecision, Strategy
from trading.utils.time_utils import minutes_until


class TrendPullbackStrategy(Strategy):
    name = "trend_pullback"

    def __init__(self, config: Config | None = None):
        self.config = config or get_config()
        self.cfg = self.config.strategies["trend_pullback"]

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

        # 우상향 추세 + 기준선/5일선 위
        if features.daily_position < 0.15:
            return EntryDecision(False, reason="not_above_baseline_or_ma5", score=score_result.score)

        if features.minute_trend < 0:
            return EntryDecision(False, reason="minute_trend_not_positive", score=score_result.score)

        # 거래량이 완전히 죽지 않았는가
        if features.volume_surge < -0.5:
            return EntryDecision(False, reason="volume_dead", score=score_result.score)

        # 눌림 이후 양봉 출현 (직전 봉은 약세/횡보, 현재 봉은 양봉)
        mbars = snapshot.minute_bars
        if len(mbars) >= 3:
            prev_body = mbars["close"].iloc[-2] - mbars["open"].iloc[-2]
            curr_body = mbars["close"].iloc[-1] - mbars["open"].iloc[-1]
            if not (curr_body > 0 and features.candle_strength > 0.15):
                return EntryDecision(False, reason="no_bullish_candle_after_pullback", score=score_result.score)

        entry_price = snapshot.last_close
        daily = snapshot.daily_bars
        baseline = ta.ichimoku_baseline(daily["high"], daily["low"], 26).iloc[-1] if len(daily) > 0 else entry_price * 0.95
        stop_price = min(baseline, entry_price * (1 - 3.0 / 100))
        target_price = entry_price * (1 + 6.0 / 100)
        return EntryDecision(True, stop_price=stop_price, target_price=target_price, reason="trend_intact_pullback_bounce", score=score_result.score)

    def evaluate_exit(
        self,
        snapshot: MarketSnapshot,
        features: FeatureVector,
        position: Position,
        minutes_held: float,
    ) -> ExitDecision:
        price = snapshot.last_close

        if price <= position.stop_price:
            return ExitDecision(True, reason="baseline_broken")

        if features.daily_position < -0.1 or features.minute_trend < -0.4:
            return ExitDecision(True, reason="trend_damaged")

        minutes_to_close = minutes_until(snapshot.timestamp, self.config.hard_close_time)
        if minutes_to_close <= self.cfg["force_exit_before_close_minutes"]:
            return ExitDecision(True, reason="force_exit_before_close")

        return ExitDecision(False)
