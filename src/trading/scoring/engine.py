"""가중치 기반 진입 스코어링 엔진.

score = sigmoid(bias + sum_i(weight_i * feature_i))

가중치는 config/weights.json에서 로드하며, 전략(phase)별 보정치를 default 위에 덮어쓴다.
ML 최적화(trading.ml.optimizer)가 이 파일을 갱신하면 다음 실행부터 자동 반영된다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from trading.config import Config, get_config
from trading.scoring.features import FEATURE_NAMES, FeatureVector


def _sigmoid(x: float) -> float:
    return float(1.0 / (1.0 + np.exp(-x)))


@dataclass
class ScoreResult:
    score: float  # 0~1 진입 확률(적합도)
    raw: float  # sigmoid 이전 가중합
    contributions: dict[str, float]  # 피처별 기여도 (설명가능성)


class ScoringEngine:
    def __init__(self, config: Config | None = None):
        self.config = config or get_config()
        self._weights = self.config.load_weights()

    def reload_weights(self) -> None:
        self._weights = self.config.load_weights()

    def weight_vector(self, phase: str) -> tuple[float, dict[str, float]]:
        base = dict(self._weights.get("default", {}))
        override = self._weights.get(phase, {})
        base.update(override)
        bias = float(self._weights.get("bias", 0.0))
        return bias, {name: float(base.get(name, 0.0)) for name in FEATURE_NAMES}

    def score(self, features: FeatureVector, phase: str) -> ScoreResult:
        bias, weights = self.weight_vector(phase)
        contributions = {name: weights[name] * getattr(features, name) for name in FEATURE_NAMES}
        raw = bias + sum(contributions.values())
        return ScoreResult(score=_sigmoid(raw), raw=raw, contributions=contributions)

    def save_weights(self, weights: dict, path: Path | str | None = None) -> None:
        target = Path(path) if path else self.config.weights_path()
        weights.setdefault("_meta", {})
        weights["_meta"]["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(target, "w", encoding="utf-8") as f:
            json.dump(weights, f, ensure_ascii=False, indent=2)
        self._weights = weights
