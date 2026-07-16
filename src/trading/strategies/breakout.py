"""① 장 시작 ~ 10분: 돌파매매.

시초 강세 종목의 첫 급등 파동만 짧게 먹고 나온다. 오래 보유하지 않는다.
"""
from __future__ import annotations

from trading.brokers.interfaces import Position
from trading.config import Config, get_config
from trading.data.interfaces import MarketSnapshot
from trading.indicators import technical as ta
from trading.market_regime.classifier import RegimeResult
from trading.scoring.engine import ScoreResult
from trading.scoring.features import FeatureVector
from trading.strategies.base import EntryDecision, ExitDecision, Strategy


class BreakoutStrategy(Strategy):
    name = "breakout"

    def __init__(self, config: Config | None = None):
        self.config = config or get_config()
        self.cfg = self.config.strategies["breakout"]

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

        # 실제 돌파(직전 고점/시초가 상회) 여부를 하드 조건으로 재확인한다.
        if features.prior_high_breakout < 1.0:
            return EntryDecision(False, reason="not_a_real_breakout", score=score_result.score)

        # 돌파 캔들 자체가 약하면(도지 등) 가짜 돌파일 가능성이 높다.
        if features.candle_strength < 0.25:
            return EntryDecision(False, reason="weak_breakout_candle", score=score_result.score)

        entry_price = snapshot.last_close
        stop_price = entry_price * (1 - self.cfg["hard_stop_pct"] / 100)
        target_price = entry_price * (1 + self.cfg["target_profit_pct"][1] / 100)
        return EntryDecision(True, stop_price=stop_price, target_price=target_price, reason="breakout_confirmed", score=score_result.score)

    def evaluate_exit(
        self,
        snapshot: MarketSnapshot,
        features: FeatureVector,
        position: Position,
        minutes_held: float,
    ) -> ExitDecision:
        price = snapshot.last_close
        profit_pct = (price / position.avg_price - 1) * 100

        if price <= position.stop_price:
            return ExitDecision(True, reason="hard_stop_hit")

        # 돌파 실패: 돌파 이후 재차 진입가(=돌파 시점 고점) 아래로 반납
        if price < position.avg_price and profit_pct <= -self.cfg["hard_stop_pct"] * 0.5:
            mbars = snapshot.minute_bars
            recent_high = mbars["high"].tail(5).max()
            if price < recent_high * 0.995:
                return ExitDecision(True, reason="breakout_failed")

        if profit_pct >= self.cfg["target_profit_pct"][1]:
            return ExitDecision(True, reason="target_reached")

        if profit_pct >= self.cfg["target_profit_pct"][0] and snapshot.execution_strength < self.cfg["execution_strength_exit_threshold"]:
            return ExitDecision(True, reason="momentum_weakening_at_profit")

        if snapshot.execution_strength < self.cfg["execution_strength_exit_threshold"] - 15:
            return ExitDecision(True, reason="execution_strength_collapsed")

        if minutes_held >= self.cfg["max_hold_minutes"]:
            return ExitDecision(True, reason="max_hold_time")

        return ExitDecision(False)
