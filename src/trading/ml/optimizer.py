"""트레이드 로그로 스코어링 가중치를 학습(최적화)한다.

L1 정규화 로지스틱회귀를 사용해 승패를 예측하는 계수를 구한다.
L1은 기여도가 낮은 피처의 계수를 자연스럽게 0에 가깝게 수축시키므로,
'불필요한 조건 제거'가 수작업이 아니라 데이터 기반으로 이루어진다.
표본이 충분한 전략(phase)에 한해 phase별 보정치를 별도로 학습하고,
표본이 적으면 공통(default) 가중치만 사용해 과적합을 피한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.linear_model import LogisticRegression

from trading.backtest.engine import BacktestResult
from trading.config import Config, get_config
from trading.ml.dataset import build_dataset
from trading.scoring.engine import ScoringEngine
from trading.scoring.features import FEATURE_NAMES

MIN_SAMPLES_OVERALL = 20
MIN_SAMPLES_PER_PHASE = 15
DROP_THRESHOLD = 0.03  # 이 값 미만인 |가중치|는 0으로 수축 (조건 제거)
MAX_ABS_WEIGHT = 1.5  # sigmoid 포화를 막기 위한 클리핑


@dataclass
class OptimizationReport:
    n_samples: int
    trained: bool
    dropped_features: list[str] = field(default_factory=list)
    phase_reports: dict[str, str] = field(default_factory=dict)
    train_accuracy: float = 0.0


class WeightOptimizer:
    def __init__(self, config: Config | None = None):
        self.config = config or get_config()

    def _fit_logistic(self, X: np.ndarray, y: np.ndarray) -> np.ndarray | None:
        if len(np.unique(y)) < 2 or len(y) < 5:
            return None
        model = LogisticRegression(penalty="l1", solver="liblinear", C=0.8, max_iter=2000)
        model.fit(X, y)
        coefs = model.coef_[0]
        return coefs

    def fit_from_result(self, result: BacktestResult) -> tuple[dict, OptimizationReport]:
        df = build_dataset(result)
        available = [f for f in FEATURE_NAMES if f in df.columns]
        weights = self.config.load_weights()
        report = OptimizationReport(n_samples=len(df), trained=False)

        if len(df) < MIN_SAMPLES_OVERALL:
            report.phase_reports["_overall"] = f"표본 부족({len(df)}건) - 기존 가중치 유지"
            return weights, report

        X_all = df[available].to_numpy(dtype=float)
        y_all = df["win"].to_numpy(dtype=int)
        coefs = self._fit_logistic(X_all, y_all)
        if coefs is None:
            report.phase_reports["_overall"] = "단일 클래스(전부 승 또는 전부 패) - 학습 불가"
            return weights, report

        coefs = np.clip(coefs, -MAX_ABS_WEIGHT, MAX_ABS_WEIGHT)
        new_default = {}
        dropped = []
        for name, coef in zip(available, coefs):
            if abs(coef) < DROP_THRESHOLD:
                new_default[name] = 0.0
                dropped.append(name)
            else:
                new_default[name] = float(coef)
        for name in FEATURE_NAMES:
            new_default.setdefault(name, weights.get("default", {}).get(name, 0.0))

        weights["default"] = new_default
        weights["bias"] = float(-np.log(1 / max(y_all.mean(), 1e-6) - 1)) if 0 < y_all.mean() < 1 else weights.get("bias", 0.0)
        report.trained = True
        report.dropped_features = dropped
        report.train_accuracy = _train_acc(X_all, y_all, coefs, weights["bias"])

        for phase in df["phase"].unique():
            subset = df[df["phase"] == phase]
            if len(subset) < MIN_SAMPLES_PER_PHASE:
                report.phase_reports[phase] = f"표본 부족({len(subset)}건) - default 가중치만 사용"
                weights.setdefault(phase, {})
                continue
            Xp = subset[available].to_numpy(dtype=float)
            yp = subset["win"].to_numpy(dtype=int)
            coefs_p = self._fit_logistic(Xp, yp)
            if coefs_p is None:
                report.phase_reports[phase] = "단일 클래스 - default 가중치만 사용"
                continue
            coefs_p = np.clip(coefs_p, -MAX_ABS_WEIGHT, MAX_ABS_WEIGHT)
            override = {}
            for name, coef, base_coef in zip(available, coefs_p, coefs):
                delta = coef - base_coef
                if abs(delta) >= DROP_THRESHOLD:
                    override[name] = float(coef)
            weights[phase] = override
            report.phase_reports[phase] = f"{len(subset)}건 학습, {len(override)}개 피처 보정"

        return weights, report

    def optimize_and_save(self, result: BacktestResult) -> OptimizationReport:
        weights, report = self.fit_from_result(result)
        if report.trained:
            ScoringEngine(self.config).save_weights(weights)
        return report


def _train_acc(X: np.ndarray, y: np.ndarray, coefs: np.ndarray, bias: float) -> float:
    logits = X @ coefs + bias
    preds = (logits > 0).astype(int)
    return float((preds == y).mean())
