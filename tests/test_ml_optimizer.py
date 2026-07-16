from datetime import datetime, timedelta

import numpy as np

from trading.backtest.engine import BacktestResult, TradeRecord
from trading.config import get_config
from trading.ml.optimizer import WeightOptimizer
from trading.scoring.features import FEATURE_NAMES


def _synthetic_result(n: int = 80, seed: int = 0) -> BacktestResult:
    rng = np.random.default_rng(seed)
    trades = []
    base_ts = datetime(2026, 7, 16, 9, 0)
    for i in range(n):
        features = {name: float(rng.uniform(-1, 1)) for name in FEATURE_NAMES}
        # execution_strength가 높을수록 이기도록 설계 (신호 있는 피처)
        # 나머지 피처는 순수 노이즈 (신호 없는 피처 -> L1이 0으로 수축해야 함)
        win_prob = 1 / (1 + np.exp(-4 * features["execution_strength"]))
        win = rng.random() < win_prob
        pnl_pct = rng.uniform(1, 8) if win else -rng.uniform(1, 4)
        trades.append(
            TradeRecord(
                symbol=f"A{i:06d}",
                phase="breakout" if i % 2 == 0 else "trend_pullback",
                entry_ts=base_ts + timedelta(minutes=i),
                exit_ts=base_ts + timedelta(minutes=i + 5),
                entry_price=10000,
                exit_price=10000 * (1 + pnl_pct / 100),
                quantity=10,
                pnl_krw=10000 * (pnl_pct / 100) * 10,
                pnl_pct=pnl_pct,
                exit_reason="target_reached" if win else "hard_stop_hit",
                entry_score=win_prob,
                features_at_entry=features,
            )
        )
    return BacktestResult(trades=trades, equity_curve=[(base_ts, 50_000_000)], final_equity=50_000_000, start_equity=50_000_000)


def test_optimizer_recovers_informative_feature():
    result = _synthetic_result(n=100, seed=1)
    optimizer = WeightOptimizer(get_config())
    weights, report = optimizer.fit_from_result(result)

    assert report.trained
    assert weights["default"]["execution_strength"] > 0.2


def test_optimizer_drops_pure_noise_features():
    result = _synthetic_result(n=100, seed=1)
    optimizer = WeightOptimizer(get_config())
    weights, report = optimizer.fit_from_result(result)

    # 신호 없는 피처 다수가 0으로 수축(=제거)되어야 한다
    assert len(report.dropped_features) >= 5


def test_optimizer_skips_when_insufficient_samples():
    result = _synthetic_result(n=5, seed=2)
    optimizer = WeightOptimizer(get_config())
    weights, report = optimizer.fit_from_result(result)

    assert not report.trained
