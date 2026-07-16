"""전략 공용 인터페이스와 진입/청산 의사결정 자료구조."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from trading.brokers.interfaces import Position
from trading.data.interfaces import MarketSnapshot
from trading.market_regime.classifier import RegimeResult
from trading.scoring.engine import ScoreResult
from trading.scoring.features import FeatureVector


@dataclass
class EntryDecision:
    should_enter: bool
    stop_price: float = 0.0
    target_price: float = 0.0
    reason: str = ""
    score: float = 0.0


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str = ""


class Strategy(ABC):
    name: str

    @abstractmethod
    def evaluate_entry(
        self,
        snapshot: MarketSnapshot,
        features: FeatureVector,
        score_result: ScoreResult,
        regime: RegimeResult,
    ) -> EntryDecision:
        ...

    @abstractmethod
    def evaluate_exit(
        self,
        snapshot: MarketSnapshot,
        features: FeatureVector,
        position: Position,
        minutes_held: float,
    ) -> ExitDecision:
        ...
