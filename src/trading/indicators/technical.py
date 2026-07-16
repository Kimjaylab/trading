"""기술적 지표 계산 함수 모음. 일봉/분봉 어느 주기의 OHLCV Series에도 적용 가능하다."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=1).mean()


def ichimoku_baseline(high: pd.Series, low: pd.Series, window: int = 26) -> pd.Series:
    """일목균형표 기준선 = (window기간 최고가 + 최저가) / 2."""
    return (high.rolling(window, min_periods=1).max() + low.rolling(window, min_periods=1).min()) / 2


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1).bfill()
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    return true_range(high, low, close).rolling(window, min_periods=1).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """평균방향지수(ADX). 추세 강도(방향 무관)를 0~100으로 반환한다."""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = true_range(high, low, close)
    atr_val = tr.rolling(window, min_periods=1).mean().replace(0, np.nan)

    plus_di = 100 * pd.Series(plus_dm, index=high.index).rolling(window, min_periods=1).mean() / atr_val
    minus_di = 100 * pd.Series(minus_dm, index=high.index).rolling(window, min_periods=1).mean() / atr_val

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.rolling(window, min_periods=1).mean().fillna(0.0)


def volume_ratio(volume: pd.Series, window: int = 20) -> pd.Series:
    """직전 window 평균 거래량 대비 현재 거래량 배율."""
    avg = volume.shift(1).rolling(window, min_periods=1).mean().replace(0, np.nan)
    return (volume / avg).fillna(1.0)


def candle_strength(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """캔들의 힘: 몸통 크기를 전체 범위로 정규화하고 방향(양봉+/음봉-)을 부여, [-1, 1] 범위."""
    rng = (high - low).replace(0, np.nan)
    body_ratio = ((close - open_) / rng).clip(-1, 1)
    return body_ratio.fillna(0.0)


def rolling_high(series: pd.Series, window: int) -> pd.Series:
    """자기 자신을 제외한 직전 window 구간의 최고값 (돌파 여부 판정용)."""
    return series.shift(1).rolling(window, min_periods=1).max()


def minute_trend_slope(close: pd.Series, window: int = 5) -> pd.Series:
    """최근 window 분봉 종가의 선형회귀 기울기를 종가로 정규화해 추세 방향/강도를 반환."""

    def _slope(y: np.ndarray) -> float:
        if len(y) < 2 or y[-1] == 0:
            return 0.0
        x = np.arange(len(y))
        slope = np.polyfit(x, y, 1)[0]
        return float(slope / y[-1])

    return close.rolling(window, min_periods=2).apply(_slope, raw=True).fillna(0.0)
