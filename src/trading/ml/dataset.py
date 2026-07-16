"""백테스트/실거래 트레이드 로그를 ML 학습용 데이터셋으로 변환한다."""
from __future__ import annotations

import numpy as np
import pandas as pd

from trading.backtest.engine import BacktestResult
from trading.backtest.report import trades_to_dataframe
from trading.scoring.features import FEATURE_NAMES


def build_dataset(result: BacktestResult) -> pd.DataFrame:
    """각 행 = 트레이드 1건. 피처 컬럼(FEATURE_NAMES) + phase + win + pnl_pct."""
    return trades_to_dataframe(result)


def feature_matrix(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(X, y) — X는 피처 배열, y는 승패(1/0) 라벨."""
    available = [f for f in FEATURE_NAMES if f in df.columns]
    X = df[available].to_numpy(dtype=float)
    y = df["win"].to_numpy(dtype=int)
    return X, y
